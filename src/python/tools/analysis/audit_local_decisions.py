"""Replay public archived combat snapshots; never request AI or dispatch actions."""
import argparse
from collections import Counter
import json
from datetime import datetime, timezone
from pathlib import Path
from autoslay.decision.cache import digest
from autoslay.configuration import decision_config
from autoslay.paths import ROOT
from autoslay.archive.repository import Repository, atomic
from autoslay.decision.deterministic import assess, operations, ADMIN
from autoslay.decision.local_combat import select
from autoslay.decision.normalize import normalize
from autoslay.decision.unified import analyze_decision
from autoslay.knowledge_base.repository import knowledge_scope
from autoslay.decision.coverage import diagnose


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('run_id')
    parser.add_argument('--output',type=Path,required=True,help='New versioned output; existing files are never overwritten')
    parser.add_argument('--manifest',type=Path,help='Replay exactly the event IDs in an earlier audit')
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Audit output already exists')
    repo=Repository();entries=repo.entries(args.run_id,limit=10000,skip_heartbeat=True)
    if len(entries)>=10000:raise ValueError('Archive listing truncated; refusing an incomplete rate audit')
    entries=list({e['event_id']:e for e in entries}.values())
    if args.manifest:
        manifest=json.loads(args.manifest.read_text(encoding='utf-8-sig'))
        if manifest['run_id']!=args.run_id:raise ValueError('Manifest run differs')
        ids=set(manifest['event_ids']);entries=[e for e in entries if e['event_id'] in ids]
        if {e['event_id'] for e in entries}!=ids:raise ValueError('Manifest events missing')
    chosen={};old_blockers=Counter();rows=[];choices=[]
    for e in entries:
        if e['kind'] in {'local_choice','ai_choice'} and (e.get('location') or {}).get('phase')=='combat':
            chosen[(e.get('details') or {}).get('analysis_id')]=e['kind']
            choices.append(e)
    with knowledge_scope() as knowledge:
        for e in entries:
            if e['kind']!='decision' or (e.get('location') or {}).get('phase')!='combat':continue
            decision=repo.load_event(e['event_id']);snapshot=decision['snapshot']
            old_blockers.update((decision['report'].get('autonomy') or {}).get('reasons') or [])
            fast=assess({},snapshot);proof=fast if fast['eligible'] else None;blockers=[];report=None
            if not proof and fast['mode']!='wait':
                try:
                    model=normalize(snapshot)
                    if model.warnings:blockers=list(model.warnings)
                    else:
                        report=analyze_decision(snapshot,use_cache=False)
                        proof,blockers=select(report,snapshot)
                except (ValueError,KeyError,TypeError) as exc:
                    blockers=['replay_unavailable:'+type(exc).__name__]
            legal=operations([a for a in snapshot['state'].get('legal_actions') or [] if a.get('type') not in ADMIN|{'discard_potion'}])
            rows.append(dict(decision_id=e['event_id'],location=e['location'],original_choice=chosen.get(e['event_id']),
                             forced=len(legal)==1,replay_mode='local' if proof else 'wait' if fast['mode']=='wait' else 'unavailable' if any(b.startswith('replay_unavailable:') for b in blockers) else 'ai_required',
                             replay_eligible=bool(proof),rule_id=proof.get('rule_id') if proof else None,
                             selected_steps=(proof.get('steps') or proof.get('selected_steps')) if proof else None,blockers=blockers))
            if not proof and fast['mode']!='wait':
                rows[-1]['diagnostics']=diagnose(snapshot,report,blockers[0] if blockers else None)
            if len(rows)%10==0:print(json.dumps(dict(progress=len(rows)),ensure_ascii=False),flush=True)
        revision=knowledge.revision
    by_analysis={row['decision_id']:row for row in rows}
    actual_local=sum(e['kind']=='local_choice' for e in choices);actual_ai=sum(e['kind']=='ai_choice' for e in choices)
    additional_ids=[e['event_id'] for e in choices if e['kind']=='ai_choice' and by_analysis.get((e.get('details') or {}).get('analysis_id'),{}).get('replay_eligible')]
    additional=len(additional_ids)
    active=[e for e in choices if (e.get('details') or {}).get('analysis_id') in by_analysis and not by_analysis[e['details']['analysis_id']]['forced']]
    active_local=sum(e['kind']=='local_choice' for e in active)
    total=actual_local+actual_ai
    policies={name:decision_config(ROOT/('config/decision/'+name+'.default.json')) for name in ('combat','analysis','autonomy')}
    payload=dict(schema_version='local-combat-replay/1.1',run_id=args.run_id,knowledge_revision=revision,
                 cutoff_utc=datetime.now(timezone.utc).isoformat(),event_ids=[e['event_id'] for e in entries],
                 policy_fingerprint=digest(policies),policies=policies,
                 code_fingerprint=digest({str(p.relative_to(ROOT)):p.read_text(encoding='utf-8-sig') for directory in ('decision','planning','rules','evaluation') for p in (ROOT/'src/python/src/autoslay'/directory).glob('*.py')}),
                 choice_event_ids=[e['event_id'] for e in choices],additionally_eligible_choice_ids=additional_ids,
                 recorded_active_local_choices=active_local,recorded_active_choices=len(active),
                 recorded_active_local_rate=active_local/len(active) if active else None,
                 choices_without_analysis=sum((e.get('details') or {}).get('analysis_id') not in by_analysis for e in choices),
                 combat_analysis_count=len(rows),recorded_local_choices=actual_local,recorded_ai_choices=actual_ai,
                 recorded_local_rate=actual_local/total if total else None,
                 additionally_eligible_ai_choices=additional,
                 fixed_snapshot_opportunity_rate=(actual_local+additional)/total if total else None,
                 original_blocker_counts=dict(old_blockers),replay_blocker_counts=dict(Counter(b for row in rows for b in row['blockers'])),
                 decisions=rows,limits=['Public archived snapshots reanalyzed with current local rules.',
                                       'No original logs changed, AI requests made or game actions dispatched.',
                                       'Opportunity rate on fixed snapshots is not a measured live rate or a counterfactual game trajectory.'])
    target=args.output;atomic(target,payload)
    print(json.dumps(dict(path=str(target),**{k:payload[k] for k in ('combat_analysis_count','recorded_local_choices','recorded_ai_choices','additionally_eligible_ai_choices','fixed_snapshot_opportunity_rate')}),ensure_ascii=False),flush=True)


if __name__=='__main__':main()
