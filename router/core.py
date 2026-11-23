"""Pure policy and bounded orchestration. No Hermes imports or prompt logging."""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class Policy:
    cloud_model: str = 'gpt-5.6-sol'
    cloud_provider: str = 'openai-codex'
    local_model: str = 'gemma4:e2b'
    local_alias: str = 'gemma-local'
    local_url: str = 'http://127.0.0.1:11434/v1'
    long_context_bytes: int = 24000
    local_input_bytes: int = 48000
    simple_max_bytes: int = 2000
    max_output_tokens: int = 2048
    retries: int = 1
    retry_delay_seconds: float = 0.25
    health_timeout_seconds: float = 2
    attempt_timeout_seconds: float = 120
    allow_complex_rate_limit_fallback: bool = False
    allow_unknown_rate_limit_fallback: bool = True
    private_patterns: tuple = ()
    complex_patterns: tuple = ()
    simple_patterns: tuple = ()

    @classmethod
    def load(cls, path=None):
        data = json.loads(Path(path or Path(__file__).with_name('policy.json')).read_text())
        policy = cls(**data)
        policy.validate()
        return policy

    def validate(self):
        if self.cloud_provider != 'openai-codex':
            raise ValueError('This adapter supports the existing openai-codex cloud provider')
        if any(not isinstance(value, str) or not value.strip() for value in (
            self.cloud_model, self.local_model, self.local_alias, self.local_url
        )):
            raise ValueError('Model, alias, and endpoint must be nonempty strings')
        url = urlparse(self.local_url)
        if url.scheme != 'http' or url.hostname not in {'127.0.0.1', '::1', 'localhost'} or url.username or url.password:
            raise ValueError('Local inference must use an HTTP loopback URL without credentials')
        # Bytes upper-bound text tokens; reserve >17k tokens for Hermes scaffolding/output.
        if not 0 < self.simple_max_bytes < self.long_context_bytes <= self.local_input_bytes <= 48000:
            raise ValueError('Invalid context budgets')
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= 8192:
            raise ValueError('max_output_tokens must be between 1 and 8192')
        if type(self.retries) is not int or not 0 <= self.retries <= 3:
            raise ValueError('retries must be between 0 and 3')
        for value in (self.retry_delay_seconds, self.health_timeout_seconds, self.attempt_timeout_seconds):
            if not 0 < value <= 600:
                raise ValueError('Timeouts/delay must be positive and bounded')
        for pattern in (*self.private_patterns, *self.complex_patterns, *self.simple_patterns):
            re.compile(pattern)


@dataclass(frozen=True)
class Request:
    prompt: str
    private: bool = False
    history: list = field(default_factory=list)
    principal_id: int | None = field(default=None, repr=False)

    def __post_init__(self):
        if self.principal_id is not None and (type(self.principal_id) is not int or self.principal_id <= 0):
            raise ValueError('Invalid principal')
        if type(self.private) is not bool:
            raise ValueError('private must be a boolean')
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError('A nonempty text prompt is required')
        if not isinstance(self.history, list) or any(
            not isinstance(m, dict) or set(m) != {'role', 'content'}
            or m['role'] not in {'user', 'assistant'} or not isinstance(m['content'], str)
            for m in self.history
        ):
            raise ValueError('History must contain text-only user/assistant messages')

    @property
    def text(self):
        return '\n'.join([*(m['content'] for m in self.history), self.prompt])


@dataclass(frozen=True)
class Decision:
    route: str
    reason: str
    private: bool
    local_eligible: bool


