"""Entry-balanced map search; loss scenarios have no invented probabilities."""
from collections import deque
from dataclasses import asdict, replace
from math import floor, isfinite
from autoslay.evaluation.strategic import RunResources, ValueContract, resource_value, settle_victory
from autoslay.evaluation.combat_resources import DEFAULTS
from autoslay.decision.cache import digest
from autoslay.evaluation.entity_effects import apply_route_effects

VERSION='2.1.0'
# Sensitivity inputs, not measured encounter losses or game rules.
SCENARIOS={'low_loss':dict(Monster=3,Elite=10,Boss=18),
           'central':dict(Monster=8,Elite=18,Boss=30),
           'high_loss':dict(Monster=15,Elite=30,Boss=45)}
ROUTE=dict(reserve_fraction=.4,rest_weight=0)


def room_transition(resources,node,losses,victory_heal=0,upgrade_value=0,relic_profiles=()):
    if resources.dead:return resources,dict(choice='absorbing_death')
    kind=node.get('type');before=resources
    note=dict(room_type=kind,model='heuristic_room_scenario',unmodeled=[])
    relic_notes=[]
    if kind in {'Monster','Elite','Boss'}:
        resources,changes=apply_route_effects(resources,'combat_start',relic_profiles);relic_notes.extend(changes)
        hp=max(0,resources.hp-losses[kind])
        resources=settle_victory(replace(resources,hp=hp,dead=hp==0),victory_heal)
        resources,changes=apply_route_effects(resources,'combat_end',relic_profiles);relic_notes.extend(changes)
        resources,changes=apply_route_effects(resources,'every_combat_reward',relic_profiles);relic_notes.extend(changes)
        if not resources.dead:
            resources=replace(resources,growth_proxy=resources.growth_proxy+{'Monster':3,'Elite':8,'Boss':10}[kind])
        note.update(hp_loss_scenario=losses[kind],victory_heal=resources.hp-hp,
                    unmodeled=['enemy identity, tactical feasibility, gold and actual rewards'])
    elif kind=='RestSite':
        resources,changes=apply_route_effects(resources,'enter_rest',relic_profiles);relic_notes.extend(changes)
        constraints={r['kind'] for p in relic_profiles for v in p['variants'] for r in v.get('restrictions',[])}
        healed=resources if 'no_heal' in constraints else replace(resources,hp=min(resources.max_hp,resources.hp+floor(resources.max_hp*.3)))
        healed,changes=apply_route_effects(healed,'rest',relic_profiles)
        upgraded=replace(resources,growth_proxy=resources.growth_proxy+upgrade_value)
        no_rest=any(r['kind']=='no_rest' for p in relic_profiles for v in p['variants'] for r in v.get('restrictions',[]))
        resources=upgraded if no_rest else max((healed,upgraded),key=lambda s:resource_value(s,DEFAULTS,ROUTE))
        if resources==healed:relic_notes.extend(changes)
        note.update(choice='heal' if resources==healed else 'upgrade_proxy',
                    unmodeled=['rest modifiers and option restrictions'])
    elif kind=='Shop':
        resources,changes=apply_route_effects(resources,'enter_shop',relic_profiles);relic_notes.extend(changes)
        note.update(affordable_gold=resources.gold,unmodeled=['inventory and purchasing policy'])
    else:note['unmodeled']=['room outcome and rewards']
    return resources,dict(note,before=asdict(before),after=asdict(resources),relic_effects=relic_notes)


