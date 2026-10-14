"""Replay public archived combat snapshots; never request AI or dispatch actions."""
import argparse
from collections import Counter
import json
from autoslay.archive.repository import Repository, atomic
from autoslay.decision.deterministic import assess
from autoslay.decision.local_combat import select
from autoslay.decision.normalize import normalize
from autoslay.decision.unified import analyze_decision
from autoslay.knowledge_base.repository import knowledge_scope


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run_id');args=parser.parse_args()
    repo=Repository();entries=repo.entries(args.run_id,limit=10000,skip_heartbeat=True)
    if len(entries)>=10000:raise ValueError('Archive listing truncated; refusing an incomplete rate audit')
    chosen={};old_blockers=Counter();rows=[]
    for e in entries:
        if e['kind'] in {'local_choice','ai_choice'} and (e.get('location') or {}).get('phase')=='combat':
            chosen[(e.get('details') or {}).get('analysis_id')]=e['kind']
    with knowledge_scope() as knowledge:
        for e in entries:
            if e['kind']!='decision' or (e.get('location') or {}).get('phase')!='combat':continue
            decision=repo.load_event(e['event_id']);snapshot=decision['snapshot']
            old_blockers.update((decision['report'].get('autonomy') or {}).get('reasons') or [])
            fast=assess({},snapshot);proof=fast if fast['eligible'] else None;blockers=[]
            if not proof and fast['mode']!='wait':
                try:
                    model=normalize(snapshot)
                    if model.warnings:blockers=list(model.warnings)
                    else:
                        report=analyze_decision(snapshot)
                        proof,blockers=select(report,snapshot)
                except (ValueError,KeyError,TypeError) as exc:
                    blockers=['replay_unavailable:'+type(exc).__name__]
            rows.append(dict(decision_id=e['event_id'],location=e['location'],original_choice=chosen.get(e['event_id']),
                             replay_eligible=bool(proof),rule_id=proof.get('rule_id') if proof else None,
                             selected_steps=(proof.get('steps') or proof.get('selected_steps')) if proof else None,blockers=blockers))
            if len(rows)%10==0:print(json.dumps(dict(progress=len(rows)),ensure_ascii=False),flush=True)
        revision=knowledge.revision
    actual_local=sum(kind=='local_choice' for kind in chosen.values());actual_ai=sum(kind=='ai_choice' for kind in chosen.values())
    additional=sum(row['original_choice']=='ai_choice' and row['replay_eligible'] for row in rows)
    total=actual_local+actual_ai
    payload=dict(schema_version='local-combat-replay/1.0',run_id=args.run_id,knowledge_revision=revision,
                 combat_analysis_count=len(rows),recorded_local_choices=actual_local,recorded_ai_choices=actual_ai,
                 recorded_local_rate=actual_local/total if total else None,
                 additionally_eligible_ai_choices=additional,
                 fixed_snapshot_opportunity_rate=(actual_local+additional)/total if total else None,
                 original_blocker_counts=dict(old_blockers),replay_blocker_counts=dict(Counter(b for row in rows for b in row['blockers'])),
                 decisions=rows,limits=['Public archived snapshots reanalyzed with current local rules.',
                                       'No original logs changed, AI requests made or game actions dispatched.',
                                       'Opportunity rate on fixed snapshots is not a measured live rate or a counterfactual game trajectory.'])
    target=repo.folder(args.run_id)/'provenance'/'local-combat-replay-2026-10-14.json';atomic(target,payload)
    print(json.dumps(dict(path=str(target),**{k:v for k,v in payload.items() if k not in {'decisions','original_blocker_counts','replay_blocker_counts'}}),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
