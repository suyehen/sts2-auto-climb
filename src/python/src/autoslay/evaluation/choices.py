"""Separate reward/removal/upgrade comparisons sharing situational deck metrics."""
from autoslay.evaluation.run import compare_choice
from autoslay.evaluation.run import card_features,unmodeled,intrinsic_value
from autoslay.evaluation.entity_effects import entity_reference

VERSION = "1.3.0"

def cards_report(deck, offered, context, entries, *, policy=None, mode='add',relics=()):
    candidates=[dict(kind='skip', card=None, delta_utility=0, warnings=[])] if mode=='add' else []
    for value in offered:
        try:
            kwargs={'add':value} if mode=='add' else {mode:value['instance_id']}
            comparison=compare_choice(deck,context,entries,policy=policy,relics=relics,**kwargs)
            features=card_features(value,entries)
            unknown=unmodeled(features) or bool(comparison['mechanism_gaps'])
            if mode=='upgrade':
                unknown=unknown or unmodeled(card_features(dict(value,upgrade_level=value.get('upgrade_level',0)+1),entries))
            candidates.append(dict(kind=mode, card=value, delta_utility=None if unknown else comparison['delta_utility'],
                intrinsic=intrinsic_value(value,entries,policy),
                metrics=comparison['metric_deltas'],component_deltas=comparison['component_deltas'], warnings=comparison['after']['warnings'], comparison=comparison))
        except ValueError as exc:
            candidates.append(dict(kind=mode, card=value, delta_utility=None, warnings=[str(exc)]))
        candidates[-1]['semantic_analysis']=entity_reference(value,entries)
        if mode=='upgrade':
            candidates[-1]['upgraded_semantic_analysis']=entity_reference(
                dict(value,upgrade_level=value.get('upgrade_level',0)+1),entries)
    candidates.sort(key=lambda c:(c['delta_utility'] is not None,c['delta_utility'] or 0),reverse=True)
    return dict(module_version=VERSION, candidates=candidates, units='heuristic utility points',
                execution_authorized=False, limitations=['unmodeled choices remain uncertain, not automatically skipped'])
