"""Bounded stochastic continuation under explicit future-intent scenarios.

Uses only public pile composition. It never consults hidden order or enemy move scripts.
Unknown branch mass is reported instead of assigning it zero value.
"""
from collections import Counter
from dataclasses import replace
from time import perf_counter
from autoslay.decision.contracts import Operation, Transition, UnsupportedEffect
from autoslay.rules.registry import IroncladRules
from autoslay.rules.periodic_relics import advance
from autoslay.simulation.engine import CombatSimulator
from autoslay.simulation.card_flow import plan_draw, add_to_composition
from autoslay.probability.draw import distribution
from autoslay.knowledge.templates import canonical
from autoslay.planning.search import search, SearchBudget
from autoslay.evaluation.combat import CombatEvaluator

VERSION='1.1.0'

class ScenarioRules(IroncladRules):
    def card(self,card,state):
        if not card.canonical: return super().card(card,state)
        rule=super().card(card,replace(state,legion_ready=False))
        effects=[]
        for effect in rule.effects:
            value=effect.amount
            if effect.kind in {'damage','dismantle'}:
                value=max(0,value+state.base_strength+state.strength_delta)
                if state.player_weak: value=value*3//4
                value-=state.strength_delta  # Engine adds delta; do not apply Strength twice.
            elif effect.kind in {'block','exhaust_nonattacks'} and state.player_frail: value=value*3//4
            effects.append(replace(effect,amount=value))
        return replace(rule,effects=tuple(effects))

def templates_for(snapshot,entries):
    templates={}; variants={}
    p=snapshot['state']['player']
    for raw in (p.get('deck') or [])+(p.get('hand') or [])+(p.get('discard') or [])+(p.get('exhaust') or []):
        entity=raw['entity_id']; variants.setdefault(entity,set()).add(raw.get('upgrade_level',0))
        try: templates[entity]=replace(canonical(raw,entries)[0],canonical=True)
        except ValueError: pass
    for entity,levels in variants.items():
        if len(levels)>1: templates.pop(entity,None)
    return templates

def draw_branches(state,count,templates,limit,seed):
    """Ordinary draw and one reshuffle; branch probabilities retain exact/sample mass."""
    plan=plan_draw(count,len(state.hand),sum(n for _,n in state.draw_composition),sum(n for _,n in state.discard_composition),
                   blocked=state.effect_draw_blocked,discard_known=state.discard_known)
    if not plan.supported: return [],'unknown_discard'
    draw=dict(state.draw_composition); discard=dict(state.discard_composition)
    known=Counter()
    if plan.actual>sum(draw.values()):
        known.update(draw); pool=discard; number=plan.actual-sum(draw.values()); reshuffle=True
    else: pool=draw; number=plan.actual; reshuffle=False
    outcomes=distribution(pool,number,exact_limit=limit,samples=limit,seed=seed)
    result=[]
    for branch_index,outcome in enumerate(outcomes.outcomes):
        selected=Counter(dict(outcome.cards)); all_drawn=known+selected
        if any(entity not in templates for entity in all_drawn):
            result.append((float(outcome.probability),None)); continue
        cards=[]; used={c.instance_id for c in state.hand}
        for entity,n in sorted(all_drawn.items()):
            for i in range(n):
                identity=f'scenario-{state.turn}-{branch_index}-{entity}-{i}'
                while identity in used: identity+='-next'
                used.add(identity); cards.append(replace(templates[entity],instance_id=identity))
        remainder=Counter(pool); remainder.subtract(selected)
        result.append((float(outcome.probability),replace(state,hand=state.hand+tuple(cards),
            draw_composition=tuple(sorted((k,n) for k,n in remainder.items() if n)),
            discard_composition=() if reshuffle else state.discard_composition)))
    return result,outcomes.method

