"""Bounded explicit event state transitions with re-evaluation after every change."""
from dataclasses import asdict,replace
from autoslay.knowledge.events import rules
from autoslay.evaluation.run import deck_value

VERSION='1.1.0'

def evaluate_event_sequence(event,deck,context,entries,policy,game_version,*,max_steps=12,max_hp_weight=None):
    if type(max_steps) is not int or not 1<=max_steps<=30: raise ValueError('Event depth 1..30 required')
    data=rules(); rule=data['entries'].get(event.get('entity_id'))
    if not rule:
        from autoslay.evaluation.event_adapters import evaluate_source_event
        return evaluate_source_event(event,deck,context,entries,policy,game_version,max_steps=max_steps)
    if game_version!=data['game_version']: return dict(supported=False,reason='event_rule_version_mismatch',candidates=[])
    if event.get('interaction_warnings'): return dict(supported=False,reason='event_resource_hooks_unmodeled',
                                                    warnings=event['interaction_warnings'],candidates=[])
    if rule['model']=='explicit_graph':
        observed={o.get('text_key') for o in event.get('options',[]) if o.get('available',True)}
        matched=[s['state'] for s in rule['stage_selectors'] if set(s['text_keys'])<=observed]
        if len(matched)!=1: return dict(supported=False,reason='ambiguous_or_unknown_event_stage',candidates=[])
        return evaluate_graph(rule['states'],matched[0],deck,context,entries,policy,max_depth=max_steps)
    if rule['model']!='repeat_max_hp_then_damage': raise ValueError('Unknown event model')
    if max_hp_weight is None: max_hp_weight=policy.max_hp_weight
    # Relic heal/damage/maxHP hooks can change both survival and utility.
    options=[o for o in event.get('options',[]) if o.get('available',True) and not o.get('locked')]
    keys=[o.get('text_key','') for o in options]
    initial=any(k.endswith('.IMMERSE') for k in keys)
    later=any(k.endswith('.LINGER') for k in keys)
    if not initial and not later: return dict(supported=False,reason='unrecognized_event_stage',candidates=[])
    variables={v['name']:v.get('value',v.get('base_value')) for v in event.get('dynamic_variables') or []}
    damage=variables.get('Damage',rule['initial_damage'] if initial else None)
    if type(damage) not in {int,float} or damage<0: return dict(supported=False,reason='next_damage_not_observed',candidates=[])
    gain=variables.get('MaxHp',rule['gain_max_hp']); heal=variables.get('Heal',rule['initial_exit_heal'])
    if any(type(v) not in {int,float} or v<0 for v in (gain,heal)): raise ValueError('Invalid event variables')
    gain,heal,damage=int(gain),int(heal),int(damage)
    start=context; base=deck_value(deck,start,entries,policy)['utility_points']; rows=[]; traces=[]
    current=start; resource_utility=0.; fatal_seen=False
    for count in range(max_steps+1):
        exit_heal=heal if initial and count==0 else rule['later_exit_heal']
        actual=min(exit_heal,current.max_hp-current.hp)
        final=replace(current,hp=current.hp+actual)
        deck_delta=deck_value(deck,final,entries,policy)['utility_points']-base
        utility=resource_utility+actual*policy.hp_weight*current.urgency+deck_delta
        first_key='.IMMERSE' if initial and count else '.LINGER' if count else '.ABSTAIN' if initial else '.EXIT_BATHS'
        action=next((o.get('action_id') for o in options if o.get('text_key','').endswith(first_key)),None)
        rows.append(dict(repeats=count,utility=utility,fatal=False,first_action_id=action,
                         final_context=asdict(final),trace=list(traces)+[dict(operation='exit',heal=actual)],
                         max_hp_gain=final.max_hp-start.max_hp,hp_change=final.hp-start.hp,
                         components=dict(resource_utility=resource_utility,exit_heal_utility=actual*policy.hp_weight*current.urgency,deck_environment_delta=deck_delta)))
        if count==max_steps: break
        next_max=current.max_hp+gain
        gained_hp=gain if rule['gain_max_hp_also_heals'] else 0
        next_hp=min(next_max,current.hp+gained_hp)-damage
        if next_hp<=0:
            fatal_seen=True; break
        step_utility=gain*max_hp_weight+(gained_hp-damage)*policy.hp_weight*current.urgency
        resource_utility+=step_utility
        traces.append(dict(operation='gain_max_hp_then_damage',gain_max_hp=gain,heal_from_max_hp=gained_hp,
                           damage=damage,hp_before=current.hp,hp_after=next_hp,max_hp_after=next_max,utility=step_utility))
        current=replace(current,hp=next_hp,max_hp=next_max); damage+=rule['damage_increment']
    rows.sort(key=lambda r:r['utility'],reverse=True)
    return dict(module_version=VERSION,supported=True,source=rule['source'],validation=rule['validation'],
                candidates=rows,search_truncated=not fatal_seen and len(traces)==max_steps,
                avoided_lethal_continuation=fatal_seen,execution_authorized=False,
                assumptions=dict(max_hp_weight=max_hp_weight,units='heuristic points; not calibrated lifetime HP value'),
                limitations=rule['limitations']+['only first current action is bound; reobserve after each event choice'])


