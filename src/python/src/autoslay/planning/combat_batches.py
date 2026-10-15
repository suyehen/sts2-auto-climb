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
from autoslay.planning.lookahead import ScenarioRules,templates_for,draw_branches
from autoslay.rules.monsters import MonsterRules
from autoslay.simulation.engine import CombatSimulator
from autoslay.simulation.card_flow import add_to_composition
from autoslay.rules.periodic_relics import advance
from autoslay.rules.registry import clean
import re

VERSION='1.0.0'


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
    initial=replace(prepared,enemies=tuple(inferred));rules=ScenarioRules();templates=templates_for(snapshot,entries)
    for identity in model.generated_ids:
        if identity not in templates:
            from autoslay.knowledge_base.templates import canonical
            try:templates[identity]=replace(canonical(dict(entity_id=identity,upgrade_level=0),entries)[0],canonical=True)
            except ValueError:assumptions.add('unknown_generated_card:'+identity)
    costs={p.action_id:reserve_cost(snapshot,p,policy.potion_reserve) for p in initial.potions}
    potion_ids={p.action_id:p.instance_id for p in initial.potions}
    public_actions={a['action_id']:a for a in snapshot['state'].get('legal_actions') or []}
    from autoslay.execution.batch_models import load as model_status
    degraded=model_status().get('entities',{})
    search_started=perf_counter()
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
        if nodes>=budget.max_nodes:limits.add('node_limit');return True
        if (perf_counter()-search_started)*1000>=budget.time_ms:limits.add('time_limit');return True
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
                    resources=resource,future_pressure=future)
    def combine(values,method='exact'):
        mass=sum(p for p,_ in values)
        if not mass:return terminal(initial,1)
        # Every unknown branch receives a heuristic continuation, never dropped.
        return dict(score=sum(p*v['score'] for p,v in values)/mass,
            death=sum(p*v['death'] for p,v in values)/mass,loss=max(v['loss'] for p,v in values),
            parts={k:sum(p*v['parts'][k] for p,v in values)/mass for k in values[0][1]['parts']},
            unknown=max(v['unknown'] for p,v in values)+(method!='exact'),
            used=sorted({a for p,v in values for a in v['used']}),
            resources={k:sum(p*v['resources'][k] for p,v in values)/mass for k in values[0][1]['resources']},
            future_pressure={'basis':'capability proxy; actual future intents and draws may differ'})
    def end_turn(state,ply):
        compiled=CombatSimulator(state,rules)
        unknown_end=any(c.instance_id in compiled.unsupported and c.entity_id!='CARD.ASCENDERS_BANE' for c in state.hand)
        if unknown_end:assumptions.add('unmodeled_held_card_end_turn_hooks')
        discarded=[c.variant_key or c.entity_id for c in state.hand if c.entity_id!='CARD.ASCENDERS_BANE']
        exhausted=[c.variant_key or c.entity_id for c in state.hand if c.entity_id=='CARD.ASCENDERS_BANE']
        state=replace(state,hand=(),discard_composition=add_to_composition(state.discard_composition,discarded),
                      exhausted_composition=add_to_composition(state.exhausted_composition,exhausted))
        try:branches=model.step(state,settings.get('branches',32),select_next=True)
        except (UnsupportedEffect,ValueError,KeyError,SyntaxError) as exc:
            assumptions.add(str(exc));loss=0;block=state.block
            for enemy in state.enemies:
                if enemy.hp<=0:continue
                for hit in enemy.attacks:
                    hit=hit//2 if state.colossus and enemy.vulnerable else hit
                    absorbed=min(block,hit);block-=absorbed;loss+=max(0,hit-absorbed-state.hp_loss_reduction)
            # Visible damage is a fallback scenario, not a prediction that all
            # unknown nonattack mechanics have no effect.
            state=replace(state,hp=max(0,state.hp-loss),enemy_hp_loss=state.enemy_hp_loss+min(state.hp,loss))
            return terminal(state,1+int(unknown_end))
        values=[]
        for p,after,_ in branches:
            if after.hp<=0 or after.combat_won or ply>=horizon:values.append((p,terminal(after)));continue
            relics=after.periodic_relics;draw_bonus=energy_bonus=0
            if relics:
                if any(r.counter is None for r in relics):values.append((p,terminal(after,1)));continue
                relics,draw_bonus,energy_bonus=advance(relics)
            if after.effect_draw_blocked:values.append((p,terminal(after,1)));continue
            after=replace(after,hand=(),block=0,energy=settings.get('energy',3)+energy_bonus,
                base_strength=after.base_strength+after.strength_delta-after.temporary_strength,
                strength_delta=0,temporary_strength=0,colossus=False,legion_ready=False,
                periodic_relics=relics,turn=(after.turn or 0)+1,player_weak=after.weak_turns>0,player_frail=after.frail_turns>0,
                enemies=tuple(replace(e,vulnerable=max(0,e.vulnerable-1)) for e in after.enemies))
            meta={};drawn,_=draw_branches(after,settings.get('draws',5)+draw_bonus,templates,settings.get('branches',32),settings.get('seed',42)+ply,meta)
            values.append((p,combine([(w,visit(Transition(s),ply+1,0) if s is not None else terminal(after,1)) for w,s in drawn],meta.get('method','exact'))))
        result=combine(values)
        if unknown_end:
            result=dict(result,parts=dict(result['parts']),unknown=result['unknown']+1)
            result['parts']['unknown']-=parameters['unknown_penalty'];result['score']-=parameters['unknown_penalty']
        return result
    quick_cache={}
    def quick(state):
        if state in quick_cache:return quick_cache[state]
        try:
            result=combine([(p,terminal(after,1)) for p,after,_ in model.step(state,settings.get('branches',32),select_next=True)])
        except (UnsupportedEffect,ValueError,KeyError,SyntaxError):
            block=state.block;loss=0
            for enemy in state.enemies:
                if enemy.hp<=0:continue
                for hit in enemy.attacks:
                    hit=hit//2 if state.colossus and enemy.vulnerable else hit
                    absorbed=min(block,hit);block-=absorbed;loss+=max(0,hit-absorbed-state.hp_loss_reduction)
            result=terminal(replace(state,hp=max(0,state.hp-loss),enemy_hp_loss=state.enemy_hp_loss+min(state.hp,loss)),1)
        quick_cache[state]=result;return result
    def visit(transition,ply,depth):
        nonlocal nodes
        state=transition.state;key=(transition,ply,depth)
        if key in memo:return memo[key]
        if state.hp<=0 or state.combat_won or all(e.hp<=0 for e in state.enemies):return terminal(state)
        if expired():return quick(state)
        nodes+=1
        if transition.boundary=='draw_requires_observation':
            meta={};drawn,_=draw_branches(state,transition.draw_count,templates,settings.get('branches',32),settings.get('seed',42)+ply,meta)
            sim=CombatSimulator(state,rules);values=[]
            for p,branch in drawn:
                try:value=visit(sim.resume_draw(transition,branch),ply,depth) if branch is not None else terminal(state,1)
                except (UnsupportedEffect,ValueError,KeyError):value=terminal(state,1)
                values.append((p,value))
            result=combine(values,meta.get('method','exact'))
        elif transition.boundary=='next_turn_requires_observation':result=end_turn(state,ply)
        elif transition.boundary:result=terminal(state,1)
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
                    card=next((c for c in state.hand if action.action_id=='play_'+c.instance_id),None)
                    potion=next((p for p in state.potions if action.action_id==p.action_id),None)
                    entity=(card or potion).entity_id if card or potion else None
                    if '*' in degraded or entity in degraded:
                        result=replace(result,boundary=result.boundary or 'model_downgrade_observation')
                        assumptions.add('downgraded_model:'+str(entity))
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
            if key not in seen:
                frontier.append((transition,(action,),transition.boundary or 'batch_goal_completed'));seen[key]=1
        except (UnsupportedEffect,ValueError,KeyError):frontier.append((None,(action,),'unmodeled_effect_observation'))
    nodes=generation_nodes
    frontier.sort(key=lambda item:(item[0] is None,-len(item[1]),tuple((a.action_id,a.target_id or '') for a in item[1])))
    for transition,steps,boundary in frontier:
        if transition is None:
            value=terminal(initial,1);estimate=visible_action_score(snapshot,steps[0],profile,parameters)
            value['score']+=estimate;value['parts']['visible_action_estimate']=estimate
        else:value=visit(transition,1,len(steps))
        row=dict(batch_id=digest([asdict(s) for s in steps])[:16],steps=[asdict(s) for s in steps],
            stop_reason=boundary,score=value['score'],utility_components=value['parts'],
            resource_forecast=value['resources'],future_pressure=value['future_pressure'],
            estimated_death_probability=value['death'],estimated_worst_hp_loss=value['loss'],
            approximation_count=value['unknown']+len(observed.warnings),consumed_potions=value['used'],
            forecast_kind='heuristic_batch_continuation',bound_kind='uncalibrated_estimate',
            endpoint=dict(hp=transition.state.hp,energy=transition.state.energy,block=transition.state.block,
                          enemy_hp=sum(e.hp for e in transition.state.enemies)) if transition else None)
        candidates[row['batch_id']]=row
    ordered=sorted(candidates.values(),key=lambda r:(r['estimated_death_probability'], -r['score'],r['approximation_count'], -len(r['steps']),r['batch_id']))
    safe=ordered[0]['estimated_death_probability'] if ordered else 1
    best=ordered[0]['score'] if ordered else 0
    close=[r for r in ordered[:budget.top_k] if r['estimated_death_probability']==safe and best-r['score']<=parameters['tie_margin']]
    selected=min(close,key=lambda r:(len(r['consumed_potions']),r['estimated_worst_hp_loss'],r['approximation_count'],-len(r['steps']),r['batch_id']),default=None)
    for row in ordered[:budget.top_k]:
        current=initial;trace=[asdict(current)]
        for step in row['steps']:
            transition=edges.get((current,Operation(**step)))
            if transition is None:break
            current=transition.state;trace.append(asdict(current))
        row['state_trace']=trace
    return dict(module_version=VERSION,mode='local',decision_unit='operation_batch',top_batches=ordered[:budget.top_k],
        selected_batch=selected,candidate_count=len(ordered),nodes=nodes,elapsed_ms=(perf_counter()-started)*1000,
        limits=sorted(limits),assumptions=sorted(assumptions),deck_capabilities=profile,run_resources=route,
        preparation_ms=(search_started-started)*1000,
        tie_margin=parameters['tie_margin'],near_best_count=len(close),execution_authorized=False,
        limitations=['Heuristic scores permit mistakes; no calibrated safety or optimality claim.',
                     'Unknown effects use explicit approximations; execute only real operations to a decision boundary.'])


