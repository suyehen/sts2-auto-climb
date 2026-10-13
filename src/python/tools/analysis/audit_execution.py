"""Reconstruct a run's observed Agent effects without running game actions or models."""
import argparse
import json
from collections import Counter
from autoslay.archive.repository import Repository,atomic
from autoslay.telemetry.effects import archived


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_id')
    args=parser.parse_args()
    repo=Repository();entries=repo.entries(args.run_id,limit=10000,skip_heartbeat=True)
    rows=[]
    for entry in entries:
        if entry['kind']!='decision':continue
        decision=repo.load_event(entry['event_id'])
        related=[e for e in entries if (e.get('details') or {}).get('analysis_id')==entry['event_id']]
        plan_ids={value for e in related for key in ('request_id','plan_request_id')
                  if (value:=(e.get('details') or {}).get(key))}
        lifecycle=[e.get('details') for e in entries if e['kind']=='agent_request' and
                   (e.get('details') or {}).get('plan_request_id') in plan_ids]
        effects=archived(repo,decision,entries)
        observed=any(effect.get('status')=='observed' for effect in effects)
        chosen=any(e['kind'] in {'ai_choice','local_choice'} for e in related)
        explicitly_stopped=any(e.get('status')=='not_executed' for e in lifecycle)
        status=('observed' if observed else 'not_executed' if explicitly_stopped else
                'choice_without_confirmed_execution' if chosen else 'analysis_without_execution')
        rows.append(dict(decision_id=entry['event_id'],location=entry['location'],effects=effects,
                         execution_status=status,request_events=lifecycle))
    payload=dict(schema_version='agent-execution-audit/1.0',run_id=args.run_id,
                 basis='Original public before/after snapshots; exact analysis, request, session and run identities',
                 decision_count=len(rows),confirmed_decisions=sum(r['execution_status']=='observed' for r in rows),
                 execution_status_counts=dict(Counter(r['execution_status'] for r in rows)),
                 entries_truncated=len(entries)>=10000,decisions=rows,
                 limits=['Observed action effects are not strategic utility scores.',
                         'No unchosen counterfactuals, hidden random outcomes or missing execution evidence are invented.'])
    target=repo.folder(args.run_id)/'provenance'/'agent-execution-audit.json'
    atomic(target,payload)
    print(json.dumps(dict(path=str(target),decisions=len(rows),confirmed=payload['confirmed_decisions'],entries_truncated=payload['entries_truncated']),ensure_ascii=False))


if __name__=='__main__':main()
