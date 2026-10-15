"""Read-only per-turn batch recommendations; never creates a provider or submits actions."""
import argparse
import json
from copy import deepcopy
from pathlib import Path
from autoslay.decision.unified import analyze_decision
from autoslay.decision.cache import digest
from autoslay.archive.repository import atomic
from autoslay.paths import ROOT
from autoslay.configuration import decision_config


def audit(folder,include_paused=False):
    folder=folder.resolve()
    events=[json.loads(line) for line in (folder/'timeline.jsonl').read_text(encoding='utf-8-sig').splitlines()]
    def load(row):
        path=(folder/row['path']).resolve()
        if not path.is_relative_to(folder):raise ValueError('Archive path escapes run directory')
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        if data.get('snapshot_ref'):
            path=(folder/data['snapshot_ref']).resolve()
            if not path.is_relative_to(folder):raise ValueError('Snapshot path escapes run directory')
            data['snapshot']=json.loads(path.read_text(encoding='utf-8-sig'))
        return data
    cases={};latest={};choices={}
    for event in events:
        location=event.get('location') or {}
        if location.get('phase')!='combat':continue
        turn=location.get('turn')
        if event['kind'] in {'ai_choice','local_choice'}:choices.setdefault(turn,[]).append(dict(kind=event['kind'],event_id=event['event_id'],details=event.get('details')))
        if event['kind'] not in {'decision','snapshot','heartbeat'}:continue
        data=load(event);snapshot=data.get('snapshot') or {};state=snapshot.get('state') or {}
        paused_example=include_paused and state.get('control_mode')=='paused'
        if (not snapshot.get('ready') and not paused_example) or snapshot.get('busy') or state.get('phase')!='combat':continue
        if (state.get('player') or {}).get('turn_phase') not in {None,'Play'}:continue
        latest[turn]=(event,snapshot)
        if event['kind']=='decision' and turn not in cases:cases[turn]=(event,snapshot)
    cases={**latest,**cases};rows=[]
    for turn,(event,original) in sorted(cases.items(),key=lambda item:item[0] or 0):
        snapshot=deepcopy(original);snapshot['state']['control_mode']='auto'
        copied={'state.control_mode':'auto'}
        if not original.get('ready'):
            # Pausing removes the live action catalogue. This optional case is
            # explicitly illustrative, never evidence of executable legality.
            player=snapshot['state']['player'];actions=[]
            enemies=[e['instance_id'] for e in snapshot['state'].get('enemies') or [] if e.get('hp',0)>0]
            for card in player.get('hand') or []:
                if not 0<=card.get('energy_cost',-1)<=player['energy'] or max(0,card.get('star_cost',-1))>(player.get('stars') or 0):continue
                target=card.get('target_type')
                if target not in {'Self','None','AllEnemies','AnyEnemy'}:continue
                actions.append(dict(action_id='play_'+card['instance_id'],type='play_card',
                    allows_no_target=target!='AnyEnemy',allowed_target_ids=enemies if target=='AnyEnemy' else []))
            actions.append(dict(action_id='end_turn',type='end_turn',allows_no_target=True,allowed_target_ids=[]))
            snapshot['state']['legal_actions']=actions;snapshot['ready']=True
            copied.update({'snapshot.ready':True,'state.legal_actions':'synthetic cost/target-only catalogue'})
        report=analyze_decision(snapshot,use_cache=False);batches=(report.get('combat') or {}).get('batches')
        player=snapshot['state']['player']
        rows.append(dict(turn=turn,source_event_id=event['event_id'],source_kind=event['kind'],
            original_snapshot_fingerprint=digest(original),copied_fields=copied,
            illustration_only=not original.get('ready'),
            resources=dict(hp=player['creature']['hp'],energy=player['energy'],block=player['creature']['block'],
                hand=[{k:c.get(k) for k in ('instance_id','entity_id','name','energy_cost','description')} for c in player.get('hand') or []],
                enemies=[{k:e.get(k) for k in ('instance_id','name','hp','block','powers','intents')} for e in snapshot['state'].get('enemies') or []]),
            autonomy=report['autonomy'],batches=batches,recorded_choices=choices.get(turn,[])))
    return dict(schema_version='combat-batch-replay/1.0',run_directory=str(folder),turns=rows,
        code_fingerprint=digest({str(p.relative_to(ROOT)):p.read_text(encoding='utf-8-sig')
            for name in ('decision','planning','evaluation','execution')
            for p in (ROOT/'src/python/src/autoslay'/name).glob('*.py')}),
        policies={name:decision_config(ROOT/('config/decision/'+name+'.default.json')) for name in ('combat','analysis','autonomy')},
        validation='counterfactual recommendations on independent historical states; not a replayed game trajectory',
        execution_authorized=False,limits=['Only copied control_mode is overridden for read-only recommendation.',
            'No model requests, game actions or original archive changes. Time budgets may cause estimate variation.'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_directory',type=Path);parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--include-paused-estimates',action='store_true',help='Illustrate paused resources using synthetic cost-only legal operations; never executable evidence')
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Replay output already exists')
    result=audit(args.run_directory,args.include_paused_estimates);atomic(args.output,result)
    print(json.dumps(dict(path=str(args.output),turns=[dict(turn=r['turn'],rule=r['autonomy'].get('rule_id'),
        steps=r['autonomy'].get('selected_steps'),nodes=(r['batches'] or {}).get('nodes')) for r in result['turns']]),ensure_ascii=False))


if __name__=='__main__':main()
