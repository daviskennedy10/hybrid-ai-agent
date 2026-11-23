import io
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from router.core import InferenceFailure, JsonLog, Policy, Request, Router, decide
from router.hermes_backend import HermesBackend
from router.hermes_worker import failure_kind


class FakeBackend:
    def __init__(self, outcomes=(), healthy=True):
        self.outcomes = iter(outcomes)
        self.available = healthy
        self.calls = []

    def healthy(self):
        return self.available

    def infer(self, route, request):
        self.calls.append(route)
        value = next(self.outcomes, 'answer')
        if isinstance(value, Exception):
            raise value
        return value


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy.load()
        self.events = []

    def run_request(self, prompt='Hello!', backend=None, private=False, policy=None, history=None, **kwargs):
        self.backend = backend or FakeBackend()
        return Router(policy or self.policy, self.backend, self.events.append, sleep=lambda _: None).run(
            Request(prompt, private, history or []), **kwargs)

    def test_rules(self):
        cases = [('Hello!', 'local', 'simple_task'),
                 ('Translate cat into French', 'local', 'simple_task'),
                 ('What is 2 + 2?', 'local', 'simple_task'),
                 ('Prove a theorem', 'cloud', 'complex_reasoning'),
                 ('x' * 24000, 'cloud', 'long_context'),
                 ('Unrecognized work', 'cloud', 'conservative_default'),
                 ('[private] Prove a theorem', 'local', 'privacy')]
        for prompt, route, reason in cases:
            with self.subTest(reason=reason):
                d = decide(Request(prompt), self.policy)
                self.assertEqual((d.route, d.reason), (route, reason))

    def test_long_history_and_utf8(self):
        history = [{'role': 'user', 'content': 'x' * 24000}]
        self.assertEqual(decide(Request('Hello!', history=history), self.policy).reason, 'long_context')
        self.assertEqual(decide(Request('猫' * 8000), self.policy).reason, 'long_context')

    def test_private_history(self):
        result = self.run_request(history=[{'role': 'user', 'content': '[private] synthetic data'}])
        self.assertTrue(result['private'])
        self.assertEqual(self.backend.calls, ['local'])

    def test_private_flag_overrides_long_rule_and_holds_oversize(self):
        self.assertEqual(self.run_request('x' * 24000, private=True)['route'], 'local')
        self.assertEqual(self.run_request('x' * 48001, private=True)['status'], 'held')
        self.assertEqual(self.backend.calls, [])

    def test_local_unavailable_cloud(self):
        result = self.run_request(backend=FakeBackend(healthy=False))
        self.assertEqual(self.backend.calls, ['cloud'])
        self.assertEqual(result['fallback'], 'local_unavailable_to_cloud')
        self.assertTrue(result['success'])

    def test_private_unavailable_held_no_inference(self):
        result = self.run_request(private=True, backend=FakeBackend(healthy=False))
        self.assertEqual(result['status'], 'held')
        self.assertEqual(self.backend.calls, [])

    def test_local_retry_exhaustion_cloud(self):
        result = self.run_request(backend=FakeBackend([InferenceFailure('timeout')] * 2 + ['cloud answer']))
        self.assertEqual(self.backend.calls, ['local', 'local', 'cloud'])
        self.assertEqual(result['fallback'], 'local_failure_to_cloud')

    def test_private_retry_exhaustion_stays_held(self):
        result = self.run_request(private=True, backend=FakeBackend([InferenceFailure('timeout')] * 2))
        self.assertEqual(result['route'], 'held')
        self.assertEqual(self.backend.calls, ['local', 'local'])

    def test_cloud_rate_limit_fallback(self):
        result = self.run_request('Unrecognized work', backend=FakeBackend([InferenceFailure('rate_limit')] * 2))
        self.assertEqual(self.backend.calls, ['cloud', 'cloud', 'local'])
        self.assertEqual(result['fallback'], 'cloud_rate_limit_to_local')
        self.assertTrue(result['success'])

    def test_simulated_rate_limit_real_local_path(self):
        result = self.run_request('Unrecognized work', simulate_cloud_rate_limit=True)
        self.assertEqual(self.backend.calls, ['local'])
        self.assertEqual([a['failure'] for a in result['attempts']], ['rate_limit', 'rate_limit', None])

    def test_no_cloud_to_local_for_long_or_complex_default(self):
        for prompt in ['x' * 24000, 'Prove a theorem']:
            result = self.run_request(prompt, simulate_cloud_rate_limit=True)
            self.assertEqual(result['status'], 'failure')
            self.assertEqual(self.backend.calls, [])

    def test_configurable_complex_fallback(self):
        result = self.run_request('Prove a theorem', simulate_cloud_rate_limit=True,
                                  policy=replace(self.policy, allow_complex_rate_limit_fallback=True))
        self.assertEqual(result['route'], 'local')
        self.assertTrue(result['success'])

    def test_fallback_unavailable_and_no_ping_pong(self):
        result = self.run_request('Unknown', backend=FakeBackend(healthy=False), simulate_cloud_rate_limit=True)
        self.assertEqual(result['fallback'], 'cloud_rate_limit_local_unavailable')
        result = self.run_request(backend=FakeBackend(healthy=False), simulate_cloud_rate_limit=True)
        self.assertEqual(result['fallback'], 'local_unavailable_to_cloud')
        self.assertEqual(result['status'], 'failure')
        self.assertEqual(len(result['attempts']), 2)

    def test_auth_and_unknown_do_not_retry_or_fallback(self):
        for error in [InferenceFailure('auth'), RuntimeError('PROMPT_OR_SECRET')]:
            result = self.run_request(backend=FakeBackend([error]))
            self.assertEqual(len(self.backend.calls), 1)
            self.assertEqual(result['status'], 'failure')
            self.assertNotIn('PROMPT_OR_SECRET', json.dumps(self.events))

    def test_rate_limit_fallback_failure_terminates(self):
        backend = FakeBackend([InferenceFailure('rate_limit')] * 2 + [InferenceFailure('timeout')] * 2)
        result = self.run_request('Unknown', backend=backend)
        self.assertEqual(backend.calls, ['cloud', 'cloud', 'local', 'local'])
        self.assertEqual(result['status'], 'failure')

    def test_log_schema_and_no_content_or_credentials(self):
        secret = 'synthetic-token-NEVER-LOG'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'route.jsonl'
            router = Router(self.policy, FakeBackend([RuntimeError(secret)]), JsonLog(path))
            router.run(Request('[private] ' + secret, history=[{'role': 'user', 'content': secret}]))
            text = path.read_text()
            event = json.loads(text)
            self.assertNotIn(secret, text)
            self.assertNotIn('prompt', text)
            self.assertNotIn('response', event)
            self.assertTrue({'timestamp', 'route', 'selected_model', 'reason', 'success', 'latency_ms',
                             'fallback', 'attempts', 'request_id', 'severity'} <= event.keys())

    def test_health_model_and_timeout(self):
        backend = HermesBackend(self.policy)
        response = io.BytesIO(json.dumps({'data': [{'id': self.policy.local_model}]}).encode())
        with patch('router.hermes_backend.build_opener') as opener:
            opener.return_value.open.return_value = response
            self.assertTrue(backend.healthy())
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'], 2)
        with patch('router.hermes_backend.build_opener', side_effect=TimeoutError):
            self.assertFalse(backend.healthy())

    def test_health_exception_is_held_and_logged_for_private(self):
        backend = FakeBackend()
        backend.healthy = lambda: (_ for _ in ()).throw(RuntimeError('sensitive detail'))
        result = self.run_request(private=True, backend=backend)
        self.assertEqual(result['status'], 'held')
        self.assertEqual(len(self.events), 1)
        self.assertNotIn('sensitive detail', json.dumps(self.events))

    def test_backend_checks_actual_model_and_provider(self):
        backend = HermesBackend(self.policy)
        with patch('router.hermes_backend.subprocess.run') as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps({'ok': True, 'response': 'answer',
                                                  'model': 'wrong-model', 'provider': 'custom'})
            with self.assertRaisesRegex(InferenceFailure, 'worker_error'):
                backend.infer('local', Request('Hello!'))
            run.return_value.stdout = json.dumps({'ok': True, 'response': 'answer',
                                                  'model': self.policy.local_model, 'provider': 'custom',
                                                  'execution': {'provider': 'custom', 'wire_model': self.policy.local_model,
                                                                'endpoint_host': '127.0.0.1', 'api_mode': 'chat_completions',
                                                                'normalized_response_model': self.policy.local_model,
                                                                'raw_prompt': 'NEVER_LOG_THIS'}})
            verified = backend.infer('local', Request('Hello!'))
            self.assertEqual(verified.response, 'answer')
            self.assertNotIn('raw_prompt', verified.execution)
            self.assertNotIn('NEVER_LOG_THIS', json.dumps(verified.execution))
            payload = json.loads(run.return_value.stdout)
            payload['execution']['wire_model'] = 'wrong-wire-model'
            run.return_value.stdout = json.dumps(payload)
            with self.assertRaisesRegex(InferenceFailure, 'worker_error'):
                backend.infer('local', Request('Hello!'))

    def test_worker_timeout_and_malformed_output(self):
        import subprocess
        backend = HermesBackend(self.policy)
        with patch('router.hermes_backend.subprocess.run', side_effect=subprocess.TimeoutExpired('worker', 1)):
            with self.assertRaisesRegex(InferenceFailure, 'timeout'):
                backend.infer('local', Request('Hello!'))
        with patch('router.hermes_backend.subprocess.run') as run:
            run.return_value.stdout = 'unsafe exception with secret'
            with self.assertRaisesRegex(InferenceFailure, 'worker_error'):
                backend.infer('local', Request('Hello!'))

    def test_diagnostics_keep_dns_codes_separate(self):
        import socket
        from router.diagnose import probe
        with patch('router.diagnose.socket.create_connection', side_effect=socket.gaierror(8, 'dns error')):
            result = probe('chatgpt.com', 443)
            self.assertEqual(result['code_namespace'], 'getaddrinfo')
            self.assertEqual(result['error_type'], 'gaierror')
        with patch('router.diagnose.socket.create_connection', side_effect=PermissionError(1, 'denied')):
            result = probe('127.0.0.1', 11434)
            self.assertEqual(result['code_namespace'], 'errno')
            self.assertEqual(result['error_type'], 'PermissionError')

    def test_failure_classification(self):
        for reason, expected in [('auth_permanent', 'auth'), ('rate_limit', 'rate_limit'),
                                 ('overloaded', 'server_error'), ('timeout', 'timeout'),
                                 ('context_overflow', 'context_overflow'), ('new reason', 'unknown')]:
            self.assertEqual(failure_kind(reason), expected)

    def test_configuration_and_input_validation(self):
        for change in [{'local_url': 'https://public.example/v1'}, {'retries': 9},
                       {'local_input_bytes': 65536}, {'attempt_timeout_seconds': 0}]:
            with self.assertRaises(ValueError):
                replace(self.policy, **change).validate()
        for history in [[{'role': 'system', 'content': 'bad'}], [{'role': 'user', 'content': []}]]:
            with self.assertRaises(ValueError):
                Request('hello', history=history)
        with self.assertRaises(ValueError):
            Request('')


if __name__ == '__main__':
    unittest.main()