def decide(request, policy):
    text = request.text
    size = len(text.encode('utf-8'))
    matches = lambda patterns: any(re.search(p, text, re.I | re.S) for p in patterns)
    private = request.private or matches(policy.private_patterns)
    if private:
        fits = size <= policy.local_input_bytes
        return Decision('local' if fits else 'held', 'privacy' if fits else 'private_context_exceeds_budget', True, fits)
    if size >= policy.long_context_bytes:
        return Decision('cloud', 'long_context', False, False)
    if matches(policy.complex_patterns):
        return Decision('cloud', 'complex_reasoning', False, policy.allow_complex_rate_limit_fallback)
    if size <= policy.simple_max_bytes and matches(policy.simple_patterns):
        return Decision('local', 'simple_task', False, True)
    return Decision('cloud', 'conservative_default', False, policy.allow_unknown_rate_limit_fallback)


FAILURES = {'unavailable', 'timeout', 'rate_limit', 'server_error', 'auth', 'context_overflow',
            'invalid_request', 'model_not_found', 'unknown', 'worker_error', 'incomplete'}
RETRYABLE = {'unavailable', 'timeout', 'rate_limit', 'server_error'}


@dataclass(frozen=True)
class InferenceResult:
    response: str
    execution: dict


class InferenceFailure(Exception):
    def __init__(self, kind):
        self.kind = kind if kind in FAILURES else 'unknown'
        super().__init__(self.kind)


class JsonLog:
    def __init__(self, path):
        self.path = Path(path)

    def __call__(self, event):
        from operations.security import append_json
        append_json(self.path,event)


