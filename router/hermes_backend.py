"""Process adapter: every real inference goes through Hermes AIAgent."""
import json
import os
import re
from urllib.parse import urlparse
from pathlib import Path
import subprocess
from urllib.request import ProxyHandler, build_opener

from router.core import InferenceFailure, InferenceResult

ROOT = Path(__file__).resolve().parents[1]


class HermesBackend:
    def __init__(self, policy, hermes_root=None):
        self.policy = policy
        self.hermes_root = Path(hermes_root or ROOT / 'hermes-agent').resolve()

    def healthy(self):
        # Ignore ambient HTTP proxies; a local prompt must stay on loopback.
        try:
            opener = build_opener(ProxyHandler({}))
            with opener.open(self.policy.local_url.rstrip('/') + '/models',
                             timeout=self.policy.health_timeout_seconds) as response:
                data = json.load(response)
            return any(m.get('id') == self.policy.local_model for m in data.get('data', []))
        except Exception:
            return False

    def infer(self, route, request):
        payload = {'route': route, 'prompt': request.prompt, 'history': request.history,
                   'policy': self.policy.__dict__, 'hermes_root': str(self.hermes_root)}
        from operations.security import worker_environment
        env = worker_environment(os.environ)
        try:
            process = subprocess.run(
                [str(self.hermes_root / '.venv/bin/python'), '-m', 'router.hermes_worker'],
                input=json.dumps(payload), text=True, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, timeout=self.policy.attempt_timeout_seconds,
                cwd=ROOT, env=env,
            )
        except subprocess.TimeoutExpired:
            raise InferenceFailure('timeout') from None
        except OSError:
            raise InferenceFailure('worker_error') from None
        try:
            result = json.loads(process.stdout)
        except (ValueError, TypeError):
            raise InferenceFailure('worker_error') from None
        if process.returncode or not result.get('ok'):
            raise InferenceFailure(result.get('failure', 'worker_error'))
        expected_model = self.policy.local_model if route == 'local' else self.policy.cloud_model
        expected_provider = 'custom' if route == 'local' else self.policy.cloud_provider
        if (result.get('model'), result.get('provider')) != (expected_model, expected_provider):
            raise InferenceFailure('worker_error')
        execution = result.get('execution')
        expected_host = urlparse(self.policy.local_url).hostname if route == 'local' else 'chatgpt.com'
        expected_mode = 'chat_completions' if route == 'local' else 'codex_responses'
        if not isinstance(execution, dict) or any(execution.get(key) != value for key, value in {
            'provider': expected_provider, 'wire_model': expected_model,
            'endpoint_host': expected_host, 'api_mode': expected_mode,
        }.items()):
            raise InferenceFailure('worker_error')
        # Only allow model identifiers from the worker; never forward arbitrary metadata.
        normalized = execution.get('normalized_response_model')
        if normalized is not None and (not isinstance(normalized, str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}', normalized)):
            raise InferenceFailure('worker_error')
        safe_execution = {key: execution[key] for key in ('provider', 'wire_model', 'endpoint_host', 'api_mode')}
        safe_execution['normalized_response_model'] = normalized
        return InferenceResult(result['response'], safe_execution)
