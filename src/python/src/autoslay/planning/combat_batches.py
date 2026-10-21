"""Heuristic operation batches to observation/goal boundaries. Never a proof.

Forecasts deliberately retain unknown-hook assumptions. Execution validates each
step, and never dispatches actions from hypothetical draw branches.
"""
from collections import deque
from dataclasses import asdict,replace
from time import perf_counter
from autoslay.decision.contracts import Operation,Transition,UnsupportedEffect
from autoslay.decision.cache import digest
from autoslay.decision.normalize import normalize
from autoslay.evaluation.combat_resources import DEFAULTS,capabilities,route_context,resources_value,future_pressure_value,hp_value
from autoslay.evaluation.potion_value import reserve_cost
from autoslay.rules.registry import KnowledgeRules
from autoslay.planning.lookahead import templates_for,draw_branches
from autoslay.rules.monsters import MonsterRules
from autoslay.simulation.engine import CombatSimulator
from autoslay.simulation.card_flow import add_to_composition
from autoslay.rules.statuses import preserve_block,hand_draw_bonus,next_turn_energy
from autoslay.rules.periodic_relics import advance
from autoslay.evaluation.strategic import ValueContract, RunResources, resource_value, summarize_outcomes

VERSION='1.5.0'


def endpoint_key(transition):
    """Interchangeable observed card instances do not occupy separate Top-K slots."""
    state=asdict(transition.state)
    state['hand']=sorted([{k:v for k,v in c.items() if k!='instance_id'} for c in state['hand']],key=str)
    pending=asdict(transition.pending_card) if transition.pending_card else None
    if pending:pending.pop('instance_id')
    return digest(dict(state=state,boundary=transition.boundary,resume_effects=transition.resume_effects,
                       draw_count=transition.draw_count,pending_card=pending,pending_exhaust=transition.pending_exhaust,
                       target_ids=transition.effect_target_ids))


def forecast_state(state):
    """Isolated approximation only; strict simulator/coverage semantics unchanged."""
    return replace(state,warnings=(),mechanism_scope=())