class Router:
    def __init__(self, policy, backend, log, sleep=time.sleep, skill_dispatcher=None, memory_store=None, activity=None):
        policy.validate()
        self.policy, self.backend, self.log, self.sleep = policy, backend, log, sleep
        self.skill_dispatcher = skill_dispatcher
        self.memory_store, self.activity = memory_store, activity

    def run(self, request, *, simulate_cloud_rate_limit=False):
        started = time.monotonic()
        if self.memory_store is not None and self.memory_store.handles(request.prompt):
            from agent_memory.store import MemoryFault
            try:
                response,private,execution=self.memory_store.handle(request)
                success,failure=True,None
            except (MemoryFault,ValueError,UnicodeError,OSError):
                response='Memory request rejected. Use synthetic supported preferences only; /memory shows stored facts. Example: /remember project = Project Cardinal.'
                private,execution,success,failure=True,{'operation':'read','revision':0,'record_count':0},False,'invalid_request'
            event={'timestamp':datetime.now(timezone.utc).isoformat(),'request_id':uuid.uuid4().hex,
                   'message':'hybrid_router_memory','route':'memory','selected_model':None,'reason':'memory_request',
                   'private':private,'success':success,'status':'success' if success else 'failure','failure':failure,
                   'latency_ms':round((time.monotonic()-started)*1000,2),'memory_execution':execution,'attempts':[]}
            self.log(event)
            if self.activity and success:
                self.activity.record(request.principal_id,'memory_write' if execution['operation'] in {'write','forget'} else 'memory_read',event['request_id'])
            return {**event,'response':response}
        if self.skill_dispatcher is not None:
            selected = self.skill_dispatcher.plan(request.prompt)
            if selected is not None:
                result = self.skill_dispatcher.execute(request, selected)
                event = {
                    "timestamp": datetime.now(timezone.utc).isoformat(), "request_id": uuid.uuid4().hex,
                    "message": "hybrid_router_skill", "severity": "INFO" if result["success"] else "WARNING",
                    "initial_route": "tool", "route": "tool", "selected_model": None, "reason": "skill_request",
                    "status": result["status"], "success": result["success"], "failure": result["failure"],
                    "private": result["private"], "latency_ms": round((time.monotonic()-started)*1000,2),
                    "fallback": None, "attempts": [], "skill_execution": result["skill_execution"]}
                self.log(event)
                if self.activity:
                    category=('tool_success' if result['success'] else 'tool_failure') if result['skill_execution'].get('tool_invoked') else 'tool_rejected'
                    self.activity.record(request.principal_id,category,event['request_id'])
                return {**event, "response": result["response"], "skill_data": result["skill_data"]}
        memory_execution=None
        if self.memory_store is not None:
            context,private,memory_execution=self.memory_store.context(request.principal_id)
            if context:
                request=Request(request.prompt,request.private or private,
                                [{'role':'assistant','content':context}]+request.history,principal_id=request.principal_id)
        decision = decide(request, self.policy)
        route = decision.route
        attempts = []
        fallback = None
        error = 'context_overflow' if route == 'held' else None
        answer = None
        if route == 'local' and not self._healthy():
            error = 'unavailable'
            if decision.private:
                route = 'held'
            else:
                fallback, route = 'local_unavailable_to_cloud', 'cloud'
        if route != 'held':
            answer, error = self._attempt(route, request, attempts, simulate_cloud_rate_limit)
            if error and route == 'local' and not decision.private and error in RETRYABLE and fallback is None:
                fallback, route = 'local_failure_to_cloud', 'cloud'
                answer, error = self._attempt(route, request, attempts, simulate_cloud_rate_limit)
            elif error == 'rate_limit' and route == 'cloud' and decision.local_eligible and fallback is None:
                if self._healthy():
                    fallback, route = 'cloud_rate_limit_to_local', 'local'
                    answer, error = self._attempt(route, request, attempts, False)
                else:
                    fallback = 'cloud_rate_limit_local_unavailable'
            elif error == 'rate_limit' and route == 'cloud' and fallback is None:
                fallback = 'cloud_rate_limit_local_ineligible'
            if error and decision.private:
                route = 'held'
        status = 'held' if route == 'held' else ('failure' if error else 'success')
        event = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'severity': 'INFO' if status == 'success' else 'WARNING',
            'message': 'hybrid_router_request', 'request_id': uuid.uuid4().hex,
            'initial_route': decision.route, 'route': route,
            'selected_model': self._model(route), 'reason': decision.reason,
            'status': status, 'success': status == 'success', 'failure': error,
            'latency_ms': round((time.monotonic() - started) * 1000, 2),
            'fallback': fallback, 'private': decision.private,
            'simulated_cloud_rate_limit': bool(simulate_cloud_rate_limit), 'attempts': attempts,
        }
        if memory_execution is not None: event['memory_execution']={**memory_execution,'operation':'context'}
        self.log(event)
        if self.activity: self.activity.record(request.principal_id,'model_success' if status=='success' else 'model_failure',event['request_id'])
        return {**event, 'response': answer}

    def handles_skill(self, text):
        return (self.memory_store is not None and self.memory_store.handles(text)) or (self.skill_dispatcher is not None and self.skill_dispatcher.plan(text) is not None)

    def _healthy(self):
        try:
            return bool(self.backend.healthy())
        except Exception:
            return False

    def _model(self, route):
        return {'local': self.policy.local_model, 'cloud': self.policy.cloud_model}.get(route)

    def _attempt(self, route, request, attempts, simulated):
        for number in range(self.policy.retries + 1):
            started = time.monotonic()
            error = None
            answer = None
            execution = None
            try:
                if simulated and route == 'cloud':
                    raise InferenceFailure('rate_limit')
                answer = self.backend.infer(route, request)
                if isinstance(answer, InferenceResult):
                    execution, answer = answer.execution, answer.response
                if not isinstance(answer, str) or not answer.strip():
                    raise InferenceFailure('incomplete')
            except InferenceFailure as exc:
                error = exc.kind
            except Exception:
                # Never serialize an exception: providers may echo prompts/headers in it.
                error = 'worker_error'
            attempts.append({'route': route, 'model': self._model(route), 'attempt': number + 1,
                             'success': error is None, 'failure': error,
                             'latency_ms': round((time.monotonic() - started) * 1000, 2),
                             'execution': execution})
            if error not in RETRYABLE or number == self.policy.retries:
                return answer, error
            self.sleep(self.policy.retry_delay_seconds)
