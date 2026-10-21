"""Description references for display and auditing, never executable rules."""
from autoslay.knowledge_base.effect_profiles import profile_for,selected_variant

VERSION='2.0.0'


def entity_reference(value,entries):
    profile=profile_for(value,entries)
    base=dict(entity_id=value.get('entity_id'),utility=None,known_component_utility=None,
        reference_only=True,execution_authorized=False,
        scope='description reference only; no effect execution or utility estimation')
    if profile is None:
        return dict(base,coverage='reference_missing',unpriced=['entity not in current catalog'])
    variant=selected_variant(profile,value.get('upgrade_level',0))
    if variant is None:
        return dict(base,coverage='variant_missing',unpriced=['observed upgrade variant missing'],profile=profile)
    components=[]
    for clause in variant['clauses']:
        for effect in clause['effects']:
            components.append(dict(effect=effect,trigger=clause['trigger'],per_trigger_proxy=None,
                occurrences=None,utility=None,basis='unverified description reference; not a declared effect'))
    unpriced=['description parsing does not define game effects or value']
    unpriced.extend(c['unresolved_text'] for c in variant['clauses'] if c['unresolved_text'])
    return dict(base,category=profile['category'],components=components,unpriced=unpriced,
        coverage='reference_only',resource_costs=variant['costs'],keywords=variant['keywords'],
        restrictions=variant.get('restrictions',[]),description=variant['description'],
        dynamic_variables=variant['dynamic_variables'],effect_references=variant['hover_tips'],
        clauses=variant['clauses'],target_type=variant['target_type'],usage=profile['usage'])
