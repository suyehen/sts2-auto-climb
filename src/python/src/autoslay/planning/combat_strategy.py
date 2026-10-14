"""Finite-horizon search over every legal first action with shared continuation cache.

Unknown transitions retain missing mass. Interrupted solves require a reviewed
closed-universe upper bound. Only the first real action is proposed.
"""
from dataclasses import replace, asdict
from time import perf_counter
from autoslay.decision.contracts import Operation, Transition, UnsupportedEffect
from autoslay.decision.cache import digest
from autoslay.evaluation.combat_value import ValuePolicy, evaluate_outcome, aggregate
from autoslay.evaluation.potion_value import reserve_cost, compare_pairs
from autoslay.planning.lookahead import ScenarioRules, templates_for, draw_branches
from autoslay.rules.monsters import MonsterRules
from autoslay.rules.periodic_relics import advance
from autoslay.simulation.engine import CombatSimulator
from autoslay.simulation.card_flow import add_to_composition
from autoslay.calibration.combat import apply as calibrate, state_class

VERSION='1.0.0'


def analyze(snapshot, initial, entries, combat_policy, settings, budget,
            calibration=None, knowledge_revision=None, monster_model=None):
    from autoslay.decision.deterministic import operations, ADMIN
    policy=ValuePolicy(**combat_policy.short_term)
    horizon=settings.get('horizon',2);limit=settings.get('branches',32);seed=settings.get('seed',42)
    future_draw=settings.get('draws',5);future_energy=settings.get('energy',3)
    outcome_limit=settings.get('outcome_limit',128)
    if not 1<=horizon<=2 or not 1<=limit<=128 or not 1<=outcome_limit<=1024:
        raise ValueError('Invalid combat strategy horizon/branch budget')
    started=perf_counter();nodes=0;hits=0;limits=set();problems=set();draw_metadata=[]
    memo={};edge_cache={};model=monster_model or MonsterRules(snapshot,entries)
    active_depth=1
    prepared=model.prepare(initial)
    problems.update(model.notes)
    rules=ScenarioRules()
    templates=templates_for(snapshot,entries)
    for identity in model.generated_ids:
        if identity not in templates:
            from autoslay.knowledge_base.templates import canonical
            try:templates[identity]=replace(canonical(dict(entity_id=identity,upgrade_level=0),entries)[0],canonical=True)
            except ValueError:pass
    costs={p.action_id:reserve_cost(snapshot,p,combat_policy.potion_reserve) for p in initial.potions}
    potion_map={p.action_id:p.instance_id for p in initial.potions}
    victory_heal=6 if any(r['entity_id']=='RELIC.BURNING_BLOOD' for r in snapshot['state']['player'].get('relics') or []) else 0
    def expired():
        if nodes>=budget.max_nodes:limits.add('node_limit');return True
        if (perf_counter()-started)*1000>=budget.time_ms:limits.add('time_limit');return True
        return False
    def limited(outcomes):
        if len(outcomes)<=outcome_limit:return outcomes
        limits.add('branch_limit')
        return outcomes[:outcome_limit]+[(sum(p for p,_ in outcomes[outcome_limit:]),None)]
    def weighted(outcomes,probability,method='exact'):
        values=[]
        for p,value in outcomes:
            if method=='sampled':value=dict(value or {'ledger_matches':False},probability_method='sampled')
            values.append((probability*p,value))
        return values
    def leaf(state):
        if state.hp>0 and (state.combat_won or all(e.hp<=0 for e in state.enemies)) and victory_heal:
            gained=min(victory_heal,state.max_hp-state.hp)
            state=replace(state,hp=state.hp+gained,healing=state.healing+gained)
        value=evaluate_outcome(initial,state,horizon,policy)
        remaining={p.instance_id for p in state.potions}
        used=[a for a,instance in potion_map.items() if instance not in remaining]
        known=all(costs[a]['known'] for a in used)
        value.update(consumed_potions=used,
                     reserve_lower=sum(costs[a]['lower'] for a in used) if known else None,
                     reserve_upper=sum(costs[a]['upper'] for a in used) if known else None)
        return [(1.0,value)]
    def score(outcomes):
        val=aggregate(outcomes,policy,'sampled' if any(v and v.get('probability_method')=='sampled' for _,v in outcomes) else 'exact')
        observed=[(p,v) for p,v in outcomes if v is not None and v.get('ledger_matches')]
        reserves_known=observed and all(v['reserve_lower'] is not None for _,v in observed)
        mass=sum(p for p,_ in observed)
        low=sum(p*v['reserve_lower'] for p,v in observed)/mass if reserves_known else None
        high=sum(p*v['reserve_upper'] for p,v in observed)/mass if reserves_known else None
        val.update(reserve_lower=low,reserve_upper=high,
                   consumed_potions=sorted({a for _,v in observed for a in v['consumed_potions']}),
                   comparison_utility=val['Q_hat']-(low+high)/2 if val['Q_hat'] is not None and low is not None else None)
        return val
    def choose(values):
        feasible=[(score(v),v) for v in values]
        feasible=[(s,v) for s,v in feasible if s['Q_hat'] is not None]
        if not feasible:
            sampled=any(v and v.get('probability_method')=='sampled' for valueset in values for _,v in valueset)
            return [(1.0,{'ledger_matches':False,'probability_method':'sampled'} if sampled else None)]
        # Survival is a constraint, not a finite penalty which damage can cancel.
        safe=[pair for pair in feasible if pair[0]['complete'] and pair[0]['death_upper']==0]
        ranked=safe or feasible
        ranked.sort(key=lambda pair: pair[0]['comparison_utility'] if pair[0]['comparison_utility'] is not None else float('-inf'),reverse=True)
        return ranked[0][1]
    def enemy_turn(state,ply):
        # Unsupported end-of-turn hooks cannot disappear into a discard operation.
        sim=CombatSimulator(state,rules)
        if any(c.instance_id in sim.unsupported and c.entity_id!='CARD.ASCENDERS_BANE' for c in state.hand):
            raise UnsupportedEffect('unsupported_remaining_card_end_turn_hooks')
        discarded=[c.variant_key or c.entity_id for c in state.hand if c.entity_id!='CARD.ASCENDERS_BANE']
        exhausted=[c.variant_key or c.entity_id for c in state.hand if c.entity_id=='CARD.ASCENDERS_BANE']
        state=replace(state,hand=(),discard_composition=add_to_composition(state.discard_composition,discarded),
                      exhausted_composition=add_to_composition(state.exhausted_composition,exhausted))
        outcomes=[]
        for probability,after,_ in model.step(state,limit,select_next=ply<horizon):
            if after.hp<=0 or after.combat_won or ply>=horizon:
                next_values=leaf(after)
            else:
                if after.effect_draw_blocked:raise UnsupportedEffect('future_draw_prevention_duration_unknown')
                relics=after.periodic_relics;draw_bonus=energy_bonus=0
                if relics:
                    if any(r.counter is None for r in relics):raise UnsupportedEffect('unknown_periodic_counter')
                    relics,draw_bonus,energy_bonus=advance(relics)
                after=replace(after,hand=(),block=0,energy=future_energy+energy_bonus,
                              base_strength=after.base_strength+after.strength_delta-after.temporary_strength,
                              strength_delta=0,temporary_strength=0,colossus=False,legion_ready=False,
                              periodic_relics=relics,turn=(after.turn or 0)+1,
                              player_weak=after.weak_turns>0,player_frail=after.frail_turns>0,
                              enemies=tuple(replace(e,vulnerable=max(0,e.vulnerable-1)) for e in after.enemies))
                meta={};branches,_=draw_branches(after,future_draw+draw_bonus,templates,limit,seed+ply,meta)
                if meta:draw_metadata.append(meta)
                next_values=[]
                for weight,drawn in branches:
                    values=visit(Transition(drawn),ply+1,0) if drawn is not None else [(1.0,None)]
                    next_values.extend(weighted(values,weight,meta.get('method','exact')))
                if not branches:next_values=[(1.0,None)]
            outcomes.extend((probability*p,v) for p,v in next_values)
        return limited(outcomes)
    def visit(transition,ply,depth):
        nonlocal nodes,hits
        key=(transition,ply,depth,active_depth)
        if key in memo:hits+=1;return memo[key]
        state=transition.state
        if state.hp<=0 or state.combat_won or all(e.hp<=0 for e in state.enemies):return leaf(state)
        if expired():return [(1.0,None)]
        nodes+=1
        try:
            if transition.boundary=='draw_requires_observation':
                meta={};branches,_=draw_branches(state,transition.draw_count,templates,limit,seed+ply,meta)
                if meta:draw_metadata.append(meta)
                values=[];sim=CombatSimulator(state,rules)
                for weight,drawn in branches:
                    result=visit(sim.resume_draw(transition,drawn),ply,depth) if drawn is not None else [(1.0,None)]
                    values.extend(weighted(result,weight,meta.get('method','exact')))
                result=limited(values) if values else [(1.0,None)]
            elif transition.boundary=='next_turn_requires_observation':
                result=enemy_turn(state,ply)
            elif transition.boundary:
                raise UnsupportedEffect(transition.boundary)
            else:
                alternatives=[enemy_turn(state,ply)]
                sim=CombatSimulator(state,rules)
                actions=[a for a in sim.actions(state) if a.action_id!='end_turn']
                if depth>=active_depth and actions:
                    limits.add('depth_limit');result=choose(alternatives)
                    # A feasible lower estimate is preserved, never an upper bound.
                else:
                    for action in sorted(actions,key=lambda a:(a.action_id,a.target_id or '')):
                        edge=(state,action)
                        if edge in edge_cache:hits+=1
                        else:edge_cache[edge]=sim.apply(state,action)
                        alternatives.append(visit(edge_cache[edge],ply,depth+1))
                    result=choose(alternatives)
            memo[key]=result
            return result
        except (UnsupportedEffect,KeyError,ValueError,SyntaxError) as exc:
            problems.add(str(exc));limits.add('mechanics_boundary');return [(1.0,None)]
    legal=operations([a for a in snapshot['state'].get('legal_actions') or [] if a.get('type') not in ADMIN|{'discard_potion'}])
    simulator=CombatSimulator(prepared,rules)
    modeled=set(simulator.actions(prepared))|{Operation('end_turn')}
    rows=[]
    # Round-robin root seeding prevents a long first branch from hiding another action.
    roots=[]
    for op in sorted(legal,key=lambda a:(a['action_id'],a['target_id'] or '')):
        action=Operation(**op)
        try:
            if action not in modeled:raise UnsupportedEffect('first_step_not_modeled')
            transition=Transition(prepared,'next_turn_requires_observation') if action.action_id=='end_turn' else simulator.apply(prepared,action)
            roots.append((op,transition,None))
        except (UnsupportedEffect,ValueError) as exc:roots.append((op,None,str(exc)))
    stages=[];incumbents={}
    for active_depth in range(1,budget.max_depth+1):
        limits.clear();stage_rows=[]
        for op,transition,reason in roots:
            before=set(limits);values=visit(transition,1,1) if transition is not None else [(1.0,None)]
            row=score(values)
            key=(op['action_id'],op['target_id'])
            incumbent=incumbents.get(key)
            value=row.get('comparison_utility');old=incumbent.get('comparison_utility') if incumbent else None
            if old is not None and (value is None or old>value):row=dict(incumbent)
            elif value is not None:incumbents[key]=dict(row)
            row.update(first_step=op,reasons=[reason] if reason else sorted(limits-before),
                       unsearched_alternatives=1 if expired() else 0)
            stage_rows.append(row)
        rows=stage_rows
        stages.append(dict(depth=active_depth,nodes=nodes,reasons=sorted(limits)))
        if 'depth_limit' not in limits or expired():break
    identity=dict(game_version=snapshot.get('game_version'),game_assembly_sha256=snapshot.get('game_assembly_sha256'),
                  knowledge_revision=knowledge_revision,
                  policy_fingerprint=digest(dict(policy=asdict(combat_policy),settings=settings,module=VERSION)))
    from pathlib import Path
    package=Path(__file__).resolve().parents[1]
    identity['model_fingerprint']=digest({str(p.relative_to(package)):p.read_text(encoding='utf-8-sig')
        for folder in ('decision','rules','planning','simulation','evaluation','probability','calibration')
        for p in (package/folder).glob('*.py')})
    calibration_reasons=calibrate(rows,calibration,identity,state_class(snapshot))
    # Global incomplete search invalidates all comparative bounds, even if one
    # completed continuation happens to have a good model score.
    search_complete=not (limits & {'node_limit','time_limit','depth_limit','mechanics_boundary','branch_limit'}) and all(r['complete'] for r in rows)
    global_upper=None
    if not search_complete and not problems and not calibration_reasons and not initial.warnings:
        # A narrow closed universe: no unmodeled hooks, HP/max-HP generators,
        # unknown card variants or unpriced consumables. Never applies to a
        # mechanics boundary. HP can rise only through the reviewed victory heal.
        allowed_relics={'RELIC.BURNING_BLOOD','RELIC.MANGO','RELIC.LARGE_CAPSULE','RELIC.POTION_BELT'}
        closed=all(r['entity_id'] in allowed_relics for r in snapshot['state']['player'].get('relics') or [])
        needed={k for k,n in initial.draw_composition+initial.discard_composition if n}
        needed.update(c.variant_key or c.entity_id for c in initial.hand)
        closed=closed and needed<=templates.keys() and all(v['known'] for v in costs.values())
        try:
            from autoslay.rules.monsters import unmodeled_special_hooks
            from autoslay.rules.mechanism_scope import reviewed_move_scope
            if not model.compatible:closed=False
            for enemy in prepared.enemies:
                entry=model.entry(enemy)
                if unmodeled_special_hooks(entry):closed=False
                for move in entry.get('moves',[]):
                    model.ordered_effects(enemy,entry,move)
                    reviewed=reviewed_move_scope(entry,move) or {}
                    writes=set(reviewed.get('ignored_state_updates',[])+reviewed.get('initialized_fields',[]))
                    if any(u['variable'] not in writes for u in move.get('state_updates',[])):closed=False
            for card in templates.values():
                if card.entity_id=='CARD.ASCENDERS_BANE':continue
                effects=rules.card(card,initial).effects
                if any(e.kind not in {'damage','unpowered_damage','block','unpowered_block','multiply_block',
                                     'vulnerable','double_vulnerable','enemy_strength','strength','temporary_strength',
                                     'lose_hp','energy','exhaust_nonattacks','colossus','draw'} for e in effects):closed=False
        except (UnsupportedEffect,KeyError,ValueError,SyntaxError):closed=False
        if closed:
            weight=policy.hp_weight*(1+policy.hp_sensitivity*(1-initial.hp/max(1,initial.max_hp))**2)
            global_upper=max(policy.finish_weight+weight*min(victory_heal,initial.max_hp-initial.hp),
                             policy.discount**horizon*policy.strength_value_cap)
    if not search_complete:
        if global_upper is None:
            for row in rows:row.update(lower=None,upper=None,bound_kind='unknown_unsearched_alternatives')
        else:
            cohort=calibration['cohorts'][state_class(snapshot)]
            for row in rows:
                # A complete chosen trajectory is only a feasible lower value;
                # it does not cover continuations skipped by the search budget.
                row.update(upper=global_upper+cohort['radius'],
                           bound_kind='assumption_upper_bound_with_empirical_error')
                if not row['complete']:
                    row.update(lower=None)
    candidates=[r for r in rows if r.get('comparison_utility') is not None and r['death_upper']==0]
    recommended=max(candidates,key=lambda r:r['comparison_utility'],default=None)
    return dict(module_version=VERSION,policy=asdict(policy),policy_identity=identity,
                state_class=state_class(snapshot),horizon=horizon,first_actions=rows,
                suggested_first_step=recommended['first_step'] if recommended else None,
                potion_costs=costs,potion_comparisons=compare_pairs(rows,costs),
                search_complete=search_complete,nodes=nodes,cache_hits=hits,
                comparison_bounds_complete=all(r.get('upper') is not None for r in rows),
                unsearched_global_model_upper=global_upper,
                global_bound_assumptions=['closed reviewed effect universe; no HP/max-HP generation except victory heal',
                                          'terminal Strength capped; other terminal resource components nonpositive'] if global_upper is not None else [],
                budget_stages=stages,
                truncation_reasons=sorted(limits),problems=sorted(problems),draw_distributions=draw_metadata,
                calibration_version=(calibration or {}).get('fingerprint'),calibration_reasons=calibration_reasons,
                elapsed_ms=(perf_counter()-started)*1000,execution_authorized=False,
                assumptions=['finite horizon; future base energy/draw explicitly configured',
                             'unknown effects and missing branch mass never certify safety',
                             'empirical calibration intervals may fail to cover; they are not proofs'])
