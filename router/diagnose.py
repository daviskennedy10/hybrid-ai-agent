"""Read-only connectivity diagnostics, with no OAuth access or model inference."""
import json
import socket
from datetime import datetime, timezone
from urllib.request import ProxyHandler, build_opener

from router.core import Policy


def probe(host, port, timeout=4):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {'host': host, 'port': port, 'connected': True}
    except OSError as exc:
        # gaierror codes belong to the DNS namespace, not errno.errorcode.
        return {'host': host, 'port': port, 'connected': False,
                'error_type': type(exc).__name__, 'code': exc.errno,
                'code_namespace': 'getaddrinfo' if isinstance(exc, socket.gaierror) else 'errno'}


def diagnose():
    policy = Policy.load()
    from urllib.parse import urlparse
    endpoint = urlparse(policy.local_url)
    local = probe(endpoint.hostname, endpoint.port or 80)
    cloud = probe('chatgpt.com', 443)
    models = []
    error = None
    try:
        with build_opener(ProxyHandler({})).open(policy.local_url.rstrip('/') + '/models',
                                                timeout=policy.health_timeout_seconds) as response:
            models = [m['id'] for m in json.load(response)['data'] if m.get('id') == policy.local_model]
    except Exception as exc:
        error = type(exc).__name__
    return {'timestamp': datetime.now(timezone.utc).isoformat(), 'local_tcp': local, 'cloud_tcp': cloud,
            'expected_ollama_model_present': policy.local_model in models,
            'ollama_http_error_type': error, 'model_inference_attempted': False}


if __name__ == '__main__':
    print(json.dumps(diagnose(), indent=2))
