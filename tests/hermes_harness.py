"""Test-only subprocess: real Hermes loop, mocked model transport and credentials."""
import contextlib
from dataclasses import asdict
import io
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'hermes-agent')]
logging.disable(logging.CRITICAL)

from router.core import InferenceResult, Policy, Request, Router
from router.hermes_worker import execute


def main():
    payload = json.load(sys.stdin)
    policy = Policy.load()
    observed = []
    events = []
    with tempfile.TemporaryDirectory() as original, tempfile.TemporaryDirectory() as isolated:
        os.environ['HERMES_HOME'] = original
        Path(original, 'config.yaml').write_text(json.dumps({
            '_config_version': 52, 'model_aliases': {policy.local_alias: {
                'model': policy.local_model, 'provider': 'custom', 'base_url': policy.local_url}}}))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            from run_agent import AIAgent
            from openai.types.chat import ChatCompletion

            def transport(agent, kwargs):
                observed.append({'contains_synthetic_preference':'Project Cardinal' in json.dumps(kwargs), 'model': agent.model, 'provider': agent.provider,
                                 'api_mode': agent.api_mode, 'wire_model': kwargs.get('model'), 'tools': len(agent.tools),
                                 'fallbacks': len(agent._fallback_chain),
                                 'api_retries': agent._api_max_retries,
                                 'compression_enabled': agent.compression_enabled})
                if payload.get('transport_auth_failure'):
                    import httpx
                    from openai import AuthenticationError
                    raise AuthenticationError('Synthetic 401', response=httpx.Response(
                        401, request=httpx.Request('POST', 'https://synthetic.invalid')), body=None)
                if payload.get('transport_rate_limit') and agent.provider == 'openai-codex':
                    import httpx
                    from openai import RateLimitError
                    raise RateLimitError('Synthetic 429', response=httpx.Response(
                        429, request=httpx.Request('POST', 'https://synthetic.invalid')), body=None)
                if agent.api_mode == 'codex_responses':
                    return SimpleNamespace(id='synthetic', model=agent.model, status='completed',
                                           output_text='HERMES_TRANSPORT_FIXTURE_OK', output=[],
                                           usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15))
                return ChatCompletion(id='synthetic', created=0, model=agent.model, object='chat.completion',
                                      choices=[{'index': 0, 'finish_reason': 'stop', 'message': {
                                          'role': 'assistant', 'content': 'HERMES_TRANSPORT_FIXTURE_OK'}}],
                                      usage={'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15})

            class Backend:
                def healthy(self):
                    return payload.get('healthy', True)

                def infer(self, route, request):
                    os.environ['HERMES_HOME'] = original
                    from hermes_constants import set_hermes_home_override
                    set_hermes_home_override(original)
                    # Use a new isolated home for each attempt, as production does.
                    with tempfile.TemporaryDirectory(dir=isolated) as home:
                        result = execute({'policy': asdict(policy), 'route': route,
                                          'prompt': request.prompt, 'history': request.history,
                                          'hermes_root': str(ROOT / 'hermes-agent')}, home)
                        os.chdir(ROOT)
                    if not result['ok']:
                        from router.core import InferenceFailure
                        raise InferenceFailure(result['failure'])
                    return InferenceResult(result['response'], result['execution'])

            with patch.object(AIAgent, '_interruptible_api_call', transport), patch(
                'agent.turn_api_call._should_stream', return_value=False
            ), patch('hermes_cli.auth_codex.resolve_codex_runtime_credentials', return_value={
                'provider': 'openai-codex', 'api_key': 'synthetic-test-token',
                'base_url': payload.get('credential_base_url','https://chatgpt.com/backend-api/codex')
            }):
                from agent_memory.store import Store
                memory=Store(payload['memory_root'],{101}) if payload.get('memory_root') else None
                result = Router(policy, Backend(), events.append, sleep=lambda _: None,memory_store=memory).run(
                    Request(payload['prompt'], payload.get('private', False),principal_id=101 if memory else None),
                    simulate_cloud_rate_limit=payload.get('simulate', False))
        print(json.dumps({'result': result, 'observed': observed}))


if __name__ == '__main__':
    main()
