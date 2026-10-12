"""Offline what-if timing analysis; supplied future costs must carry explicit evidence."""
import argparse
import json
from pathlib import Path
from autoslay.decision.normalize import normalize
from autoslay.evaluation.relic_timing import DelayCost, OpeningContext, TimingPolicy, evaluate_timing
from autoslay.paths import ROOT


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--field", choices=("before", "after"), default="before")
    parser.add_argument("--opening-draw", type=int, default=5)
    parser.add_argument("--opening-hand", type=int, default=0)
    parser.add_argument("--later-draw", type=int, default=5)
    parser.add_argument("--later-hand", type=int, default=0)
    parser.add_argument("--costs", type=Path, help="JSON list of total HP/resource costs until combat completion, including delay=0")
    parser.add_argument("--policy", type=Path, default=ROOT / "config/decision/relic-timing.default.json")
    parser.add_argument("--output", type=Path, default=ROOT / ".runtime/logs/relic-timing-analysis.json")
    args = parser.parse_args()
    source = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
    snapshot = source if "state" in source else source[args.field]
    state = normalize(snapshot)
    context = OpeningContext(args.opening_draw, args.later_draw, args.opening_hand, args.later_hand,
                             basis="explicit CLI what-if context; verify actual draw modifiers and retained cards")
    costs = tuple(DelayCost(**row) for row in json.loads(args.costs.read_text(encoding="utf-8-sig"))) if args.costs else ()
    policy = TimingPolicy(**json.loads(args.policy.read_text(encoding="utf-8-sig")))
    report = evaluate_timing(state, context=context, policy=policy, costs=costs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report_path": str(args.output), "enabled": report["enabled"],
                      "recommendation": report.get("recommendation"), "proposed_delay": report.get("proposed_delay")}, ensure_ascii=True))
