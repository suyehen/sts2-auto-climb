"""Convert the candidate explicitly selected by Codex into a guarded plan; no gameplay."""
import argparse
import json
import uuid
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--candidate", type=int, required=True, help="Zero-based index explicitly selected by Codex")
    parser.add_argument("--group", choices=["candidates", "observation_boundaries", "wait_candidates"], default="candidates")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8-sig"))
    if 'combat' in report: report=report['combat']
    candidates = report[args.group]
    if not 0 <= args.candidate < len(candidates) or not candidates[args.candidate]["steps"]:
        parser.error("Select a nonempty candidate from this report")
    source = report["source"]
    plan = dict(plan_id="codex-"+uuid.uuid4().hex, session_epoch=source["session_epoch"],
                run_id=source["run_id"], expected_state_version=source["state_version"],
                steps=candidates[args.candidate]["steps"])
    args.output.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps(dict(plan_file=str(args.output), steps=len(plan["steps"]))))

if __name__ == "__main__":
    main()
