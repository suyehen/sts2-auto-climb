"""Rest options use common resource commands and actual upgrade combinations."""
from itertools import combinations
from dataclasses import asdict
from math import isfinite
from autoslay.evaluation.run import card_features,unmodeled
from autoslay.evaluation.events import transition_value
from autoslay.evaluation.strategic import RunResources
from autoslay.rules.run_commands import RunCommands
from autoslay.rules.selections import SelectionRules
from autoslay.decision.contracts import UnsupportedEffect

VERSION='2.1.0'

def evaluate_rest(options,deck,context,entries,policy,*,relics=()):
    candidates=[]
    initial=RunResources(context.hp,context.max_hp,context.gold,dead=context.hp==0,
        deck=tuple(deck),relics=tuple(relics))
    commands=RunCommands(references=entries)
    for option in options:
        if not option.get('available'): continue
        record=dict(option_id=option.get('option_id'),action_id=option.get('action_id'),utility=None)
        try:
            if option.get('option_id','').lower()=='heal':
                observed=option.get('heal_amount')
                if observed is not None and (type(observed) not in (int,float) or not isfinite(observed) or observed<0):
                    raise UnsupportedEffect('Invalid observed rest heal amount')
                result=commands.rest(initial,observed_heal=int(observed) if observed is not None else None)
                healed=result.state.hp-initial.hp
                priced=transition_value(initial,result.state,context,entries,policy)
                record.update(utility=priced['utility'] if not result.gaps else None,
                    healed=healed,basis='common heal command; observed amount includes modifiers',
                    command_trace=list(result.trace),mechanism_gaps=list(result.gaps),after=asdict(result.state))
            elif option.get('option_id','').lower()=='smith':
                count=option.get('smith_count')
                if type(count) is not int or not 1<=count<=10:raise UnsupportedEffect('Observed SmithCount required')
                eligible=[];unknown=[]
                for card in deck:
                    try:commands.execute(initial,dict(operation='upgrade_card',instance_id=card['instance_id']),source='REST.SMITH')
                    except (UnsupportedEffect,ValueError):continue
                    if unmodeled(card_features(card,entries)) or unmodeled(card_features(dict(card,upgrade_level=card.get('upgrade_level',0)+1),entries)):
                        unknown.append(card['instance_id']);continue
                    eligible.append(card)
                evaluated=[];truncated=False
                selection_rules=SelectionRules(commands.models,entries)
                declaration=commands.models.selections['SELECTION.REST_SITE_SMITH']
                for i,group in enumerate(combinations(eligible,min(count,len(eligible)))):
                    if not group:break
                    if i>=policy.shop_nodes:truncated=True;break
                    result=selection_rules.apply(initial,declaration,{},group)
                    state=result.state;trace=list(result.trace);gaps=list(result.gaps)
                    try:value=transition_value(initial,state,context,entries,policy)['utility']
                    except (UnsupportedEffect,ValueError) as exc:
                        value=None;gaps.append(str(exc))
                    evaluated.append(dict(instances=[c['instance_id'] for c in group],utility=value if not gaps else None,
                        known_component_utility=value,
                        command_trace=trace,mechanism_gaps=gaps,after=asdict(state)))
                evaluated.sort(key=lambda r:(r['utility'] is None,-(r['known_component_utility'] or 0)))
                complete=bool(evaluated) and not unknown and not truncated and all(r['utility'] is not None for r in evaluated)
                record.update(upgrade_combinations=evaluated,smith_count=count,unmodeled_instances=unknown,
                    comparison_complete=complete,selection_model_id=declaration['entity_id'],
                    rule_fingerprint=commands.models.fingerprint,
                    search_truncated=truncated,basis='bounded combinations using the declared selector continuation')
                if evaluated:record['known_component_utility']=evaluated[0]['known_component_utility']
                if complete:record['utility']=evaluated[0]['utility']
            if record['utility'] is None:record['reason']='option or lifecycle not fully modeled'
        except (UnsupportedEffect,ValueError,KeyError) as exc:record['reason']=str(exc)
        candidates.append(record)
    candidates.sort(key=lambda c:(c['utility'] is not None,c['utility'] or 0),reverse=True)
    return dict(module_version=VERSION,candidates=candidates,execution_authorized=False,
                limitations=['resource utility is a proxy; unsupported options and lifecycle hooks stay unranked'])
