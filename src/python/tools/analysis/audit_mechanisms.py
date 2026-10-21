"""Write complete mechanism requirements and executable rule classifications."""
import argparse
import json
from pathlib import Path
from autoslay.knowledge_base.repository import scoped, read_json, dataset_path
from autoslay.knowledge_base.mechanism_audit import build_inventory


@scoped
def build():
    return build_inventory(read_json(dataset_path('source_inventory')),read_json(dataset_path('behavior')))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    summary = {k: v for k, v in report.items() if k not in {'rows', 'requirements'}}
    args.output.with_name(args.output.stem + '-summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=True))