def evaluate_lookahead(snapshot,initial,candidates,boundaries,entries,settings,scenarios=None):
    horizon=settings.get('horizon',2); limit=settings.get('branches',8); total_ms=settings.get('time_ms',600)
    seed=settings.get('seed',42); draws=settings.get('draws',5); energy=settings.get('energy',3)
    if any(type(v) is not int for v in (horizon,limit,total_ms,seed,draws,energy)) or not (1<=horizon<=3 and 1<=limit<=32 and 1<=total_ms<=3000 and 0<=draws<=10 and 0<=energy<=10):
        raise ValueError('Invalid bounded lookahead settings')
    scenarios=scenarios or [dict(name='repeat_visible_attack_scenario',future_attacks={e.instance_id:list(e.attacks) for e in initial.enemies})]
    if not 1<=len(scenarios)<=4: raise ValueError('At most four explicit scenarios')
    for scenario in scenarios:
        if not isinstance(scenario,dict) or not isinstance(scenario.get('name'),str) or not isinstance(scenario.get('future_attacks'),dict):
            raise ValueError('Named scenario with explicit future_attacks required')
        if any(e.hp>0 and e.instance_id not in scenario['future_attacks'] for e in initial.enemies):
            raise ValueError('Scenario must specify attacks for every living enemy; [] explicitly means no attack')
        for hits in scenario.get('future_attacks',{}).values():
            if not isinstance(hits,list) or len(hits)>100 or any(type(h) is not int or not 0<=h<=10000 for h in hits): raise ValueError('Invalid scenario attacks')
    started=perf_counter(); templates=templates_for(snapshot,entries); observations=set(); methods=set(); nodes=0
    def expired(): return (perf_counter()-started)*1000>=total_ms
    def terminal(state):
        won=state.combat_won or all(e.hp<=0 for e in state.enemies)
        return dict(hp_loss=initial.hp-state.hp,remaining_enemy_hp=sum(e.hp for e in state.enemies if not e.minion),
                    survived=state.hp>0,finished=won and state.hp>0,
                    utility=state.hp-initial.hp-0.4*sum(e.hp for e in state.enemies if not e.minion)+(50 if won else 0)-(1000 if state.hp<=0 else 0))
    def combine(results):
        weighted=[(p*r.get('covered_probability',1),r.get('conditional_metrics',r)) for p,r in results if r is not None and r.get('conditional_metrics',r) is not None]
        covered=sum(p for p,r in weighted)
        if covered<=0: return dict(covered_probability=0,conditional_metrics=None)
        keys=('hp_loss','remaining_enemy_hp','survived','finished','utility')
        return dict(covered_probability=covered,conditional_metrics={k:sum(p*r[k] for p,r in weighted)/covered for k in keys})
    def continue_transition(sim,before,transition,steps,ply,scenario):
        if transition.boundary=='draw_requires_observation':
            branch_values,method=draw_branches(transition.state,transition.draw_count,templates,limit,seed+ply); methods.add(method)
            if not steps: return None
            results=[]
            for probability,state in branch_values:
                if state is None or expired(): results.append((probability,None)); continue
                resumed=sim.resume_draw(transition,state)
                valued=(continue_transition(sim,state,resumed,steps,ply,scenario) if resumed.boundary
                        else visit(resumed.state,ply,scenario))
                results.append((probability,valued))
            combined=combine(results)
            if combined['covered_probability']<1-1e-9: observations.add('unsupported_or_budget_limited_draw_mass')
            return combined
        if transition.boundary:
            observations.add(transition.boundary); return None
        state=transition.state
        if state.hp<=0 or state.combat_won: return dict(covered_probability=1,conditional_metrics=terminal(state))
        if any(c.instance_id in sim.unsupported and c.entity_id!='CARD.ASCENDERS_BANE' for c in state.hand):
            observations.add('unsupported_remaining_card_end_turn_hooks'); return None
        loss=CombatEvaluator().evaluate(state,Transition(state),())['projected_hp_loss']
        state=replace(state,hp=max(0,state.hp-loss))
        if state.hp<=0 or ply>=horizon: return dict(covered_probability=1,conditional_metrics=terminal(state))
        if state.effect_draw_blocked or any(e.weak for e in state.enemies):
            observations.add('future_draw_prevention_or_weak_duration_unmodeled'); return None
        relics=state.periodic_relics; extra_draw=extra_energy=0
        if relics:
            if any(r.counter is None for r in relics): observations.add('unknown_periodic_counter'); return None
            relics,extra_draw,extra_energy=advance(relics)
        discarded=add_to_composition(state.discard_composition,[c.entity_id for c in state.hand if c.entity_id!='CARD.ASCENDERS_BANE'])
        exhausted=add_to_composition(state.exhausted_composition,[c.entity_id for c in state.hand if c.entity_id=='CARD.ASCENDERS_BANE'])
        state=replace(state,hand=(),block=0,energy=energy+extra_energy,discard_composition=discarded,exhausted_composition=exhausted,
            base_strength=state.base_strength+state.strength_delta-state.temporary_strength,strength_delta=0,temporary_strength=0,
            colossus=False,legion_ready=False,periodic_relics=relics,turn=(state.turn or 0)+1,
            weak_turns=max(0,state.weak_turns-1),frail_turns=max(0,state.frail_turns-1),
            player_weak=state.weak_turns>1,player_frail=state.frail_turns>1,
            enemies=tuple(replace(e,block=0,vulnerable=max(0,e.vulnerable-1),
                attacks=tuple(scenario.get('future_attacks',{}).get(e.instance_id,[]))) for e in state.enemies))
        branches,method=draw_branches(state,draws+extra_draw,templates,limit,seed+ply); methods.add(method)
        outcomes=[]
        for probability,branch in branches:
            outcomes.append((probability,visit(branch,ply+1,scenario) if branch is not None and not expired() else None))
        return combine(outcomes)
    def visit(state,ply,scenario):
        nonlocal nodes
        if expired(): return None
        sim=CombatSimulator(state,ScenarioRules())
        report=search(state,sim,CombatEvaluator(),SearchBudget(max_nodes=150,time_ms=max(1,min(25,int(total_ms))),top_k=2,max_depth=6)); nodes+=report['nodes']
        best=None
        for candidate in report['candidates'][:2]+report['observation_boundaries'][:1]:
            local=state; result=Transition(local); operations=[]
            try:
                for step in candidate['steps']:
                    operations.append(Operation(**step)); before=local; result=sim.apply(local,operations[-1]); local=result.state
                valued=continue_transition(sim,before if operations else state,result,operations,ply,scenario)
            except (ValueError,KeyError,StopIteration): valued=None
            if valued and valued.get('conditional_metrics'):
                if best is None or valued['conditional_metrics']['utility']>best['conditional_metrics']['utility']: best=valued
        return best
    output=[]
    for group,records in [('candidates',candidates),('observation_boundaries',boundaries)]:
        for index,candidate in enumerate(records[:3]):
            scenario_results=[]
            for scenario in scenarios:
                local=initial; sim=CombatSimulator(initial); transition=Transition(initial); operations=[]
                try:
                    for step in candidate['steps']:
                        before=local; operations.append(Operation(**step)); transition=sim.apply(local,operations[-1]); local=transition.state
                    result=continue_transition(sim,before if operations else initial,transition,operations,1,scenario) if not expired() else None
                except (ValueError,KeyError,StopIteration) as exc: observations.add(str(exc)); result=None
                scenario_results.append(dict(name=scenario['name'],result=result))
            output.append(dict(group=group,index=index,steps=candidate['steps'],scenarios=scenario_results))
    return dict(module_version=VERSION,horizon=horizon,candidates=output,elapsed_ms=(perf_counter()-started)*1000,
        nodes=nodes,budget_exhausted=expired(),draw_methods=sorted(methods),unmodeled=sorted(observations),
        scenarios=scenarios,execution_authorized=False,
        assumptions=['uniform unknown draw order; ordinary discard reshuffle; no hidden enemy move data',
                     'future attacks are caller scenarios or repeated visible attacks, not predictions of actual next moves',
                     'default future energy 3 / draw 5; unsupported hooks and ambiguous upgraded variants lose coverage',
                     'sampled draw fractions are seeded empirical estimates, not exact probabilities'],
        limitations=['conditional survived/finished fractions apply only to the stated scenario, not calibrated win probability',
                     'bounded continuation uses current-turn candidate policy; incomplete branches never certify safety',
                     'remaining HP-loss cap is a conservative forecast; self-damage under unknown cap stops'])
