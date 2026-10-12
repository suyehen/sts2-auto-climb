"""Explicit local reference refresh; never continues a save or changes control mode."""
import argparse
import asyncio
import json
from autoslay.knowledge.catalog import export_catalog

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true", help="Re-read current unlocked content and language")
    args = parser.parse_args()
    result = asyncio.run(export_catalog(refresh=args.refresh))
    print(json.dumps(result, ensure_ascii=True))
    raise SystemExit(1 if result.get("error") else 0)
