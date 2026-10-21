"""Bounded valuation of declared event state machines and supplied graphs."""
from dataclasses import replace,asdict
from decimal import Decimal
from autoslay.rules.flows import FlowMachine
from autoslay.evaluation.strategic import RunResources
from autoslay.evaluation.events import transition_value
from autoslay.decision.contracts import UnsupportedEffect

VERSION='3.0.0'


def evaluate_event_sequence(event,deck,context,entries,policy,game_version,*,max_steps=12,max_nodes=500,max_hp_weight=None):
    if type(max_steps) is not int or not 1<=max_steps<=30 or type(max_nodes) is not int or not 1<=max_nodes<=10000:
        raise ValueError('Invalid event state-machine budget')
    base=dict(module_version=VERSION,supported=False,candidates=[],execution_authorized=False)
    if event.get('interaction_warnings'):
        return dict(base,reason='event_resource_hooks_unmodeled',warnings=event['interaction_warnings'])
    machine=FlowMachine(references=entries)
    from autoslay.rules.registry import IMPLEMENTATION_SHA256
    if game_version!=machine.models.game_version or event.get('game_assembly_sha256')!=IMPLEMENTATION_SHA256:
        return dict(base,reason='event_rule_version_or_hash_mismatch')
    if max_hp_weight is not None:policy=replace(policy,max_hp_weight=max_hp_weight)
    initial_resources=RunResources(context.hp,context.max_hp,context.gold,dead=context.hp==0,
        deck=tuple(deck),relics=tuple(event.get('owned_relics') or []))
    try:initial,bindings=machine.bind(event,initial_resources)
    except (ValueError,KeyError,TypeError) as exc:return dict(base,reason=str(exc))
    nodes=0;truncated=False
    def solve(state,depth):
        nonlocal nodes,truncated
        if depth>=max_steps or nodes>=max_nodes:
            truncated=True
            return dict(known=False,comparison_complete=False,best_known=None,best=None,candidates=[],reason='flow_search_budget')
        nodes+=1;rows=[]
        for option in machine.options(state):
            if depth==0 and option['id'] not in bindings:continue
            outcomes=machine.apply(state,option['id']);branches=[];complete=True
            for outcome in outcomes:
                resources=outcome.state.resources;gaps=list(outcome.gaps)
                immediate=dict(hp_after=resources.hp,max_hp_after=resources.max_hp,gold_after=resources.gold,
                    deck_after=list(resources.deck),fatal=resources.dead,command_trace=list(outcome.trace),utility=None)
                if not gaps:
                    try:immediate.update(transition_value(state.resources,resources,context,entries,policy))
                    except ValueError as exc:gaps.append(str(exc))
                continuation=None;value=immediate['utility'];fatal=float(resources.dead)
                if not gaps and not outcome.boundary and not resources.dead and outcome.state.page is not None:
                    continuation=solve(outcome.state,depth+1)
                    if continuation['known']:
                        value+=continuation['best']['expected_utility'];fatal=continuation['best']['fatal_probability']
                    else:gaps.append('continuation_not_fully_modeled')
                if gaps or outcome.boundary:complete=False
                branches.append(dict(probability=outcome.weight,immediate=immediate,continuation=continuation,
                    total_utility=None if outcome.boundary else value,fatal=fatal,known=not gaps and not outcome.boundary,
                    mechanism_gaps=gaps,observation_boundary=outcome.boundary,
                    flow_state=dict(page=outcome.state.page,variables={k:str(v) if type(v) is Decimal else v for k,v in outcome.state.variables.items()})))
            row=dict(option_id=option['id'],utility=None,known=complete,branches=branches,
                callback=option['callback'],expected_utility=None,fatal_probability=None,
                known_fatal=any(o.weight>0 and o.state.resources.dead for o in outcomes))
            if depth==0:
                observed=bindings[option['id']]
                row.update(action_id=observed.get('action_id'),title=observed.get('title'),
                    first_action_id=observed.get('action_id'))
            if complete:
                expected=sum(b['probability']*b['total_utility'] for b in branches)
                downside=sum(b['probability']*max(0,-b['total_utility']) for b in branches)
                row.update(expected_utility=expected,utility=expected-policy.risk_weight*downside,
                    fatal_probability=sum(b['probability']*b['fatal'] for b in branches),
                    expected_hp_change=sum(b['probability']*(b['immediate']['hp_after']-state.resources.hp) for b in branches),
                    expected_max_hp_change=sum(b['probability']*(b['immediate']['max_hp_after']-state.resources.max_hp) for b in branches),
                    expected_gold_change=sum(b['probability']*(b['immediate']['gold_after']-state.resources.gold) for b in branches))
            rows.append(row)
        known=sorted((r for r in rows if r['known']),key=lambda r:(r['fatal_probability'],-r['utility']))
        best={k:v for k,v in known[0].items() if k!='branches'} if known else None
        rows.sort(key=lambda r:(not r['known'],r['fatal_probability'] if r['known'] else 0,-r['utility'] if r['known'] else 0))
        complete=bool(rows) and all(r['known'] for r in rows)
        return dict(known=complete,comparison_complete=complete,best=best,best_known=best,candidates=rows)
    result=solve(initial,0)
    return dict(base,**result,supported=True,nodes=nodes,search_truncated=truncated,
        rule_fingerprint=machine.models.fingerprint,source=machine.models.flows[initial.model_id]['provenance'],
        validation='source_reviewed_declaration; live_transition_pending',
        limitations=['only current option actions are bound; reobserve at every page',
            'resource/deck valuation is a proxy; declared commands define state changes',
            'unknown mechanics and search cutoffs prevent a complete comparison'])


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
