"""Summarize measured service/action stages; never attribute unmeasured time to the model."""
import json
from statistics import median
from autoslay.paths import ROOT
groups={}
for name in ('action-timings.jsonl','service-timings.jsonl'):
    path=ROOT/'.runtime/logs'/name
    if not path.exists(): continue
    for line in path.read_text(encoding='utf-8').splitlines():
        try: record=json.loads(line)
        except ValueError: continue
        if name=='service-timings.jsonl' and not (record.get('measurement_scope')=='resident_request' and record.get('bridge_connected')):
            continue  # Exclude health checks, disconnected reads and old unclassified/test records.
        for stage,value in record.get('timings_ms',{}).items():
            if isinstance(value,(int,float)): groups.setdefault(stage,[]).append(value)
summary={stage:dict(samples=len(v),p50_ms=median(v),p95_ms=sorted(v)[max(0,__import__('math').ceil(.95*len(v))-1)]) for stage,v in groups.items()}
result=dict(stages=summary,unmeasured=['Codex reasoning and model/network/tool orchestration','animation vs backend within action settlement'])
(ROOT/'.runtime/logs/timing-summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result))
