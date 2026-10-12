"""Offline read-only replay: no game connection and no action submission."""
import argparse
import json
from pathlib import Path
from autoslay.decision.service import analyze, load_policy
from autoslay.paths import ROOT
from autoslay.planning.search import SearchBudget


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--field", choices=["before", "after"], default="before")
    parser.add_argument("--max-nodes", type=int, default=2000)
    parser.add_argument("--time-ms", type=int, default=200)
    parser.add_argument("--policy", type=Path, default=ROOT / "config/decision/combat.default.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/logs/decision-analysis.json")
    args = parser.parse_args()
    data = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
    snapshot = data if "state" in data else data[args.field]
    report = analyze(snapshot, budget=SearchBudget(max_nodes=args.max_nodes, time_ms=args.time_ms),
                     policy=load_policy(args.policy), journal_path=ROOT / ".runtime/logs/decision-reports.jsonl")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(report_path=str(args.output), nodes=report["nodes"],
                         timings_ms=report["timings_ms"], search_truncated=report["search_truncated"],
                         candidates=len(report["candidates"]), boundaries=len(report["observation_boundaries"])), ensure_ascii=True))


if __name__ == "__main__":
    main()
