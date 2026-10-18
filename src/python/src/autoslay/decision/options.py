"""Materialized plans: models select IDs; only the server supplies operations."""
from copy import deepcopy
from autoslay.decision.deterministic import ADMIN


def decision_options(report,snapshot):
    if 'decision_options' in report:
        return deepcopy(report['decision_options'])
    state=snapshot.get('state') or {}
    legal={a['action_id']:a for a in state.get('legal_actions',[])
           if a['action_id'] not in ADMIN and a.get('type') not in ADMIN}
    combat=report.get('combat') or {}
    groups=[]
    for key in ('candidates','observation_boundaries','wait_candidates'):
        groups.append(('combat.'+key,combat.get(key) or []))
    groups.append(('combat.batches',((combat.get('batches') or {}).get('top_batches') or [])))
    if not combat:
        for key in ('shortlist','observations'):
            groups.append((key,report.get(key) or []))
    for key in ('choices','shop','event','event_sequence','relic','rest','route'):
        groups.append((key,(report.get(key) or {}).get('candidates') or []))
    out=[];seen={}
    def add(group,index,candidate,steps):
        steps=[dict(action_id=s['action_id'],target_id=s.get('target_id')) for s in steps]
        if not steps or len(steps)>16 or (len(steps)>1 and state.get('phase')!='combat'):
            return
        action=legal.get(steps[0]['action_id']);target=steps[0]['target_id']
        if not action or (target is None and not action.get('allows_no_target')) or (target is not None and target not in action.get('allowed_target_ids',[])):
            return
        signature=tuple((s['action_id'],s['target_id']) for s in steps)
        evidence=dict(group=group,index=index,candidate=deepcopy(candidate))
        if signature in seen:
            if group!='legal_options':seen[signature]['analyses'].append(evidence)
            return
        out.append(dict(candidate_id='option_'+str(len(out)+1),group=group,index=index,
                        steps=steps,candidate=deepcopy(candidate),analyses=[evidence]))
        seen[signature]=out[-1]
    for group,rows in groups:
        for index,candidate in enumerate(rows):
            steps=candidate.get('steps')
            action_id=candidate.get('action_id') or candidate.get('first_action_id')
            if not action_id and candidate.get('option_id') is not None:
                option=next((o for o in (state.get('event_screen') or {}).get('options',[])
                             if o.get('option_id')==candidate['option_id']),{})
                action_id=option.get('action_id')
            if steps is not None:
                add(group,index,candidate,steps or [dict(action_id='end_turn')])
            elif action_id:
                add(group,index,candidate,[dict(action_id=action_id,target_id=candidate.get('target_id'))])
    # Screens without a score model still expose fixed one-operation options.
    # They are materialized before inference, never assembled by the model.
    if not combat or not out:
        for action_id,action in legal.items():
            targets=list(action.get('allowed_target_ids') or [])
            if action.get('allows_no_target'):targets.append(None)
            for target in targets:
                step=dict(action_id=action_id,target_id=target)
                add('legal_options',None,dict(steps=[step]),[step])
    return out