def evaluate_graph(graph,start_state,deck,context,entries,policy,*,max_depth=8,max_nodes=500):
    """Known finite event graph. Exact probabilities, explicit stopping and no inferred edges."""
    from autoslay.evaluation.events import effect_value
    from math import isfinite
    if type(max_depth) is not int or not 1<=max_depth<=20 or type(max_nodes) is not int or not 1<=max_nodes<=10000:
        raise ValueError('Invalid event graph budget')
    if start_state not in graph: raise ValueError('Unknown event state')
    nodes=0; truncated=False
    def solve(state,current_deck,current_context,depth):
        nonlocal nodes,truncated
        if depth>=max_depth or nodes>=max_nodes:
            truncated=True; return dict(known=False,reason='event_graph_budget_exceeded')
        nodes+=1; candidates=[]
        for option in graph[state]:
            if not option.get('available',True): continue
            outcomes=option.get('outcomes')
            if outcomes is None: outcomes=[dict(probability=1,effects=option.get('effects',[]),next_state=option.get('next_state'))]
            probabilities=[o.get('probability') for o in outcomes]
            if not outcomes or any(type(p) not in {int,float} or not isfinite(p) or not 0<=p<=1 for p in probabilities) or abs(sum(probabilities)-1)>1e-9:
                raise ValueError('Explicit outcome probabilities summing to 1 required')
            branches=[]; known=True
            for outcome in outcomes:
                if outcome['probability']==0: continue
                try:
                    result=effect_value(outcome['effects'],current_deck,current_context,entries,policy)
                except (ValueError,KeyError,TypeError) as exc:
                    known=False
                    branches.append(dict(probability=outcome['probability'],known=False,reason=str(exc)))
                    continue
                continuation=None; value=result['utility']; fatal=float(result['fatal'])
                next_state=outcome.get('next_state')
                if next_state is not None and not result['fatal']:
                    if next_state not in graph: raise ValueError('Unknown event transition target')
                    next_context=replace(current_context,hp=result['hp_after'],max_hp=result['max_hp_after'],gold=result['gold_after'])
                    continuation=solve(next_state,result['deck_after'],next_context,depth+1)
                    if not continuation.get('known'): known=False
                    else:
                        value+=continuation['best']['expected_utility']; fatal=continuation['best']['fatal_probability']
                branches.append(dict(probability=outcome['probability'],immediate=result,continuation=continuation,total_utility=value,fatal=fatal))
            if not known:
                candidates.append(dict(option_id=option.get('option_id'),utility=None,known=False,branches=branches,
                                       reason=option.get('unknown_reason','后续效果或选项存在未知值')));continue
            expected=sum(b['probability']*b['total_utility'] for b in branches)
            downside=sum(b['probability']*max(0,-b['total_utility']) for b in branches)
            fatal=sum(b['probability']*b['fatal'] for b in branches)
            candidates.append(dict(option_id=option.get('option_id'),known=True,expected_utility=expected,
                                   utility=expected-policy.risk_weight*downside,fatal_probability=fatal,branches=branches,
                                   immediate_utility=sum(b['probability']*b['immediate']['utility'] for b in branches),
                                   future_utility=expected-sum(b['probability']*b['immediate']['utility'] for b in branches),
                                   downside_penalty=policy.risk_weight*downside,
                                   expected_hp_change=sum(b['probability']*(b['immediate']['hp_after']-current_context.hp) for b in branches),
                                   expected_max_hp_change=sum(b['probability']*(b['immediate']['max_hp_after']-current_context.max_hp) for b in branches),
                                   expected_gold_change=sum(b['probability']*(b['immediate']['gold_after']-current_context.gold) for b in branches)))
        known=[c for c in candidates if c['known']]
        known.sort(key=lambda c:(-c['fatal_probability'],c['utility']),reverse=True)
        complete=bool(candidates) and all(c['known'] for c in candidates)
        summary={k:v for k,v in known[0].items() if k!='branches'} if known else None
        return dict(known=complete,best=summary,candidates=candidates,
                    best_known=summary,comparison_complete=complete)
    result=solve(start_state,deck,context,0)
    return dict(module_version=VERSION,**result,nodes=nodes,search_truncated=truncated,execution_authorized=False,
                assumptions=['caller/source-provided transition graph','risk is local receding-horizon preference, not globally time-consistent risk measure'])
