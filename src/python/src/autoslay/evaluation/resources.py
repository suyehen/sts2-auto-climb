"""Declared item effects converted to explicit, limited strategy proxies."""
from dataclasses import asdict
from autoslay.decision.contracts import Potion, UnsupportedEffect
from autoslay.rules.registry import KnowledgeRules, mechanism_status
from autoslay.rules.run_commands import RunCommands
from autoslay.evaluation.strategic import RunResources

VERSION='3.0.0'


def potion_proxy(potion,context,entries,policy,*,relics=(),rules=None):
    """Nominal single-use resources, not future usability or encounter value."""
    rules=rules or KnowledgeRules(references=entries)
    components=[];unpriced=[];trace=[]
    initial=RunResources(context.hp,context.max_hp,context.gold,dead=context.hp==0,relics=tuple(relics))
    state=initial
    try:
        compiled=rules.potion(potion)
    except (UnsupportedEffect,ValueError,KeyError) as exc:
        compiled=None;unpriced.append(str(exc))
    weights=dict(damage=policy.damage_weight,unpowered_damage=policy.damage_weight,
        block=policy.block_weight,unpowered_block=policy.block_weight,draw=policy.draw_weight,
        energy=policy.energy_weight,strength=policy.strength_weight)
    commands=RunCommands(models=rules.model_set,references=entries)
    stopped=False
    for effect in compiled.effects if compiled else ():
        points=None;reason=None
        if stopped:reason='previous effect requires an unmodeled continuation'
        elif state.dead:reason='resource sequence reached death; continuation value unpriced'
        elif effect.kind in {'heal','heal_percent','gain_max_hp','lose_hp'}:
            try:
                result=commands.execute(state,dict(operation=effect.kind,amount=effect.amount),source=potion.entity_id+':on_use')
            except (UnsupportedEffect,ValueError,KeyError) as exc:
                unpriced.append(str(exc));stopped=True
                components.append(dict(effect=asdict(effect),utility=None));continue
            trace.extend(result.trace)
            if result.gaps:
                unpriced.extend(result.gaps);stopped=True
            else:
                points=((result.state.hp-state.hp)*policy.hp_weight*context.urgency
                    +(result.state.max_hp-state.max_hp)*policy.max_hp_weight)
            state=result.state
        elif effect.kind in weights:
            if compiled.target_type=='AllEnemies':reason='future enemy count and targets unobserved'
            else:points=effect.amount*effect.repeats*weights[effect.kind]
            if effect.kind=='draw' and context.draw_blocked:points=0
        else:reason='effect continuation value unpriced: '+effect.kind
        if reason:
            unpriced.append(reason);stopped=True
        components.append(dict(effect=asdict(effect),utility=points))
    known=[c['utility'] for c in components if c['utility'] is not None]
    complete=compiled is not None and bool(components) and not unpriced
    if compiled is not None and not components:unpriced.append('empty activation has no priced value')
    total=sum(known) if known else None
    return dict(entity_id=potion.entity_id,category='potion',utility=total if complete else None,
        known_component_utility=total,components=components,unpriced=unpriced,command_trace=trace,
        resource_sensitive=any(c['effect']['kind'] in {'heal','heal_percent','gain_max_hp','lose_hp'} for c in components),
        rule_fingerprint=rules.fingerprint,after=asdict(state),
        scope='nominal declared single-use resource proxy; future timing, targets and use permission unvalued',
        execution_authorized=False)


def item_value(value,context,deck,entries,policy,*,relics=()):
    potion=Potion(value.get('instance_id') or value.get('item_id') or value['entity_id'],
        value['entity_id'],value.get('description',''),value.get('action_id',''),(),
        dynamic_variables=tuple((v.get('name'),v.get('base_value')) for v in value.get('dynamic_variables') or []))
    evaluated=potion_proxy(potion,context,entries,policy,relics=relics)
    return dict(utility=evaluated['utility'],known_component_utility=evaluated['known_component_utility'],
        basis=evaluated['scope'],
        reference=entries.get(value.get('entity_id')),semantic_analysis=evaluated,
        mechanism_status=mechanism_status(value.get('entity_id')))
