"""One contextual evaluation path for every exported card, relic and potion.

Structured terms provide explicit resource proxies. Conditional triggers and
unparsed clauses remain part of the result, not a fabricated zero contribution.
"""
from dataclasses import replace
from math import floor, isfinite
from autoslay.knowledge_base.effect_profiles import profile_for,selected_variant

VERSION='1.0.0'


def evaluate_entity(value,context,entries,policy,*,opportunities=None):
    profile=profile_for(value,entries)
    if profile is None:
        return dict(entity_id=value.get('entity_id'),utility=None,known_component_utility=None,
                    coverage='reference_missing',unpriced=['entity not in current catalog'])
    variant=selected_variant(profile,value.get('upgrade_level',0))
    if variant is None:
        return dict(entity_id=profile['entity_id'],utility=None,known_component_utility=None,
                    coverage='variant_missing',unpriced=['observed upgrade variant missing'],profile=profile)
    opportunities=opportunities or {};components=[];unpriced=[]
    if not variant['normalized_description']:unpriced.append('description_missing_or_placeholder')
    if variant['costs'].get('costs_x') or variant['costs'].get('costs_star_x'):
        unpriced.append('X resource allocation requires a concrete playable sequence')
    if (variant['costs'].get('star_cost') or 0)>0:unpriced.append('star resource opportunity cost')
    if value.get('enchantment_id') or value.get('affliction_id') or value.get('modifiers_complete') is False:
        unpriced.append('instance modifiers require joint effect evaluation')
    for clause in variant['clauses']:
        trigger=clause['trigger'];kind=trigger['kind']
        one_shot=kind in {'on_play','on_use','pickup'}
        count=1 if one_shot else opportunities.get(kind)
        if count is not None and (type(count) not in (int,float) or not isfinite(count) or count<0):
            raise ValueError('Finite nonnegative trigger opportunities required')
        uncertain=(trigger['conditional'] or trigger['limited'] or trigger['turn'] is not None
                   or trigger['period'] is not None or clause['random'] or clause['choice'])
        if uncertain:unpriced.append(dict(text=clause['text'],reason='conditional occurrence or tactical feasibility'))
        if clause['unresolved_text']:unpriced.append(dict(text=clause['unresolved_text'],reason='source-defined effect'))
        for effect in clause['effects']:
            name=effect['kind'];n=effect['amount']*effect['repeats'];points=None
            weights=dict(damage=policy.damage_weight,block=policy.block_weight,draw=policy.draw_weight,
                         energy=policy.energy_weight,strength=policy.strength_weight,vulnerable=policy.vulnerable_weight,
                         gold=policy.gold_weight,lose_gold=-policy.gold_weight,max_hp=policy.max_hp_weight,
                         lose_max_hp=-policy.max_hp_weight)
            if effect['target']=='all_creatures':
                unpriced.append(dict(effect=effect,reason='friendly and enemy outcomes require joint evaluation'))
            elif name in weights:points=n*weights[name]
            elif name=='lose_hp':points=-n*policy.hp_weight*context.urgency
            elif name=='heal':
                amount=floor(context.max_hp*n/100) if effect['scale']=='max_hp_percent' else n
                points=min(amount,context.max_hp-context.hp)*policy.hp_weight*context.urgency
            if name=='draw' and context.draw_blocked:points=0
            if name in {'max_hp','lose_max_hp'}:
                unpriced.append(dict(effect=effect,reason='max-HP change healing/clamping semantics require source adapter'))
            if points is None:unpriced.append(dict(effect=effect,reason='resource effect lacks a common value conversion'))
            components.append(dict(effect=effect,trigger=trigger,per_trigger_proxy=points,
                occurrences=count,utility=points*count if points is not None and count is not None and not uncertain else None,
                basis='contextual resource proxy; not a simulated outcome'))
            if count is None:unpriced.append(dict(trigger=kind,reason='remaining trigger opportunities unknown'))
    # Do not imply that the value of a complex effect is zero when no term is priced.
    known=[c['utility'] for c in components if c['utility'] is not None]
    complete=not unpriced and all(c['utility'] is not None for c in components)
    total=sum(known) if known or complete else None
    return dict(entity_id=profile['entity_id'],category=profile['category'],utility=total if complete else None,
        known_component_utility=total,components=components,unpriced=unpriced,
        coverage='numeric_resource_proxy' if complete else 'partial_resource_proxy' if known else 'symbolic_effect_program',
        resource_costs=variant['costs'],keywords=variant['keywords'],
        restrictions=variant.get('restrictions',[]),
        description=variant['description'],dynamic_variables=variant['dynamic_variables'],
        effect_references=variant['hover_tips'],
        clauses=variant['clauses'],target_type=variant['target_type'],usage=profile['usage'],
        scope='resource terms only; not a complete combat or whole-run value',execution_authorized=False)


