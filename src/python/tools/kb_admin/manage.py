"""Offline knowledge status/search/index rebuild and reviewed experience import."""
import argparse
import json
import sys

from autoslay.knowledge_base.experience import publish
from autoslay.knowledge_base.repository import read_json
from autoslay.knowledge_base.service import SERVICE


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    commands.add_parser('rebuild-index')
    search = commands.add_parser('search')
    search.add_argument('query')
    search.add_argument('--game-version')
    search.add_argument('--assembly-hash')
    search.add_argument('--dataset')
    search.add_argument('--limit', type=int, default=10)
    search.add_argument('--related', action='store_true')
    review = commands.add_parser('publish-experience', help='Replace collection from explicitly reviewed JSON; retains history')
    review.add_argument('dataset', choices=('strategy', 'timing'))
    review.add_argument('path')
    args = parser.parse_args()
    if args.command == 'status':
        result = SERVICE.status()
    elif args.command == 'search':
        result = SERVICE.search(args.query, game_version=args.game_version,
            assembly_hash=args.assembly_hash, dataset=args.dataset, limit=args.limit,
            include_related=args.related)
    elif args.command == 'publish-experience':
        result = publish(read_json(args.path, fresh=True), args.dataset)
    else:
        result = SERVICE.rebuild_index()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
