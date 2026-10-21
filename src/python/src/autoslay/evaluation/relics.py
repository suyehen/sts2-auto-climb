"""Route-conditioned relic components, kept separate from acquisition costs."""
from copy import deepcopy
from autoslay.evaluation.route import evaluate_routes
from autoslay.rules.registry import mechanism_status
from autoslay.knowledge_base.effect_profiles import profile_for
from autoslay.evaluation.entity_effects import entity_reference
from autoslay.evaluation.run import RunPolicy
from autoslay.evaluation.strategic import RunResources
from dataclasses import replace

VERSION='2.0.0'


def evaluate_relic_options(options,strategic,context,entries,policy=None):
    policy=policy or RunPolicy()
    view=deepcopy(strategic['map'])
    # Future roots describe possibilities; their old action IDs carry no authority.
    if not any(n.get('available') for n in view.get('nodes',[])):
        current=view.get('current_position')
        origin=next((n for n in view.get('nodes',[]) if current is not None and n.get('coordinate')==current),None)
        children=set(origin.get('children',[])) if origin else set()
        for node in view.get('nodes',[]):
            node['available']=node['node_id'] in children
            node.pop('action_id',None)
    owned=[r['entity_id'] for r in strategic['relics']]
    resources=strategic.get('resources') or {}
    initial=RunResources(context.hp,context.max_hp,context.gold,dead=context.hp==0,
        relics=tuple(strategic['relics']),deck=tuple(strategic.get('deck') or []),
        potions=tuple(resources.get('potions') or []),potion_capacity=resources.get('potion_capacity'))
    candidates=[];baseline=None
    for option in options:
        if not option.get('available',True) or option.get('locked') or not option.get('relic_id'):continue
        entity=option['relic_id']
        row=dict(action_id=option.get('action_id'),option_id=option.get('option_id'),relic_id=entity,
                 utility=None,score_scope='model relic resource scenarios; event costs and unresolved effects excluded',
                 reference=entries.get(entity),known_fatal=bool(option.get('will_kill_player')))
        row['mechanism_status']=mechanism_status(entity)
        profile=profile_for(dict(entity_id=entity),entries)
        row['semantic_analysis']=entity_reference(dict(entity_id=entity),entries)
        if profile is None:row['reason']='relic absent from current catalog'
        elif entity in owned:row['reason']='already owned; duplicate acquisition semantics unmodeled'
        elif not any(n.get('available') for n in view.get('nodes',[])):
            row['reason']='public future route unavailable; lifetime heal value remains unpriced'
        else:
            if baseline is None:baseline=evaluate_routes(view,context,initial_resources=initial,max_nodes=120)
            from autoslay.rules.run_commands import RunCommands
            try:
                acquired_result=RunCommands(references=entries).execute(initial,
                    dict(operation='obtain_relic',entity_id=entity),source='event')
            except ValueError as exc:
                row['reason']=str(exc);row['mechanism_gaps']=[str(exc)]
                candidates.append(row);continue
            acquired=acquired_result.state;pickup_notes=list(acquired_result.trace)
            row['mechanism_gaps']=list(acquired_result.gaps)
            changed_context=replace(context,hp=acquired.hp,max_hp=acquired.max_hp,gold=acquired.gold)
            after=evaluate_routes(view,changed_context,initial_resources=acquired,max_nodes=120)
            before_by_path={tuple(c['path']):c for c in baseline['candidates'] if c['utility'] is not None}
            row['route_components']=[]
            for candidate in after['candidates']:
                before=before_by_path.get(tuple(candidate['path']))
                if candidate['utility'] is None:continue
                if before is None:
                    # Compare the same path even if adding the relic changed Top-K.
                    path=candidate['path'];forced=deepcopy(view)
                    for node in forced['nodes']:
                        node['available']=node['node_id']==path[0]
                        node['children']=[path[path.index(node['node_id'])+1]] if node['node_id'] in path[:-1] else []
                    before=evaluate_routes(forced,context,initial_resources=initial,max_nodes=max(1,len(path)))['candidates'][0]
                row['route_components'].append(dict(path=candidate['path'],
                    scenario_deltas={k: _outcome_value(candidate['scenario_outcomes'][k])-_outcome_value(before['scenario_outcomes'][k]) for k in candidate['scenario_values']},
                    before_outcomes=before['scenario_outcomes'],after_outcomes=candidate['scenario_outcomes']))
            row.update(effect_profile='game-models/1',pickup_scenario=pickup_notes,
                       search_truncated=baseline['search_truncated'] or after['search_truncated'],
                       scenario_inputs=after['scenario_inputs'],scenario_probabilities=None,
                       reason='model resource-trigger scenarios; acquisition costs, unparsed and tactical hooks remain separate')
        candidates.append(row)
    return dict(module_version=VERSION,candidates=candidates,execution_authorized=False,
                limitations=['components are not complete option utility; never add to an existing full score',
                             'every catalog relic registered; symbolic coverage and numeric/simulation coverage differ'])


def _outcome_value(resources):
    from autoslay.evaluation.strategic import resource_value
    from autoslay.evaluation.combat_resources import DEFAULTS
    from autoslay.evaluation.route import ROUTE
    return resource_value(RunResources(**resources),DEFAULTS,ROUTE)
