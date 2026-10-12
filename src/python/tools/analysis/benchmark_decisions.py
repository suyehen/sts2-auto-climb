"""Offline fixed-snapshot regression and latency/coverage benchmark; never plays."""
import argparse
import json
from pathlib import Path
from statistics import median
from autoslay.paths import ROOT
from autoslay.decision.unified import analyze_decision
from autoslay.decision.coverage import audit
from autoslay.decision.normalize import normalize
from autoslay.decision.contracts import Operation
from autoslay.simulation.engine import CombatSimulator
from autoslay.telemetry.verification import compare_transition

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--manifest',type=Path,default=ROOT/'data/replays/manifest.json')
    parser.add_argument('--repeats',type=int,default=3)
    parser.add_argument('--output',type=Path,default=ROOT/'.runtime/logs/decision-benchmark.json')
    parser.add_argument('--previous',type=Path)
    args=parser.parse_args()
    if not 1<=args.repeats<=20: parser.error('repeats must be 1..20')
    manifest=json.loads(args.manifest.read_text(encoding='utf-8-sig')); records=[]; snapshots=[]
    for case in manifest['cases']:
        snapshot=json.loads((args.manifest.parent/case['file']).read_text(encoding='utf-8-sig')); snapshots.append(snapshot)
        reports=[analyze_decision(snapshot,use_cache=False) for _ in range(args.repeats)]
        elapsed=sorted(r['request_elapsed_ms'] for r in reports)
        cached=analyze_decision(snapshot); cached=analyze_decision(snapshot)
        report=reports[-1]
        verification=None
        if case.get('action') and case.get('after'):
            predicted=CombatSimulator(normalize(snapshot)).apply(normalize(snapshot),Operation(**case['action']))
            verification=compare_transition(predicted,json.loads((args.manifest.parent/case['after']).read_text(encoding='utf-8-sig')))
        records.append(dict(case=case['id'],p50_ms=median(elapsed),p95_ms=elapsed[max(0,__import__('math').ceil(.95*len(elapsed))-1)],
            cached_ms=cached['request_elapsed_ms'],cache_hit=cached['cache_hit'],
            nodes=(report.get('combat') or {}).get('nodes'),coverage=(report.get('combat') or {}).get('coverage'),
            lookahead=(report.get('combat') or {}).get('lookahead'),verification=verification))
    result=dict(schema_version='benchmark/1.0',repeats=args.repeats,cases=records,coverage=audit(snapshots),
                limits=['latency and numeric prediction checks only; no win-rate claim','no hidden state or replayed gameplay actions'])
    if args.previous:
        previous=json.loads(args.previous.read_text(encoding='utf-8-sig'))
        old={r['case']:r for r in previous.get('cases',[]) } if isinstance(previous,dict) else {r['case']:r for r in previous}
        result['latency_deltas']={r['case']:r['p50_ms']-old[r['case']]['p50_ms'] for r in records if r['case'] in old and 'p50_ms' in old[r['case']]}
    args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(args.output),cases=[{k:r[k] for k in ('case','p50_ms','p95_ms','cached_ms','cache_hit')} for r in records])))
    if any(r['verification'] and not r['verification']['numeric_fields_match'] for r in records):
        raise SystemExit('Replay numeric regression failed; inspect output checks')

if __name__=='__main__': main()
