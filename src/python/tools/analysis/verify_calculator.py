"""Replay selected archived actions only; never connects to the game."""
import json
from autoslay.decision.contracts import Operation
from autoslay.decision.normalize import normalize
from autoslay.simulation.engine import CombatSimulator
from autoslay.telemetry.verification import compare_transition
from autoslay.paths import ROOT

CASES = ["codex-47f41415dc344acc96456d0f2e19b5d4", "codex-31c4b218220f4ad4aa560895dfac71d7",
         "codex-c113afe69e134484ba139a1065a44a9d", "codex-66726cff857b4d1182bf372ff56520ec",
         "codex-aea44f8e9f3648f291916c457215a920"]


def main():
    cases = []
    for name in CASES:
        data = json.loads((ROOT / ".runtime/logs" / (name + ".json")).read_text(encoding="utf-8"))
        initial = normalize(data["before"])
        request = data["request"]
        predicted = CombatSimulator(initial).apply(initial, Operation(request["action_id"], request.get("target_id")))
        comparison = compare_transition(predicted, data["after"])
        cases.append(dict(evidence=name, **comparison))
    report = dict(all_numeric_fields_match=all(c["numeric_fields_match"] for c in cases), cases=cases)
    path = ROOT / ".runtime/logs/calculator-archive-verification.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(dict(report_path=str(path), all_numeric_fields_match=report["all_numeric_fields_match"], cases=len(cases))))
    if not report["all_numeric_fields_match"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
