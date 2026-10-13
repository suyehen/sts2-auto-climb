"""Public before/after execution facts, independent of strategy scores and simulation."""
from collections import Counter


def observe(before, after, status='completed', request_id=None):
    result=dict(schema_version='observed-effects/1.0',status='unavailable',request_id=request_id,
                scope='observed changes during this action; no counterfactual or long-term utility',
                resources={},collections={},creatures=[],notes=[])
    if status not in {'completed','awaiting_choice'}:
        result['notes'].append('动作尚未确认完成，未把状态差异归入已执行效果。')
        return result
    left,right=before.get('state') or {},after.get('state') or {}
    run=lambda s:s.get('command_context_id') or s.get('run_id')
    if (not before.get('session_epoch') or before.get('session_epoch')!=after.get('session_epoch')
            or not run(left) or run(left)!=run(right) or before.get('game_version')!=after.get('game_version')
            or not left.get('player') or not right.get('player')):
        result['notes'].append('缺少同一会话与对局的前后快照，不能补造效果。')
        return result
    p,q=left['player'],right['player'];a,b=p.get('creature') or {},q.get('creature') or {}
    for key,x,y in [('hp',a.get('hp'),b.get('hp')),('max_hp',a.get('max_hp'),b.get('max_hp')),
                    ('block',a.get('block'),b.get('block')),('gold',p.get('gold'),q.get('gold')),
                    ('energy',p.get('energy'),q.get('energy')),('stars',p.get('stars'),q.get('stars'))]:
        if type(x) in {int,float} and type(y) in {int,float}:result['resources'][key]=dict(before=x,after=y,change=y-x)
    fields=('upgrade_level','enchantment_id','affliction_id','description','energy_cost','costs_x','star_cost')
    for key in ('deck','relics','potions','hand','exhaust'):
        if key not in p or key not in q:continue
        identity=lambda v:v.get('instance_id') or v.get('entity_id')
        old={identity(c):c for c in p[key] or []};new={identity(c):c for c in q[key] or []}
        additions=Counter(identity(c) for c in q[key] or [])-Counter(identity(c) for c in p[key] or [])
        removals=Counter(identity(c) for c in p[key] or [])-Counter(identity(c) for c in q[key] or [])
        modified=[dict(instance_id=i,name=new[i].get('name'),entity_id=new[i].get('entity_id'),
                       changes={f:dict(before=old[i].get(f),after=new[i].get(f)) for f in fields if old[i].get(f)!=new[i].get(f)})
                  for i in old.keys()&new.keys() if any(old[i].get(f)!=new[i].get(f) for f in fields)]
        result['collections'][key]=dict(added=[dict(new[i],count=n) for i,n in additions.items()],
                                        removed=[dict(old[i],count=n) for i,n in removals.items()],modified=modified)
    creatures=[('player',a,b)]+[(i,e,next((v for v in right.get('enemies') or [] if v.get('instance_id')==i),None))
                               for e in left.get('enemies') or [] if (i:=e.get('instance_id'))]
    for identity,old,new in creatures:
        if new is None:
            result['creatures'].append(dict(instance_id=identity,name=old.get('name'),removed=True,
                                           note='离开可见战场；不能仅据此断言被本次攻击击杀。'))
            continue
        powers=lambda c:{v['entity_id']:v for v in c.get('powers') or []}
        old_p,new_p=powers(old),powers(new);changes=[]
        for key in old_p.keys()|new_p.keys():
            x=old_p.get(key,{}).get('amount',0);y=new_p.get(key,{}).get('amount',0)
            if x!=y:changes.append(dict(entity_id=key,name=(new_p.get(key) or old_p[key]).get('name'),before=x,after=y))
        result['creatures'].append(dict(instance_id=identity,name=new.get('name'),hp_before=old.get('hp'),hp_after=new.get('hp'),powers=changes))
    result.update(status='observed',phase_before=left.get('phase'),phase_after=right.get('phase'),
                  turn_before=p.get('turn'),turn_after=q.get('turn'))
    result['notes'].append('生命变化包括自伤、治疗和战后回复；抽到的牌与实际附魔已记录，未选择方案的结果不作推断。')
    return result


def _before(repo, submission, decision, request):
    snapshot = (repo.load_event(submission).get('snapshot') or {}) if submission else {}
    if snapshot:
        return snapshot
    # An older submission may omit its snapshot. Only the exact source state is
    # a valid replacement; the start of a multi-step plan is not a later step.
    source = decision['report'].get('source') or {}
    if all(request.get(a) is not None and request.get(a) == source.get(b) for a, b in
           [('session_epoch', 'session_epoch'), ('run_id', 'run_id'),
            ('expected_state_version', 'state_version')]):
        return decision.get('snapshot') or {}
    return {}


def archived(repo,decision,entries):
    """Join using analysis and request IDs; never assign a nearby action to a choice."""
    identity=decision['event_id'];submissions={}
    for row in entries:
        if row['kind']=='action_submitted' and (req:=row.get('request',{}).get('request_id')):submissions[req]=row['event_id']
    facts=[];seen=set()
    for row in entries:
        d=row.get('details') or {}
        if row['kind']!='agent_outcome' or d.get('analysis_id')!=identity:continue
        req=(d.get('request') or {}).get('request_id')
        outcome=repo.load_event(row['event_id']);detail=outcome.get('details') or {}
        after=(detail.get('receipt') or {}).get('snapshot') or outcome.get('snapshot') or {}
        before=_before(repo,submissions.get(req),decision,detail.get('request') or {})
        facts.append(detail.get('observed_effects') or observe(before,after,detail.get('status'),req))
        seen.add(req)
    # Older tools and local deterministic choices did not emit agent_outcome.
    choice=next((r.get('details') or {} for r in entries if r['kind'] in {'ai_choice','local_choice'} and
                 (r.get('details') or {}).get('analysis_id')==identity),None)
    if choice:
        steps=choice.get('steps') or choice.get('selected_steps')
        if not steps:
            try:
                from autoslay.calibration.store import candidates
                candidate=candidates(decision['report'],choice['group'])[choice['index']]
                steps=candidate.get('steps') or ([dict(action_id=candidate['first_action_id'],target_id=None)] if candidate.get('first_action_id') else [candidate])
            except (ValueError,KeyError,TypeError,IndexError):steps=[]
        source=decision['report'].get('source') or {}
        for row in entries:
            request=row.get('request') or {};req=request.get('request_id')
            if row['kind']!='action_submitted' or req in seen:continue
            if not all(request.get(a) is not None and request.get(a)==source.get(b) for a,b in
                       [('session_epoch','session_epoch'),('run_id','run_id'),('expected_state_version','state_version')]):continue
            if not any(request.get('action_id')==s.get('action_id') and request.get('target_id')==s.get('target_id') for s in steps):continue
            matches=[r for r in entries if r['kind']=='action_result' and (r.get('request') or {}).get('request_id')==req]
            if not matches:continue
            outcome=repo.load_event(matches[-1]['event_id']);receipt=outcome.get('receipt') or {}
            before=_before(repo,row['event_id'],decision,request)
            after=receipt.get('snapshot') or outcome.get('snapshot') or {}
            facts.append(observe(before,after,receipt.get('status'),req));seen.add(req)
    return facts
