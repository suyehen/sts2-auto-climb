"""Event action forecasts and residuals; no automatic weight fitting or causality claim."""
from statistics import mean
from pathlib import Path

VERSION='1.0.0'


def compare(candidate,before,after):
    def resources(player):
        creature=player.get('creature') or {}
        return dict(hp=creature.get('hp'),max_hp=creature.get('max_hp'),gold=player.get('gold'))
    old=resources(before);new=resources(after)
    actual={k:new[k]-old[k] if type(old[k]) is int and type(new[k]) is int else None for k in old}
    predicted={}; support={}
    branches=candidate.get('branches',[])
    if not branches and candidate.get('outcomes'):
        branches=[dict(probability=o['probability'],immediate=o) for o in candidate['outcomes']]
    if branches and all(b.get('immediate') for b in branches):
        for k in old:
            values=[(b['probability'],b['immediate'].get(k+'_after')) for b in branches]
            if type(old[k]) is int and all(type(v) is int for _,v in values):
                predicted[k]=sum(p*(v-old[k]) for p,v in values)
                support[k]=sorted(set(v-old[k] for _,v in values))
    elif candidate.get('trace'):
        first=candidate['trace'][0]
        if first.get('operation')=='gain_max_hp_then_damage':
            predicted['hp']=first['hp_after']-first['hp_before']; predicted['max_hp']=first['gain_max_hp']
        elif first.get('operation')=='exit': predicted['hp']=first['heal'];predicted['max_hp']=0
        support={k:[v] for k,v in predicted.items()}
    residual={k:actual[k]-v if actual[k] is not None else None for k,v in predicted.items()}
    return dict(module_version=VERSION,predicted_change=predicted,actual_change=actual,residual=residual,
                within_modeled_support={k:actual[k] in values if actual[k] is not None else None for k,values in support.items()},
                forecast_scope='first_action_only',weight_update_applied=False,
                limitations=['实测差值可能包含多次操作或钩子；需要核对执行记录后才能归因',
                             '随机结果偏离期望值不等于模型错误；同时检查结果是否在已建模范围内'])


def summarize(rows):
    metrics={}
    for resource in ('hp','max_hp','gold'):
        values=[r['residual'][resource] for r in rows if r.get('residual',{}).get(resource) is not None]
        metrics[resource]=dict(samples=len(values),mean_error=mean(values) if values else None,
                               mean_absolute_error=mean(abs(v) for v in values) if values else None)
    return dict(module_version=VERSION,metrics=metrics,weight_update_applied=False,
                status='observational_review_required' if rows else 'awaiting_live_samples')


def record_action(report,before,after,action_id,result,directory=None):
    """Only correlate a settled single Agent action within the same live room."""
    from autoslay.calibration.store import save,safe_id,DIRECTORY
    old=before.get('state') or {};new=after.get('state') or {}
    if old.get('phase')!='event': return None
    if result.get('status')!='completed' or not after.get('connected') or after.get('error') or after.get('busy'):
        return None
    if any(before.get(k)!=after.get(k) for k in ('session_epoch','game_version','game_assembly_sha256')):
        return None
    if not old.get('run_id') or old.get('run_id')!=new.get('run_id') or old.get('floor')!=new.get('floor'): return None
    if type(before.get('state_version')) is not int or type(after.get('state_version')) is not int or after['state_version']<=before['state_version']: return None
    candidates=(report.get('event_sequence') or {}).get('candidates',[])
    matched=[c for c in candidates if c.get('action_id',c.get('first_action_id'))==action_id]
    if not matched:
        matched=[c for c in (report.get('event') or {}).get('candidates',[]) if c.get('action_id')==action_id]
    if not matched and not any(o.get('action_id')==action_id for o in (old.get('event_screen') or {}).get('options',[])): return None
    comparison=compare(matched[0] if matched else {},old.get('player') or {},new.get('player') or {})
    comparison.update(observed_variables=(new.get('event_screen') or {}).get('dynamic_variables'),
                      observed_runtime_state=(new.get('event_screen') or {}).get('runtime_state'))
    payload=dict(decision_id=report['analysis_id'],horizon='action',phase='event',event_comparison=comparison,
                 action_id=action_id,source=dict(game_version=before.get('game_version'),
                    game_assembly_sha256=before.get('game_assembly_sha256'),session_epoch=before.get('session_epoch'),
                    run_id=old['run_id'],floor=old.get('floor'),entity_id=(old.get('event_screen') or {}).get('entity_id')),
                 state_version=after['state_version'],model_versions=report.get('module_versions'),
                 evidence='settled_agent_action; observation is not counterfactual proof')
    identity=safe_id(report['analysis_id'])+'-event-action'
    save(Path(directory or DIRECTORY)/'observations'/(identity+'.json'),payload)
    return comparison


def runtime_validation(snapshot):
    """Check actually observed event transport fields; never certify future semantics."""
    from autoslay.knowledge_base.behavior import load
    event=(snapshot.get('state') or {}).get('event_screen') or {}; data=load()
    entry=next((e for e in data['entries'] if e['entity_id']==event.get('entity_id')),None)
    observed=(event.get('runtime_state') or {}).get('fields') or {}
    expected=[f['name'] for f in (entry or {}).get('event_model',{}).get('state_fields',[])]
    missing=[name for name in expected if name not in observed]
    variables={v['name']:v.get('value',v.get('base_value')) for v in event.get('dynamic_variables') or []}
    return dict(source_matches=snapshot.get('game_assembly_sha256')==data.get('game_assembly_sha256') and snapshot.get('game_version')==data.get('game_version'),
                live_connected=bool(snapshot.get('connected') and not snapshot.get('error')),
                observed_field_count=len(observed),missing_fields=missing,variables=variables,
                status='observed' if snapshot.get('connected') and not snapshot.get('error') and not missing else 'incomplete',
                transition_validation='requires_settled_action_observation')
