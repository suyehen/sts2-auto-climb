"""Status/start/stop/restart local services. Gameplay never starts here."""
import argparse
import json
from autoslay.services.client import ensure_manager, request, operate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'start', 'stop', 'restart'], nargs='?', default='status')
    parser.add_argument('--scope', choices=['agent', 'all'], default='agent')
    args = parser.parse_args()
    try:
        ensure_manager()
        if args.action == 'status':
            result = request('/status')
        else:
            result = operate(args.action, args.scope,
                             progress=lambda job: print(json.dumps(job, ensure_ascii=False), flush=True))
        print(json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        # Never publish HTTP request headers or process command lines.
        print(json.dumps(dict(error=str(exc) if isinstance(exc, ValueError) else 'SERVICE_MANAGER_UNAVAILABLE'), ensure_ascii=False))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