def analyze(snapshot,entries,policy,settings,budget):
    from autoslay.decision.deterministic import operations,ADMIN
    started=perf_counter();parameters=DEFAULTS|getattr(policy,'batch',{})
    profile=capabilities(snapshot,entries);route=route_context(snapshot,parameters)
    legal=[Operation(**op) for op in operations([a for a in snapshot['state'].get('legal_actions') or []
            if a.get('type') not in ADMIN|{'discard_potion'}])]
    assumptions=set();limits=set();nodes=0;memo={};edges={};candidates={};horizon=settings.get('horizon',2)
    sampling_events=[]
    try:observed=normalize(snapshot);initial=forecast_state(observed);assumptions.update(observed.warnings)
    except (ValueError,KeyError,TypeError) as exc:
        return fallback_report(snapshot,legal,parameters,profile,route,'normalization:'+str(exc))
    for creature in [snapshot['state']['player'].get('creature') or {},*(snapshot['state'].get('enemies') or [])]:
        if creature.get('hidden_power_ids')!=[]:assumptions.add('hidden_power_coverage_unknown:'+str(creature.get('instance_id','player')))
    model=MonsterRules(snapshot,entries);prepared=model.prepare(initial);assumptions.update(model.notes)
    # Old snapshots may have a unique first move when captured at turn one.
    # This is an explicitly labeled scenario, never a source of strict proof.
    inferred=[]
    for enemy in prepared.enemies:
        if not enemy.move_state and initial.turn==1:
            try:
                entry=model.entry(enemy);choices=entry['state_graph'].get('initial_choices',[])
                if len(choices)==1 and not choices[0].get('conditions'):
                    move=model.moves(entry).get(choices[0]['target'])
                    if move and model.attacks(enemy,entry,move)==enemy.attacks:
                        enemy=replace(enemy,move_state=move['variable'],move_history=(move['variable'],),history_complete=True)
                        assumptions.add('inferred_first_move_from_turn_one:'+enemy.entity_id)
            except (UnsupportedEffect,KeyError):pass
        inferred.append(enemy)
    initial=replace(prepared,enemies=tuple(inferred));rules=KnowledgeRules();templates=templates_for(snapshot,entries)
    for identity in model.generated_ids:
        if identity not in templates:
            from autoslay.knowledge_base.templates import canonical
            try:templates[identity]=replace(canonical(dict(entity_id=identity,upgrade_level=0),entries)[0],canonical=True)
            except ValueError:assumptions.add('unknown_generated_card:'+identity)
    costs={p.action_id:reserve_cost(snapshot,p,policy.potion_reserve,entries=entries) for p in initial.potions}
    potion_ids={p.action_id:p.instance_id for p in initial.potions}
    public_actions={a['action_id']:a for a in snapshot['state'].get('legal_actions') or []}
    search_started=perf_counter()
    candidate_node_limit=budget.max_nodes;candidate_deadline=search_started+budget.time_ms/1000
    quick_calls=cache_hits=0
    contract=ValueContract(valuation_inputs_sha256=digest(dict(parameters=parameters,route=route)))
    player=snapshot['state']['player']
    owned_counters=tuple((r['entity_id'],r.get('counter')) for r in player.get('relics') or [])
    initial_resources=RunResources(initial.hp,initial.max_hp,player.get('gold',0),
        tuple(p.instance_id for p in initial.potions),
        owned_counters,dead=initial.hp==0)
    reference=resource_value(initial_resources,parameters,route,contract)
    def simulated(sim,state,action):
        card=next((c for c in state.hand if action.action_id=='play_'+c.instance_id),None)
        modeled=Operation(action.action_id) if card and card.target_type in {'Self','None','AllEnemies'} else action
        return sim.apply(state,modeled)
    def executable_actions(sim,state):
        values=[]
        for action in sim.actions(state):
            raw=public_actions.get(action.action_id)
            if action.target_id is None and raw and not raw.get('allows_no_target'):
                values.extend(Operation(action.action_id,t) for t in raw.get('allowed_target_ids') or [])
            else:values.append(action)
        return values
    def expired():
        if nodes>=candidate_node_limit:limits.add('candidate_node_limit');return True
        if perf_counter()>=candidate_deadline:limits.add('candidate_time_limit');return True
        return False
    def terminal(state,unknown=0):
        won=state.combat_won or all(e.hp<=0 for e in state.enemies)
        resource=resources_value(initial,state,profile,parameters,route,won)
        parts=dict(run_hp_value=resource['run_hp_value'])
        alive=[] if won else [e for e in state.enemies if e.hp>0]
        used=[a for a,i in potion_ids.items() if i not in {p.instance_id for p in state.potions}]
        reserve=sum((costs[a]['lower']+costs[a]['upper'])/2 if costs[a]['known'] else 20 for a in used)
        future=future_pressure_value(state,profile,parameters,route) if alive else dict(estimated_future_hp_cost=0)
        if alive and not any(e.attacks for e in alive):
            try:
                # Peek one nonattack move for the terminal pressure proxy only.
                # No hypothetical HP/healing is added to the actual trajectory.
                peeks=model.step(state,settings.get('branches',32),select_next=True)
                future=dict(future,estimated_future_hp_cost=sum(p*future_pressure_value(
                    replace(state,enemies=after.enemies,player_weak=after.weak_turns>0,player_frail=after.frail_turns>0),
                    profile,parameters,route)['estimated_future_hp_cost'] for p,after,_ in peeks),
                    basis='capability proxy including one known nonattack follow-up')
            except (UnsupportedEffect,ValueError,KeyError,SyntaxError):pass
        parts['estimated_future_hp_cost']=future['estimated_future_hp_cost']
        parts.update(finish=policy.short_term.get('finish_weight',50)*won,
            remaining_enemy=-parameters['terminal_enemy_hp_weight']*sum(e.hp for e in alive),
            enemy_growth=0,player_growth=0,
            pollution=-sum(n for k,n in state.draw_composition+state.discard_composition if any(s in k for s in ('SLIMED','DAZED','WOUND','STATUS'))),
            potion_reserve=-reserve,unknown=-unknown*parameters['unknown_penalty'],
            death_penalty=-parameters['death_penalty'] if state.hp<=0 else 0)
        score=sum(parts.values())
        return dict(score=score,death=float(state.hp<=0),loss=max(0,initial.hp-state.hp),parts=parts,unknown=unknown,used=used,
                    resources=resource,future_pressure=future,
                    outcomes=[(1.0,state,unknown,won,'deterministic_leaf')],expected_used=float(len(used)),worst_used=len(used))
    def combine(values,method='exact',fallback=None):
        mass=sum(p for p,_ in values)
        if not mass:return terminal(initial if fallback is None else fallback,1)
        # Every unknown branch receives a heuristic continuation, never dropped.
        if abs(mass-1)>1e-8:raise ValueError('Forecast probability mass must be preserved')
        leaves={}
        for p,value in values:
            if not p:continue
            for weight,state,unknown,won,source in value['outcomes']:
                source=('sampled_or_mixed_tree' if method!='exact' or source=='sampled_or_mixed_tree'
                        else 'weighted_model_tree' if len(values)>1 else source)
                key=(state,unknown,won,source)
                leaves[key]=leaves.get(key,0)+p*weight
        return dict(score=sum(p*v['score'] for p,v in values)/mass,
            death=sum(p*v['death'] for p,v in values)/mass,loss=max(v['loss'] for p,v in values),
            parts={k:sum(p*v['parts'][k] for p,v in values)/mass for k in values[0][1]['parts']},
            unknown=max(v['unknown'] for p,v in values)+(method!='exact'),
            used=sorted({a for p,v in values for a in v['used']}),
            expected_used=sum(p*v['expected_used'] for p,v in values),
            worst_used=max(v['worst_used'] for p,v in values if p>0),
            outcomes=[(weight,*key) for key,weight in leaves.items()],
            resources={k:sum(p*v['resources'][k] for p,v in values)/mass for k in values[0][1]['resources']},
            future_pressure={'basis':'capability proxy; actual future intents and draws may differ'})
    def end_turn(state,ply):
        compiled=CombatSimulator(state,rules)
        unknown_end=any(c.instance_id in compiled.unsupported for c in state.hand)
        if unknown_end:assumptions.add('unmodeled_held_card_end_turn_hooks')
        from autoslay.rules.scheduled_relics import apply_phase,before_hand_draw,after_hand_draw
        state=apply_phase(state,'side_turn_end')
        if state.hp<=0 or state.combat_won:return terminal(state,int(unknown_end))
        from autoslay.simulation.card_flow import flush_hand,refresh_retained
        try:state=flush_hand(state,rules)
        except UnsupportedEffect as exc:
            assumptions.add(str(exc));return terminal(state,1)
        try:branches=model.step(state,settings.get('branches',32),select_next=True)
        except (UnsupportedEffect,ValueError,KeyError,SyntaxError) as exc:
            assumptions.add(str(exc))
            # Visible damage is a fallback scenario, not a prediction that all
            # unknown nonattack mechanics have no effect.
            from autoslay.simulation.atomic_effects import visible_enemy_attacks
            state=visible_enemy_attacks(state)
            return terminal(state,1+int(unknown_end))
        values=[]
        for p,after,_ in branches:
            if after.hp<=0 or after.combat_won or ply>=horizon:values.append((p,terminal(after)));continue
            relics=after.periodic_relics;draw_bonus=energy_bonus=0
            if relics:
                if any(r.counter is None for r in relics):values.append((p,terminal(after,1)));continue
                relics,draw_bonus,energy_bonus=advance(relics)
            try:after=refresh_retained(after,templates)
            except UnsupportedEffect as exc:
                assumptions.add(str(exc));values.append((p,terminal(after,1)));continue
            after=replace(after,block=after.block if preserve_block(after) else 0,energy=next_turn_energy(after,settings.get('energy',3))+energy_bonus,
                base_strength=after.base_strength+after.strength_delta-after.temporary_strength,
                strength_delta=0,temporary_strength=0,cards_played=0,attacks_played=0,skills_played=0,turn_cards_exhausted=0,turn_cards_drawn=0,colossus=False,legion_ready=False,
                periodic_relics=relics,turn=(after.turn or 0)+1,player_weak=after.weak_turns>0,player_frail=after.frail_turns>0,
                enemies=tuple(replace(e,vulnerable=max(0,e.vulnerable-1)) for e in after.enemies))
            after=before_hand_draw(after)
            meta={};draw_count=settings.get('draws',5)+draw_bonus+hand_draw_bonus(after)
            drawn,_=draw_branches(after,draw_count,templates,settings.get('branches',32),settings.get('seed',42)+ply,meta,
                                 rules=rules,from_hand_draw=True)
            sampling_events.append(dict(meta,ply=ply,draw_count=draw_count))
            values.append((p,combine([(w,visit(Transition(after_hand_draw(s)),ply+1,0) if s is not None else terminal(after,1)) for w,s in drawn],meta.get('method','exact'),fallback=after)))
        result=combine(values,fallback=state)
        if unknown_end:
            result=dict(result,parts=dict(result['parts']),unknown=result['unknown']+1)
            result['parts']['unknown']-=parameters['unknown_penalty'];result['score']-=parameters['unknown_penalty']
        return result
    quick_cache={}
    def quick(state):
        nonlocal quick_calls,cache_hits
        quick_calls+=1
        if state in quick_cache:cache_hits+=1;return quick_cache[state]
        try:
            result=combine([(p,terminal(after,1)) for p,after,_ in model.step(state,settings.get('branches',32),select_next=True)],fallback=state)
        except (UnsupportedEffect,ValueError,KeyError,SyntaxError) as exc:
            assumptions.add(str(exc))
            from autoslay.simulation.atomic_effects import visible_enemy_attacks
            result=terminal(visible_enemy_attacks(state),1)
        quick_cache[state]=result;return result
    def visit(transition,ply,depth):
        nonlocal nodes,cache_hits
        state=transition.state;key=(transition,ply,depth)
        if key in memo:cache_hits+=1;return memo[key]
        if state.hp<=0 or state.combat_won or all(e.hp<=0 for e in state.enemies):return terminal(state)
        if expired():return quick(state)
        nodes+=1
        if transition.boundary=='generated_cards_requires_observation':
            try:result=visit(CombatSimulator(state,rules).resume_generated(transition),ply,depth)
            except (UnsupportedEffect,ValueError,KeyError):result=quick(state)
        elif transition.boundary=='draw_requires_observation':
            meta={};drawn,_=draw_branches(state,transition.draw_count,templates,settings.get('branches',32),settings.get('seed',42)+ply,meta)
            sampling_events.append(dict(meta,ply=ply,draw_count=transition.draw_count))
            sim=CombatSimulator(state,rules);values=[]
            for p,branch in drawn:
                try:value=visit(sim.resume_draw(transition,branch),ply,depth) if branch is not None else quick(state)
                except (UnsupportedEffect,ValueError,KeyError):value=quick(state)
                values.append((p,value))
            result=combine(values,meta.get('method','exact')) if values else quick(state)
        elif transition.boundary=='next_turn_requires_observation':result=end_turn(state,ply)
        elif transition.boundary:
            assumptions.add(transition.boundary)
            result=quick(state)
        else:
            values=[end_turn(state,ply)]
            if depth<min(5,budget.max_depth):
                sim=CombatSimulator(state,rules)
                for action in sim.actions(state):
                    if action.action_id=='end_turn' or expired():continue
                    try:values.append(visit(sim.apply(state,action),ply,depth+1))
                    except (UnsupportedEffect,ValueError,KeyError):assumptions.add('continuation_effect_unknown:'+action.action_id)
            else:limits.add('depth_limit')
            result=max(values,key=lambda v:(-v['death'],v['score']))
        memo[key]=result;return result
    # Enumerate batch endpoints BEFORE spending budget on hypothetical futures.
    # Equivalent final states deduplicate, without first-action grouping.
    queue=deque([(Transition(initial),())]);seen={};frontier=[];generation_nodes=0
    generation_deadline=search_started+budget.time_ms*.0004
    while queue and generation_nodes<max(1,budget.max_nodes//2) and perf_counter()<generation_deadline:
        transition,steps=queue.popleft();state=transition.state;generation_nodes+=1
        if steps:
            boundary=transition.boundary or ('combat_victory' if state.combat_won or all(e.hp<=0 for e in state.enemies) else 'batch_goal_completed')
            key=endpoint_key(transition)
            prior=seen.get(key)
            if prior is not None and prior<=len(steps):continue
            seen[key]=len(steps);frontier.append((transition,steps,boundary))
        if transition.boundary or state.combat_won or state.hp<=0 or len(steps)>=budget.max_depth:continue
        sim=CombatSimulator(state,rules)
        possible=legal if not steps else executable_actions(sim,state)+[Operation('end_turn')]
        for action in sorted(set(possible),key=lambda a:(a.action_id,a.target_id or '')):
            try:
                edge=(state,action)
                if edge not in edges:
                    result=Transition(state,'next_turn_requires_observation') if action.action_id=='end_turn' else simulated(sim,state,action)
                    edges[edge]=result
                queue.append((edges[edge],steps+(action,)))
            except (UnsupportedEffect,ValueError,KeyError) as exc:
                if not steps:
                    assumptions.add(str(exc));frontier.append((None,(action,),'unmodeled_effect_observation'))
    if queue:limits.add('batch_generation_limit')
    # Always retain every currently legal operation even at an extreme budget.
    represented={steps[0] for _,steps,_ in frontier}
    for action in legal:
        if action in represented:continue
        try:
            transition=edges.get((initial,action))
            if transition is None:
                transition=Transition(initial,'next_turn_requires_observation') if action.action_id=='end_turn' else simulated(CombatSimulator(initial,rules),initial,action)
            key=endpoint_key(transition)
            frontier.append((transition,(action,),transition.boundary or 'batch_goal_completed'));seen[key]=1
        except (UnsupportedEffect,ValueError,KeyError):frontier.append((None,(action,),'unmodeled_effect_observation'))
    nodes=generation_nodes
    frontier.sort(key=lambda item:(item[0] is None,-len(item[1]),tuple((a.action_id,a.target_id or '') for a in item[1])))
    for candidate_index,(transition,steps,boundary) in enumerate(frontier):
        remaining=len(frontier)-candidate_index
        if settings.get('fair_candidate_budget',False):
            candidate_node_limit=nodes+max(0,(budget.max_nodes-nodes)//remaining)
            remaining_time=max(0,search_started+budget.time_ms/1000-perf_counter())
            candidate_deadline=perf_counter()+remaining_time/remaining
        before_nodes=nodes;before_quick=quick_calls;before_hits=cache_hits;candidate_started=perf_counter()
        if transition is None:
            # An unavailable action model cannot establish zero incoming loss.
            # Use the observed state's enemy-turn scenario, without inventing
            # damage, block or healing from the unsupported action's text.
            value=quick(initial);value=dict(value,parts=dict(value['parts']))
            value['score']=None;value['parts']={}
            value['unknown']+=1
        else:value=visit(transition,1,len(steps))
        row=dict(batch_id=digest([asdict(s) for s in steps])[:16],steps=[asdict(s) for s in steps],
            stop_reason=boundary,score=value['score'],utility_components=value['parts'],
            resource_forecast=value['resources'],future_pressure=value['future_pressure'],
            estimated_death_probability=value['death'],estimated_worst_hp_loss=value['loss'],
            approximation_count=value['unknown']+len(observed.warnings),consumed_potions=value['used'],
            expected_potions_used=value['expected_used'],worst_potions_used=value['worst_used'],
            actual_batch_potions=[a.action_id for a in steps if a.action_id in potion_ids],
            _outcomes=value['outcomes'],
            evaluation_budget=dict(nodes=nodes-before_nodes,quick_calls=quick_calls-before_quick,
                cache_hits=cache_hits-before_hits,elapsed_ms=(perf_counter()-candidate_started)*1000,
                horizon=horizon,node_allowance=candidate_node_limit-before_nodes),
            forecast_kind='heuristic_batch_continuation',bound_kind='uncalibrated_estimate',
            endpoint=dict(hp=transition.state.hp,energy=transition.state.energy,block=transition.state.block,
                          enemy_hp=sum(e.hp for e in transition.state.enemies)) if transition else None)
        candidates[row['batch_id']]=row
    ordered=sorted(candidates.values(),key=lambda r:(r['score'] is None,r['estimated_death_probability'], -r['score'] if r['score'] is not None else 0,r['approximation_count'], -len(r['steps']),r['batch_id']))
    priced=[r for r in ordered if r['score'] is not None]
    safe=priced[0]['estimated_death_probability'] if priced else 1
    best=priced[0]['score'] if priced else 0
    close=[r for r in priced[:budget.top_k] if r['estimated_death_probability']==safe and best-r['score']<=parameters['tie_margin']]
    selected=min(close,key=lambda r:(r['expected_potions_used'],r['worst_potions_used'],r['estimated_worst_hp_loss'],r['approximation_count'],-len(r['steps']),r['batch_id']),default=None)
    for row in ordered[:budget.top_k]:
        reporting_started=perf_counter();outcomes=[]
        for weight,state,unknown,won,probability_source in row.pop('_outcomes'):
            heal=min(profile['victory_heal'],state.max_hp-state.hp) if won and state.hp>0 else 0
            joint=RunResources(state.hp+heal,state.max_hp,player.get('gold',0),
                tuple(p.instance_id for p in state.potions),
                tuple((dict(owned_counters)|{r.entity_id:r.counter for r in state.periodic_relics}|{r.entity_id:r.counter for r in state.triggered_relics}).items()),dead=state.hp<=0)
            outcomes.append(dict(weight=weight,resources=asdict(joint),
                potions_used=len(initial.potions)-len(state.potions),contract=asdict(contract),
                boundary='post_victory_heal_pre_rewards' if won and state.hp>0 else 'dead' if state.hp<=0 else 'combat_cutoff',
                combat_state_key=digest(asdict(state)),combat_finished=bool(won),
                continuation_state=asdict(state) if not won and state.hp>0 else None,
                resource_value_delta=resource_value(joint,parameters,route,contract)-reference,
                value_scope='HP/maxHP/gold only; inventory and permanent effects unpriced',
                tail_source='none' if won or state.hp<=0 else 'legacy_capability_proxy',
                mechanism_unknown=bool(unknown or assumptions or row['approximation_count']),probability_source=probability_source))
        row['joint_outcomes']=outcomes
        row['outcome_summary']=summarize_outcomes(outcomes,contract)
        row['evaluation_budget']['reporting_ms']=(perf_counter()-reporting_started)*1000
        current=initial;trace=[asdict(current)]
        for step in row['steps']:
            transition=edges.get((current,Operation(**step)))
            if transition is None:break
            current=transition.state;trace.append(asdict(current))
        row['state_trace']=trace
    return dict(module_version=VERSION,mode='local',decision_unit='operation_batch',top_batches=ordered[:budget.top_k],
        selected_batch=selected,candidate_count=len(ordered),nodes=nodes,elapsed_ms=(perf_counter()-started)*1000,
        unpriced_actions=[r['steps'][0] for r in ordered if r['score'] is None],
        limits=sorted(limits),assumptions=sorted(assumptions),
        execution_blockers=sorted(a for a in assumptions if not a.startswith('inferred_first_move_from_turn_one:')),
        deck_capabilities=profile,run_resources=route,
        preparation_ms=(search_started-started)*1000,
        tie_margin=parameters['tie_margin'],near_best_count=len(close),execution_authorized=False,
        value_contract=asdict(contract),score_model='legacy_batch/1; shared resource values diagnostic only',
        sampling_events=sampling_events,
        budget_allocation='equal_remaining_share' if settings.get('fair_candidate_budget',False) else 'legacy_shared_budget',
        limitations=['Heuristic scores permit mistakes; no calibrated safety or optimality claim.',
                     'Unmodeled action effects remain unpriced; baseline forecasts do not predict their outcomes.'])


def fallback_report(snapshot,legal,parameters,profile,route,reason):
    rows=[dict(batch_id=digest(asdict(a))[:16],steps=[asdict(a)],stop_reason='unmodeled_effect_observation',
        score=None,approximation_count=1,
        estimated_death_probability=None,estimated_worst_hp_loss=None,consumed_potions=[],
        utility_components={},endpoint=None,bound_kind='uncalibrated_estimate') for a in legal]
    rows.sort(key=lambda r:r['batch_id'])
    return dict(module_version=VERSION,mode='local',decision_unit='operation_batch',selected_batch=None,
        top_batches=rows[:5],candidate_count=len(rows),nodes=0,limits=['mechanics_boundary'],assumptions=[reason],execution_blockers=[reason],
        unpriced_actions=[r['steps'][0] for r in rows],
        deck_capabilities=profile,run_resources=route,execution_authorized=False)
