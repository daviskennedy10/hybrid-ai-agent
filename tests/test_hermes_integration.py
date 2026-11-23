import json
import os
from pathlib import Path
import subprocess
import unittest
from dataclasses import replace

from router.core import JsonLog, Policy, Request, Router
from router.hermes_backend import HermesBackend, ROOT


@unittest.skipUnless((ROOT / 'hermes-agent/.venv/bin/python').exists(), 'Hermes checkout/venv required')
class HermesExecutionTests(unittest.TestCase):
    """Offline integration, not a live model result; AIAgent itself is never mocked."""
    def harness(self, **payload):
        run = subprocess.run([str(ROOT / 'hermes-agent/.venv/bin/python'), 'tests/hermes_harness.py'],
                             input=json.dumps(payload), text=True, capture_output=True, timeout=60, cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stderr[-1500:])
        return json.loads(run.stdout)

    def test_automatic_local_inside_real_hermes_loop(self):
        data = self.harness(prompt='Hello!')
        self.assertTrue(data['result']['success'], data)
        self.assertEqual(data['observed'][0]['model'], 'gemma4:e2b')
        self.assertEqual(data['observed'][0]['provider'], 'custom')
        self.assertEqual(data['observed'][0]['wire_model'], 'gemma4:e2b')
        self.assertEqual(data['result']['attempts'][-1]['execution']['wire_model'], 'gemma4:e2b')
        self.assertEqual(data['observed'][0]['tools'], 0)
        self.assertEqual(data['observed'][0]['fallbacks'], 0)
        self.assertEqual(data['observed'][0]['api_retries'], 1)
        self.assertFalse(data['observed'][0]['compression_enabled'])

    def test_automatic_cloud_inside_real_hermes_loop(self):
        data = self.harness(prompt='Prove that 2 + 2 equals 4 briefly.')
        self.assertTrue(data['result']['success'], data)
        self.assertEqual(data['observed'][0]['model'], 'gpt-5.6-sol')
        self.assertEqual(data['observed'][0]['api_mode'], 'codex_responses')
        self.assertEqual(data['observed'][0]['wire_model'], 'gpt-5.6-sol')
        self.assertEqual(data['result']['attempts'][-1]['execution']['endpoint_host'], 'chatgpt.com')

    def test_long_context_inside_real_hermes_loop(self):
        data = self.harness(prompt='Synthetic context. ' * 1500)
        self.assertTrue(data['result']['success'], data)
        self.assertEqual(data['result']['reason'], 'long_context')
        self.assertEqual(data['observed'][0]['wire_model'], 'gpt-5.6-sol')

    def test_private_inside_real_hermes_local_loop(self):
        data = self.harness(prompt='[private] Prove a synthetic claim.', private=True)
        self.assertTrue(data['result']['success'], data)
        self.assertEqual(data['result']['reason'], 'privacy')
        self.assertEqual(data['observed'][0]['wire_model'], 'gemma4:e2b')

    def test_simulated_429_falls_back_to_real_hermes_local_loop(self):
        data = self.harness(prompt='Return a short response.', simulate=True)
        self.assertTrue(data['result']['success'], data)
        self.assertEqual(data['result']['fallback'], 'cloud_rate_limit_to_local')
        self.assertEqual([o['model'] for o in data['observed']], ['gemma4:e2b'])

    def test_private_unavailable_never_enters_hermes(self):
        data = self.harness(prompt='[private] synthetic identifier', healthy=False)
        self.assertEqual(data['result']['status'], 'held')
        self.assertEqual(data['observed'], [])

    def test_auth_failure_is_terminal_without_credential_refresh(self):
        data = self.harness(prompt='Return a short response.', transport_auth_failure=True)
        self.assertFalse(data['result']['success'], data)
        self.assertEqual(data['result']['failure'], 'auth')
        self.assertEqual(len(data['observed']), 1)
        self.assertIsNone(data['result']['fallback'])

    def test_actual_hermes_429_classification_then_local(self):
        data = self.harness(prompt='Return a short response.', transport_rate_limit=True)
        self.assertTrue(data['result']['success'], data)
        self.assertEqual(data['result']['fallback'], 'cloud_rate_limit_to_local')
        self.assertEqual(data['observed'][-1]['model'], 'gemma4:e2b')
        self.assertEqual(data['result']['attempts'][0]['failure'], 'rate_limit')


@unittest.skipUnless(os.environ.get('STAGE2_LIVE') == '1', 'Set STAGE2_LIVE=1 for actual model calls')
class LiveHermesTests(unittest.TestCase):
    def setUp(self):
        self.policy = replace(Policy.load(), retries=0)
        self.backend = HermesBackend(self.policy)
        self.router = Router(self.policy, self.backend, JsonLog(ROOT / 'router/logs/live-tests.jsonl'))

    def test_local(self):
        self.assertTrue(self.backend.healthy(), 'Ollama loopback/model health check failed')
        result = self.router.run(Request('What is 2 + 2? Answer briefly.'))
        self.assertTrue(result['success'], result)
        self.assertEqual(result['route'], 'local')
        self.assertIsNone(result['fallback'])
        self.assert_execution(result, 'custom', 'gemma4:e2b', '127.0.0.1')

    def test_cloud(self):
        result = self.router.run(Request('Prove that the sum of two even integers is even. Be brief.'))
        self.assertTrue(result['success'], result)
        self.assertEqual(result['route'], 'cloud')
        self.assertIsNone(result['fallback'])
        self.assert_execution(result, 'openai-codex', 'gpt-5.6-sol', 'chatgpt.com')

    def test_private_local(self):
        result = self.router.run(Request('Rephrase synthetic private example: the blue cat sits.', private=True))
        self.assertTrue(result['success'], result)
        self.assertEqual(result['route'], 'local')

    def assert_execution(self, result, provider, model, host):
        execution = result['attempts'][-1]['execution']
        self.assertEqual(execution['provider'], provider)
        self.assertEqual(execution['wire_model'], model)
        self.assertEqual(execution['endpoint_host'], host)
        self.assertTrue(result['response'].strip())

    def test_unavailable_local_private_is_held(self):
        policy = replace(self.policy, local_url='http://127.0.0.1:1/v1')
        result = Router(policy, HermesBackend(policy), JsonLog(ROOT / 'router/logs/live-tests.jsonl')).run(
            Request('Rephrase a synthetic private example.', private=True))
        self.assertEqual(result['status'], 'held')
        self.assertEqual(result['attempts'], [])
        self.assertIsNone(result['response'])

    def test_unavailable_local_public_uses_cloud(self):
        policy = replace(self.policy, local_url='http://127.0.0.1:1/v1')
        result = Router(policy, HermesBackend(policy), JsonLog(ROOT / 'router/logs/live-tests.jsonl')).run(
            Request('What is 2 + 2?'))
        self.assertTrue(result['success'], result)
        self.assertEqual(result['fallback'], 'local_unavailable_to_cloud')
        self.assert_execution(result, 'openai-codex', 'gpt-5.6-sol', 'chatgpt.com')

    def test_simulated_rate_limit_real_local_fallback(self):
        result = self.router.run(Request('Return a short greeting.'), simulate_cloud_rate_limit=True)
        self.assertTrue(result['success'], result)
        self.assertEqual(result['fallback'], 'cloud_rate_limit_to_local')
        self.assertEqual(result['route'], 'local')
        self.assert_execution(result, 'custom', 'gemma4:e2b', '127.0.0.1')


if __name__ == '__main__':
    unittest.main()
