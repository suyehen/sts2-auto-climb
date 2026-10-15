"""Explicit whole-run HP opportunity proxies and overlapping deck capabilities."""
from collections import deque
from autoslay.evaluation.run import card_features
from autoslay.rules.registry import clean
import re

VERSION='1.0.0'
DEFAULTS=dict(hp_weight=2.0,hp_sensitivity=3.0,rest_opportunity=8.0,
              reserve_fraction=0.4,unknown_penalty=3.0,tie_margin=1.0,
              terminal_enemy_hp_weight=0.4,death_penalty=10000.0)


def capabilities(snapshot,entries):
    player=snapshot['state']['player'];cards=player.get('deck') or player.get('hand') or []
    features=[card_features(c,entries) for c in cards]
    n=max(1,len(features));attacks=sum(f.get('type')=='Attack' for f in features)
    healing=[];unknown=[]
    for raw,f in zip(cards,features):
        text=clean(raw.get('description',''))
        match=re.search(r'(?:回复|恢复|Heal\s+)(\d+)(?:点生命|\s+HP)',text,re.I)
        if match:healing.append(dict(entity_id=raw['entity_id'],amount=int(match[1]),basis='visible text; trigger/availability evaluated per action'))
        if f.get('warnings'):unknown.append(raw['entity_id'])
    relic_heal=6 if any(r['entity_id']=='RELIC.BURNING_BLOOD' for r in player.get('relics') or []) else 0
    return dict(cards=len(cards),attack_ratio=attacks/n,
        damage_per_draw=sum(f.get('damage',0) for f in features)/n,
        block_per_draw=sum(f.get('block',0) for f in features)/n,
        persistent_strength_per_draw=sum(f.get('strength',0) for f in features)/n,
        extra_draw_per_draw=sum(f.get('draw',0) for f in features)/n,
        energy_per_draw=sum(f.get('energy',0) for f in features)/n,
        attack_segments_per_draw=sum(f.get('hits',0) for f in features)/n,
        energy_demand_per_draw=sum(max(0,f.get('cost',0)) for f in features)/n,
        cheap_ratio=sum(0<=f.get('cost',-1)<=1 for f in features)/n,
        healing_cards=healing,victory_heal= relic_heal,unknown_card_ids=sorted(set(unknown)),
        basis='overlapping capabilities, not a fixed archetype or guaranteed draw')


def route_context(snapshot,parameters):
    view=snapshot['state'].get('map') or {};nodes={n['node_id']:n for n in view.get('nodes',[]) if n.get('node_id')}
    current=view.get('current_position');origin=next((n for n in nodes.values() if n.get('coordinate')==current),None)
    starts=origin.get('children',[]) if origin else [n['node_id'] for n in nodes.values() if n.get('available')]
    queue=deque((k,1) for k in starts);seen=set();camp=None;pressure=0
    while queue and len(seen)<128:
        key,distance=queue.popleft()
        if key in seen or key not in nodes or distance>4:continue
        seen.add(key);node=nodes[key]
        if node.get('type')=='RestSite':camp=distance if camp is None else min(camp,distance)
        if node.get('type') in {'Elite','Boss'}:pressure=max(pressure,1/distance)
        queue.extend((k,distance+1) for k in node.get('children',[]))
    return dict(reserve_fraction=min(.7,parameters['reserve_fraction']+.12*pressure),
        nearest_possible_rest=camp,pressure=pressure,
        rest_weight=parameters['rest_opportunity']*(1 if camp is None else 1+1/camp),
        basis='visible reachable rooms within four steps; route choice and future damage are estimates')


def hp_value(hp,maximum,parameters,route):
    """One HP value; rest opportunity is embedded here, never charged twice."""
    maximum=max(1,maximum);hp=max(0,min(maximum,hp));missing=maximum-hp
    threshold=maximum*route['reserve_fraction']
    return parameters['hp_weight']*(hp-parameters['hp_sensitivity']*missing**3/(3*maximum**2)) - route['rest_weight']*max(0,threshold-hp)/max(1,threshold)


def resources_value(initial,state,profile,parameters,route,won=False):
    # Apply the same capped victory heal to BOTH alternatives, not free damage.
    heal=min(profile['victory_heal'],state.max_hp-state.hp) if won and state.hp>0 else 0
    baseline_heal=min(profile['victory_heal'],initial.max_hp-initial.hp) if won else 0
    delta=hp_value(state.hp+heal,state.max_hp,parameters,route)-hp_value(initial.hp+baseline_heal,initial.max_hp,parameters,route)
    return dict(run_hp_value=delta,actual_trajectory_healing=state.healing-initial.healing,
                victory_heal=heal,baseline_victory_heal=baseline_heal,
                projected_hp_after_heal=state.hp+heal,rest_opportunity_included=True)


def future_pressure_value(state,profile,parameters,route):
    """Bounded deck-capability proxy beyond simulated horizon; not a next-move promise."""
    alive=[e for e in state.enemies if e.hp>0]
    utilization=min(1,3/max(1,5*profile['energy_demand_per_draw']))
    strength=state.base_strength+state.strength_delta-state.temporary_strength
    output=max(1,(profile['damage_per_draw']+strength*profile['attack_segments_per_draw'])*5*utilization)
    defense=profile['block_per_draw']*5*utilization
    if state.player_weak:output*=.75
    if state.player_frail:defense*=.75
    cycles=min(4,sum(e.hp+e.block for e in alive)/output)
    incoming=sum(sum(e.attacks) for e in alive)
    estimated_loss=max(0,incoming-defense)*cycles
    cost=hp_value(max(0,state.hp-estimated_loss),state.max_hp,parameters,route)-hp_value(state.hp,state.max_hp,parameters,route)
    return dict(estimated_future_hp_cost=cost*.5,estimated_turns_to_finish=cycles,
                sustainable_damage_proxy=output,sustainable_block_proxy=defense,
                basis='discounted capability/pressure proxy beyond horizon; no exact repeated-intent assumption')
