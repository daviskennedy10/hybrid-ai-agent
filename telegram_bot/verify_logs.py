"""Verify live reply evidence from the most recent bot run, without displaying messages."""
import json
import re
from pathlib import Path

from telegram_bot.config import ROOT


def load(path):
    if not Path(path).exists():
        return []
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def verify(events, routes):
    start = max((i for i, event in enumerate(events) if event.get('status') == 'ready'), default=-1)
    events = events[start + 1:]
    by_request = {event.get('request_id'): event for event in routes if event.get('request_id')}
    verified = []
    for event in events:
        route = by_request.get(event.get('route_request_id'), {})
        attempts = route.get('attempts') or []
        execution = attempts[-1].get('execution') if attempts else None
        if (type(event.get('update_id')) is not int
                or not isinstance(event.get('route_request_id'), str)
                or not re.fullmatch(r'[0-9a-f]{32}', event['route_request_id'])
                or event.get('authorized') is not True or event.get('status') != 'responded'
                or event.get('success') is not True or event.get('model_success') is not True
                or event.get('delivery_success') is not True or route.get('success') is not True
                or not isinstance(execution, dict) or event.get('execution') != execution
                or attempts[-1].get('success') is not True):
            continue
        expected = {
            'local': ('gemma4:e2b', 'custom', '127.0.0.1', 'chat_completions', 'simple_task'),
            'cloud': ('gpt-5.6-sol', 'openai-codex', 'chatgpt.com', 'codex_responses', 'complex_reasoning'),
        }.get(route.get('route'))
        if expected is None:
            continue
        model, provider, host, mode, reason = expected
        if (route.get('selected_model') == event.get('selected_model') == execution.get('wire_model') == model
                and event.get('route') == route.get('route') and route.get('reason') == reason
                and route.get('fallback') is None and execution.get('provider') == provider
                and execution.get('endpoint_host') == host and execution.get('api_mode') == mode):
            verified.append({'update_id': event['update_id'], 'request_id': route['request_id'],
                             'route': route['route'], 'model': model, 'provider': provider,
                             'reply_delivered': True})
    return {'simple_local_reply_verified': any(e['route'] == 'local' for e in verified),
            'complex_cloud_reply_verified': any(e['route'] == 'cloud' for e in verified),
            'verified_replies': verified,
            'rejected_events': sum(e.get('status') == 'rejected' for e in events)}