def visible_action_score(snapshot,action,profile,parameters):
    """One observed legal action when rule simulation is unavailable; text is a proxy."""
    player=snapshot['state'].get('player') or {};cards=player.get('hand') or []
    raw=next((c for c in cards if action.action_id=='play_'+c['instance_id']),None)
    potion=False
    if raw is None:
        raw=next((p for p in player.get('potions') or [] if p.get('use_action_id')==action.action_id),None)
        if raw is None:return 0
        potion=True
    text=clean(raw.get('description',''));score=-20 if potion else -max(0,raw.get('energy_cost',0))
    patterns=[(r'(?:造成|Deal\s+)(\d+)(?:点伤害|\s+damage)',1),
              (r'(?:获得|Gain\s+)(\d+)(?:点格挡|\s+Block)',1.5),
              (r'(?:抽|Draw\s+)(\d+)(?:张牌|\s+cards?)',3),
              (r'(?:获得|Gain\s+)(\d+)(?:点力量|\s+Strength)',3)]
    for pattern,weight in patterns:
        match=re.search(pattern,text,re.I)
        if match:score+=int(match[1])*weight
    creature=player.get('creature') or {};hp=creature.get('hp',0);maximum=creature.get('max_hp',80)
    route=route_context(snapshot,parameters)
    heal=re.search(r'(?:回复|恢复|Heal\s+)(\d+)(?:点生命|\s+HP)',text,re.I)
    if heal:score+=hp_value(min(maximum,hp+int(heal[1])),maximum,parameters,route)-hp_value(hp,maximum,parameters,route)
    loss=re.search(r'(?:失去|Lose\s+)(\d+)(?:点生命|\s+HP)',text,re.I)
    if loss:score+=hp_value(max(0,hp-int(loss[1])),maximum,parameters,route)-hp_value(hp,maximum,parameters,route)
    return score


def fallback_report(snapshot,legal,parameters,profile,route,reason):
    rows=[dict(batch_id=digest(asdict(a))[:16],steps=[asdict(a)],stop_reason='unmodeled_effect_observation',
        score=visible_action_score(snapshot,a,profile,parameters),approximation_count=1,
        estimated_death_probability=None,estimated_worst_hp_loss=None,consumed_potions=[],
        utility_components={},endpoint=None,bound_kind='uncalibrated_estimate') for a in legal]
    rows.sort(key=lambda r:(-r['score'],r['batch_id']))
    return dict(module_version=VERSION,mode='local',decision_unit='operation_batch',selected_batch=rows[0] if rows else None,
        top_batches=rows[:5],candidate_count=len(rows),nodes=0,limits=['mechanics_boundary'],assumptions=[reason],
        deck_capabilities=profile,run_resources=route,execution_authorized=False)
