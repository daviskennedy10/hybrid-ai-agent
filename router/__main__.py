"""Run from the assignment root: hermes-agent/.venv/bin/python -m router."""
import argparse
import json
from pathlib import Path
import sys

from router.core import JsonLog, Policy, Request, Router
from router.hermes_backend import HermesBackend, ROOT


def main():
    parser = argparse.ArgumentParser(description='Stage 2 automatic Hermes routing')
    parser.add_argument('--prompt', help='Text prompt; omitted reads stdin (preferred for private text)')
    parser.add_argument('--private', action='store_true', help='Never send this request/history to cloud')
    parser.add_argument('--history', type=Path, help='JSON list of text user/assistant messages')
    parser.add_argument('--policy', type=Path)
    parser.add_argument('--log', type=Path, default=ROOT / 'router/logs/routing.jsonl')
    parser.add_argument('--simulate-cloud-rate-limit', action='store_true')
    args = parser.parse_args()
    try:
        policy = Policy.load(args.policy)
        request = Request(args.prompt if args.prompt is not None else sys.stdin.read(), args.private,
                          json.loads(args.history.read_text()) if args.history else [])
        result = Router(policy, HermesBackend(policy), JsonLog(args.log)).run(
            request, simulate_cloud_rate_limit=args.simulate_cloud_rate_limit)
    except (ValueError, OSError, TypeError):
        print('Invalid input/configuration or inaccessible routing log.', file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result['success'] else 3


if __name__ == '__main__':
    raise SystemExit(main())
