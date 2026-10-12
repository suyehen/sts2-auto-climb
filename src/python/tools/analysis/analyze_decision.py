"""Offline unified decision report from a supplied player-visible snapshot."""
import argparse
import json
from pathlib import Path
from autoslay.decision.unified import analyze_decision
from autoslay.paths import ROOT

parser=argparse.ArgumentParser()
parser.add_argument('snapshot',type=Path)
parser.add_argument('--context',type=Path)
parser.add_argument('--scenarios',type=Path)
parser.add_argument('--output',type=Path,default=ROOT/'.runtime/logs/unified-analysis.json')
args=parser.parse_args()
load=lambda p:json.loads(p.read_text(encoding='utf-8-sig')) if p else None
snapshot=load(args.snapshot)
if 'state' not in snapshot: snapshot=snapshot.get('snapshot') or snapshot['before']
report=analyze_decision(snapshot,load(args.context),load(args.scenarios))
args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(dict(output=str(args.output),phase=report['phase'],elapsed_ms=report['request_elapsed_ms'])))