def evaluate_routes(map_view,context,max_nodes=500,*,max_depth=8,relic_ids=(),scenarios=None,upgrade_value=0,relic_profiles=()):
    if type(max_nodes) is not int or max_nodes<1 or type(max_depth) is not int or max_depth<1:
        raise ValueError('Positive integer route budgets required')
    if not isfinite(upgrade_value) or upgrade_value<0:raise ValueError('Invalid upgrade proxy')
    scenarios=SCENARIOS if scenarios is None else scenarios
    if not scenarios or 'central' not in scenarios:raise ValueError('Central sensitivity scenario required')
    for losses in scenarios.values():
        if any(type(losses.get(k)) is not int or losses[k]<0 for k in ('Monster','Elite','Boss')):
            raise ValueError('Nonnegative integral scenario losses required')
    nodes={n['node_id']:n for n in map_view.get('nodes',[])}
    starts=sorted((n for n in nodes.values() if n.get('available')),key=lambda n:n['node_id'])
    initial=RunResources(context.hp,context.max_hp,context.gold,dead=context.hp==0)
    reference=resource_value(initial,DEFAULTS,ROUTE)
    # Compatibility-only fallback when no complete catalog programs were supplied.
    heal=6 if 'RELIC.BURNING_BLOOD' in relic_ids and not any(p['entity_id']=='RELIC.BURNING_BLOOD' for p in relic_profiles) else 0
    candidates=[];coverage=[];expanded=0
    for index,start in enumerate(starts):
        quota=max_nodes//len(starts)+(index<max_nodes%len(starts))
        queue=deque([([start['node_id']],{k:initial for k in scenarios},[])])
        rows=[];count=0
        while queue and count<quota:
            path,states,trace=queue.popleft();count+=1
            node=nodes[path[-1]];next_states={};step={}
            for name,losses in scenarios.items():
                next_states[name],step[name]=room_transition(states[name],node,losses,heal,upgrade_value,relic_profiles)
            trace=trace+[step]
            children=sorted(k for k in node.get('children',[]) if k in nodes and k not in path)
            if not children or len(path)>=max_depth:
                rows.append((path,next_states,trace,bool(children)))
            else:queue.extend((path+[child],dict(next_states),trace) for child in children)
        if not rows and count:rows.append((path,next_states,trace,True))
        truncated=bool(queue) or any(r[3] for r in rows)
        root_rows=[]
        for path,states,trace,tail in rows:
            values={k:resource_value(s,DEFAULTS,ROUTE)-reference for k,s in states.items()}
            root_rows.append(dict(path=path,utility=values['central'],scenario_values=values,
                utility_range=[min(values.values()),max(values.values())],expected_utility=None,
                scenario_outcomes={k:asdict(s) for k,s in states.items()},resource_trace=trace,
                action_id=start.get('action_id'),first_action_id=start.get('action_id'),
                room_types=[nodes[k].get('type') for k in path],tail_unmodeled=tail,
                scenario_deaths=[k for k,s in states.items() if s.dead],known_fatal=False,
                score_scope='central loss scenario; resource and growth proxies only'))
        root_rows.sort(key=lambda c:(-c['utility'],c['path']))
        if not root_rows:
            root_rows=[dict(path=[start['node_id']],utility=None,action_id=start.get('action_id'),
                            first_action_id=start.get('action_id'),reason='entry budget not evaluated')]
        candidates.extend(root_rows[:2]);expanded+=count
        coverage.append(dict(node_id=start['node_id'],budget=quota,nodes=count,search_truncated=truncated,evaluated=bool(count)))
    candidates.sort(key=lambda c:(c['utility'] is None,-(c['utility'] or 0),c['path']))
    return dict(module_version=VERSION,
        contract=asdict(ValueContract(valuation_inputs_sha256=digest(dict(parameters=DEFAULTS,route=ROUTE)))),
        candidates=candidates,nodes=expanded,
        entry_coverage=coverage,search_truncated=any(r['search_truncated'] or not r['evaluated'] for r in coverage),
        scenario_inputs=scenarios,scenario_probabilities=None,prediction_depth=max_depth,
        relic_profile_ids=[p['entity_id'] for p in relic_profiles],
        execution_authorized=False,assumptions=['unweighted sensitivity scenarios, not expected loss or death probability',
            'branch resources; capped victory healing once per survived combat',
            'shop inventory and unknown rewards unmodeled; no fabricated purchases',
            'growth points are proxies; outside-window continuation unmodeled',
            'catalog relic resource triggers use explicit description scenarios; conditional/tactical hooks remain unmodeled'])
