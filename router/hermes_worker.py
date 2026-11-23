"""One disposable Hermes turn. stdin/stdout are a private JSON IPC channel."""
import contextlib
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlparse

from router.core import Policy, Request


def failure_kind(reason):
    # Hermes returns structured failure reasons even when it consumes the SDK exception.
    return {
        'rate_limit': 'rate_limit', 'upstream_rate_limit': 'rate_limit',
        'auth': 'auth', 'auth_permanent': 'auth', 'model_entitlement': 'auth',
        'billing': 'auth', 'timeout': 'timeout', 'connection_error': 'unavailable',
        'overloaded': 'server_error', 'server_error': 'server_error',
        'context_overflow': 'context_overflow', 'payload_too_large': 'context_overflow',
        'format_error': 'invalid_request', 'model_not_found': 'model_not_found',
    }.get(reason, 'unknown')


def execute(payload, home):
    policy = Policy(**payload['policy'])
    policy.validate()
    request = Request(payload['prompt'], history=payload['history'])
    sys.path.insert(0, payload['hermes_root'])
    # Read alias/auth from the existing profile, without changing its settings or tokens.
    if payload['route'] == 'local':
        from hermes_cli.config import load_config
        alias = load_config().get('model_aliases', {}).get(policy.local_alias, {})
        if (alias.get('model'), alias.get('provider'), alias.get('base_url', '').rstrip('/')) != (
            policy.local_model, 'custom', policy.local_url.rstrip('/')
        ):
            return {'ok': False, 'failure': 'invalid_request'}
        runtime = {'provider': 'custom', 'api_key': 'local-no-key',
                   'base_url': policy.local_url, 'api_mode': 'chat_completions'}
        model = policy.local_model
    else:
        if policy.cloud_provider != 'openai-codex':
            return {'ok': False, 'failure': 'invalid_request'}
        from hermes_cli.auth_codex import resolve_codex_runtime_credentials
        credentials = resolve_codex_runtime_credentials(read_only=True)
        endpoint = urlparse(credentials['base_url'])
        if (endpoint.scheme != 'https' or endpoint.hostname != 'chatgpt.com'
                or endpoint.port not in {None,443} or endpoint.username or endpoint.password
                or endpoint.query or endpoint.fragment or endpoint.path.rstrip('/') != '/backend-api/codex'):
            return {'ok': False, 'failure': 'invalid_request'}
        runtime = {key: credentials[key] for key in ('provider', 'api_key', 'base_url')}
        runtime['api_mode'] = 'codex_responses'
        model = policy.cloud_model

    os.environ['HERMES_HOME'] = home
    os.environ['HERMES_RUNTIME_DIR'] = str(Path(home) / 'runtime')
    # Suppress explicit parent profile overrides too.
    from hermes_constants import set_hermes_home_override
    set_hermes_home_override(home)
    config = {
        '_config_version': 52,
        'model': {'default': model, 'provider': runtime['provider'],
                  'base_url': runtime['base_url'],
                  **({'context_length': 65536} if payload['route'] == 'local' else {})},
        'agent': {'api_max_retries': 1, 'auto_recovery_cycles': 0},
        'compression': {'enabled': False},
        'memory': {'memory_enabled': False, 'user_profile_enabled': False},
        'plugins': {'enabled': []},
    }
    # JSON is also YAML. No secrets are written to this temporary config.
    Path(home, 'config.yaml').write_text(json.dumps(config))
    os.chdir(home)
    from run_agent import AIAgent
    class RoutedHermesAgent(AIAgent):
        def _record_router_execution(self, api_kwargs, response):
            self._router_execution = {
                'provider': self.provider,
                'wire_model': api_kwargs.get('model'),
                'endpoint_host': urlparse(self.base_url).hostname,
                'api_mode': self.api_mode,
                # Hermes may synthesize this from request kwargs during streaming.
                # It is deliberately not labelled a provider-reported model ID.
                'normalized_response_model': getattr(response, 'model', None),
            }
            return response

        def _interruptible_api_call(self, api_kwargs):
            response = super()._interruptible_api_call(api_kwargs)
            return self._record_router_execution(api_kwargs, response)

        def _interruptible_streaming_api_call(self, api_kwargs, *, on_first_delta=None):
            response = super()._interruptible_streaming_api_call(api_kwargs, on_first_delta=on_first_delta)
            return self._record_router_execution(api_kwargs, response)

        # Stage 2 borrows an existing access token read-only. Hermes's normal 401
        # recovery can import/refresh OAuth tokens; this adapter must not do so.
        def _try_refresh_codex_client_credentials(self, *, force=True):
            return False

        def _try_refresh_provider_runtime_credentials(self, *, reason):
            return False

        def _try_refresh_env_client_credentials(self):
            return False

    agent = RoutedHermesAgent(
        **runtime, model=model, enabled_toolsets=[], fallback_model=[],
        max_iterations=2, max_tokens=policy.max_output_tokens, quiet_mode=True,
        save_trajectories=False, verbose_logging=False,
        skip_context_files=True, skip_memory=True, skip_background_review=True,
        cwd=home, run_budget_seconds=90,
    )
    try:
        result = agent.run_conversation(
            request.prompt, conversation_history=request.history,
            system_message='You are a helpful assistant. Answer the user directly and concisely.',
        )
        if result.get('completed') and not result.get('failed') and not result.get('error'):
            return {'ok': True, 'response': result.get('final_response', ''),
                    'model': agent.model, 'provider': agent.provider,
                    'execution': getattr(agent, '_router_execution', None)}
        return {'ok': False, 'failure': failure_kind(result.get('failure_reason'))}
    finally:
        agent.close()


def main():
    payload = json.load(sys.stdin)
    logging.disable(logging.CRITICAL)
    # File descriptor redirection also catches library/background-thread output.
    result_fd = os.dup(1)
    with open(os.devnull, 'w') as sink, tempfile.TemporaryDirectory(prefix='stage2-hermes-') as home:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            try:
                result = execute(payload, home)
            except Exception as exc:
                from agent.error_classifier import classify_api_error
                from hermes_cli.auth import AuthError
                try:
                    kind = 'auth' if isinstance(exc, AuthError) else failure_kind(classify_api_error(exc).reason.value)
                except Exception:
                    kind = 'worker_error'
                result = {'ok': False, 'failure': kind}
        with os.fdopen(result_fd, 'w') as output:
            output.write(json.dumps(result))


if __name__ == '__main__':
    main()