def immediate_features(value,entries):
    """Known immediate terms for deck proxies, with complete unknown accounting."""
    profile=profile_for(value,entries)
    if not profile:return None
    variant=selected_variant(profile,value.get('upgrade_level',0))
    if not variant:return None
    features=dict(entity_id=value['entity_id'],cost=variant['costs'].get('energy_cost',-1),
        type=variant['card_type'],damage=0,block=0,draw=0,energy=0,strength=0,vulnerable=0,
        self_hp=0,hits=0,exhaust_nonattack_block=0,warnings=[],
        semantic_coverage=variant['coverage'],costs_x=variant['costs'].get('costs_x'),
        costs_star_x=variant['costs'].get('costs_star_x'))
    if features['cost'] is None:features['cost']=-1
    if variant['target_type']=='AllEnemies':features['area']=True
    for clause in variant['clauses']:
        if clause['trigger']['kind']!='on_play' or clause['trigger']['conditional'] or clause['random'] or clause['choice']:continue
        for effect in clause['effects']:
            name='self_hp' if effect['kind']=='lose_hp' else effect['kind']
            if name not in {'damage','block','draw','energy','strength','vulnerable','self_hp'}:continue
            if effect['target']=='all_creatures':continue
            features[name]+=effect['amount']*effect['repeats']
            if name=='damage':
                features['hits']+=effect['repeats']
                if effect['target']=='all_enemies':features['area']=True
    supported={'damage','block','draw','energy','strength','vulnerable','lose_hp'}
    complete=(variant['coverage']['fully_parsed'] and features['cost']>=0
        and not variant['costs'].get('costs_x') and not variant['costs'].get('costs_star_x')
        and (variant['costs'].get('star_cost') or 0)<=0
        and set(variant['keywords'])<={'Exhaust'}
        and all(c['trigger']['kind']=='on_play' and not c['trigger']['conditional']
                and not c['random'] and not c['choice']
                and all(e['kind'] in supported and e['target']!='all_creatures' for e in c['effects'])
                for c in variant['clauses']))
    features['semantic_resource_complete']=complete
    features['warnings']=['CATALOG_SEMANTIC_RESOURCE_PROXY:execution_adapter_not_implied']
    if not complete:features['warnings'].insert(0,'UNMODELED_CARD_VALUE:'+value['entity_id'])
    return features


def apply_route_effects(resources,trigger,profiles,*,context_profiles=(),effect_kinds=None):
    """Apply fully parsed resource terms in a declared description scenario.

    This is not an exemption for unknown engine hooks. All skipped semantics
    remain visible and the route is explicitly partial. Owned pickup effects
    are never applied again during room traversal.
    """
    notes=[]
    restrictions={r['kind'] for p in [*profiles,*context_profiles] for v in p['variants'] for r in v.get('restrictions',[])}
    for profile in profiles:
        variant=selected_variant(profile)
        if variant is None:continue
        for clause in variant['clauses']:
            event=clause['trigger']
            if event['kind']!=trigger:continue
            unsafe=(event['conditional'] or event['limited'] or event['turn'] is not None
                    or event['period'] is not None or clause['random'] or clause['choice'] or clause['unresolved_text'])
            if unsafe:
                notes.append(dict(entity_id=profile['entity_id'],trigger=trigger,status='unmodeled',text=clause['text']))
                continue
            for effect in clause['effects']:
                kind=effect['kind'];n=effect['amount']*effect['repeats'];before=resources
                if effect_kinds is not None and kind not in effect_kinds:continue
                if resources.dead:break
                if kind=='heal' and effect['target']=='self_or_declared_target':
                    amount=floor(resources.max_hp*n/100) if effect['scale']=='max_hp_percent' else n
                    if 'no_heal' not in restrictions:
                        resources=replace(resources,hp=min(resources.max_hp,resources.hp+int(amount)))
                elif kind=='gold':
                    if 'no_gold_gain' not in restrictions:resources=replace(resources,gold=resources.gold+int(n))
                elif kind=='lose_hp':
                    hp=max(0,resources.hp-int(n));resources=replace(resources,hp=hp,dead=hp==0)
                elif kind=='lose_gold' and resources.gold>=n:
                    resources=replace(resources,gold=resources.gold-int(n))
                else:
                    notes.append(dict(entity_id=profile['entity_id'],trigger=trigger,status='unmodeled',
                                      effect=effect,reason='requires tactical/resource adapter or max-HP heal semantics'))
                    continue
                notes.append(dict(entity_id=profile['entity_id'],trigger=trigger,status='description_scenario',
                                  effect=effect,hp_delta=resources.hp-before.hp,gold_delta=resources.gold-before.gold))
    return resources,notes
