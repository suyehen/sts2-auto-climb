"""Offline calibration summary / reviewed profile sweep. No game access or config updates."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from autoslay.paths import ROOT
from autoslay.calibration.store import DIRECTORY
from autoslay.calibration.report import summarize
from autoslay.calibration.tuning import compare_profiles
from autoslay.evaluation.run import RunPolicy

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--directory',type=Path,default=DIRECTORY)
    parser.add_argument('--labels',type=Path)
    parser.add_argument('--profiles',type=Path,default=ROOT/'config/decision/calibration-profiles.json')
    parser.add_argument('--output',type=Path,default=ROOT/'.runtime/logs/calibration-summary.json')
    args=parser.parse_args(); result=summarize(args.directory)
    if args.labels:
        labels=[json.loads(line) for line in args.labels.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        profiles=json.loads(args.profiles.read_text(encoding='utf-8-sig'))
        policy=RunPolicy(**json.loads((ROOT/'config/decision/run.default.json').read_text(encoding='utf-8-sig')))
        result['tuning']=compare_profiles(labels,profiles['profiles'],policy,profiles['game_version'],args.directory)
        result['baseline_weights']=asdict(policy)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(args.output),choice_intents=result['choice_intents'],trials=result['trials'],applied=False)))

if __name__=='__main__': main()
