"""Exercise native definitions through the real simulator and lifecycle dispatch."""
from dataclasses import replace
from pathlib import Path
import json
import pytest
from autoslay.paths import ROOT
from autoslay.decision.contracts import Card, CombatState, Enemy, Operation, Potion, UnsupportedEffect
from autoslay.rules.registry import (
    KnowledgeRules, EntityMechanics, EffectSpec, Trigger, ENTITY_MECHANICS,
    OPERATIONS, mechanism_status, audit,
    modifiers_supported,
)
from autoslay.rules.scheduled_relics import read_scheduled, apply_phase
from autoslay.simulation.engine import CombatSimulator


def native_rules(identity, category, triggers, variables=(), **kwargs):
    variant=dict(upgrade_level=0,description='',dynamic_variables=list(variables))
    reference=dict(entity_id=identity,category=category,**variant)
    if category=='card':reference['variants']=[variant]
    return KnowledgeRules({identity:EntityMechanics(category,tuple(triggers),**kwargs)},
                          {identity:reference})


def state(**kwargs):
    return replace(CombatState(40,80,0,3,(),(Enemy('enemy','MONSTER.TEST',20,5,0,None,()),),()),**kwargs)


def test_new_native_relic_composes_existing_mechanics_without_approval_or_source_files():
    identity='RELIC.NEW'
    effects=(EffectSpec('energy',{'variable':'Bonus'}),EffectSpec('stars',2),
             EffectSpec('heal_percent',10),EffectSpec('unpowered_block',7),
             EffectSpec('unpowered_damage',8,repeats=2))
    rules=native_rules(identity,'relic',[Trigger('side_turn_end',effects,turn=4)],
                       variables=[dict(name='Bonus',base_value=3)])
    initial=state(turn=4,scheduled_relics=read_scheduled(dict(entity_id=identity),4,rules))
    assert apply_phase(initial,'side_turn_start')==initial
    after=apply_phase(initial,'side_turn_end')
    assert (after.energy,after.stars,after.hp,after.healing,after.block)==(6,2,48,8,7)
    assert (after.enemies[0].hp,after.enemies[0].block)==(9,0)
    assert apply_phase(after,'side_turn_end')==after
    assert apply_phase(replace(initial,turn=3),'side_turn_end').energy==3


def test_victory_stops_later_scheduled_effects():
    identity='RELIC.NEW'
    rules=native_rules(identity,'relic',[Trigger('side_turn_end',
        (EffectSpec('unpowered_damage',99),EffectSpec('heal',10)))])
    initial=state(turn=1,scheduled_relics=read_scheduled(dict(entity_id=identity),1,rules))
    after=apply_phase(initial,'side_turn_end')
    assert after.combat_won and after.hp==initial.hp


@pytest.mark.parametrize('category,event',[('card','on_play'),('potion','on_use')])
def test_new_native_card_and_potion_share_existing_simulator(category,event):
    identity=category.upper()+'.NEW'
    rules=native_rules(identity,category,[Trigger(event,(
        EffectSpec('energy',2),EffectSpec('stars',3),EffectSpec('heal',4),EffectSpec('unpowered_block',8)))],
        **({'target_type':'Self'} if category=='potion' else {}))
    if category=='card':
        initial=state(hand=(Card('c',identity,1,'Self',''),));action=Operation('play_c')
    else:
        initial=state(potions=(Potion('p',identity,'','use_p',(),True),));action=Operation('use_p')
    after=CombatSimulator(initial,rules).apply(initial,action).state
    assert (after.energy,after.stars,after.hp,after.block)==(4 if category=='card' else 5,3,44,8)


def test_draw_boundary_resumes_without_repaying_cost():
    rules=native_rules('CARD.NEW','card',[Trigger('on_play',(
        EffectSpec('draw',1),EffectSpec('stars',3)))])
    initial=state(hand=(Card('c','CARD.NEW',1,'Self',''),),draw_composition=(('CARD.OTHER',2),),discard_known=True)
    sim=CombatSimulator(initial,rules);transition=sim.apply(initial,Operation('play_c'))
    assert transition.boundary=='draw_requires_observation' and transition.state.stars==0
    after=sim.resume_draw(transition,transition.state).state
    assert after.energy==2 and after.stars==3 and after.discard_composition==(('CARD.NEW',1),)


def test_target_validation_and_power_card_destination():
    rules=native_rules('CARD.NEW','card',[Trigger('on_play',(EffectSpec('damage',10),))])
    initial=state(hand=(Card('c','CARD.NEW',1,'Self',''),))
    assert 'enemy-targeting' in CombatSimulator(initial,rules).unsupported['c']
    initial=replace(initial,hand=(replace(initial.hand[0],target_type='AnyEnemy',card_type='Power'),))
    after=CombatSimulator(initial,rules).apply(initial,Operation('play_c','enemy')).state
    assert after.removed_composition==(('CARD.NEW',1),)
    assert not after.discard_composition and not after.exhausted_composition


def test_unknown_mechanic_is_explicitly_missing_and_never_implicitly_enabled():
    card=Card('c','CARD.UNKNOWN',1,'Self','Gain 9 Energy.')
    sim=CombatSimulator(state(hand=(card,)))
    assert 'Mechanic definition missing' in sim.unsupported['c']
    assert mechanism_status(card.entity_id)=='missing_mechanic'
    assert not sim.actions(sim.initial)


@pytest.mark.parametrize('spec',[
    lambda:EffectSpec('invented',1),lambda:EffectSpec('energy',1,repeats=3),
    lambda:EffectSpec('energy',True),lambda:EffectSpec('damage',1,repeats=0),
    lambda:EffectSpec('stars',{'variable':'x.__class__'}),
])
def test_invalid_native_definitions_fail_before_prediction(spec):
    with pytest.raises(ValueError):spec()


def test_live_relic_amount_overrides_catalog_without_changing_definition():
    rules=native_rules('RELIC.NEW','relic',[Trigger('side_turn_end',(EffectSpec('energy',{'variable':'Bonus'}),))],
                       variables=[dict(name='Bonus',base_value=1)])
    observed=dict(entity_id='RELIC.NEW',dynamic_variables=[dict(name='Bonus',base_value=7)])
    assert read_scheduled(observed,1,rules)[0].amount==7


def test_executable_compatibility_layer_and_maintenance_entrypoints_are_removed():
    manifest=json.loads((ROOT/'data/knowledge/manifest.json').read_text(encoding='utf8'))
    assert not {'rules','event_rules','mechanism_scope','source_audit'} & manifest['datasets'].keys()
    for path in (
        'src/python/src/autoslay/knowledge_base/rulebook.py',
        'src/python/src/autoslay/rules/program.py',
        'src/python/src/autoslay/evaluation/event_adapters.py',
        'src/python/tools/kb_admin/review_rule_proposals.py',
        'src/python/tools/kb_admin/migrate_effect_programs.py',
        'src/python/tools/kb_admin/rebind_export_metadata.py',
        'data/knowledge/rules/rulebook.json',
    ):
        assert not (ROOT/path).exists(),path


def test_mechanism_encyclopedia_documents_all_native_operations():
    data=json.loads((ROOT/'data/knowledge/mechanics/game-0.111.0.json').read_text(encoding='utf8'))
    documented={op for entry in data['entries'] for op in entry.get('operations',[])}
    assert OPERATIONS<=documented
    assert audit()['counts']['card']['defined']==sum(d.category=='card' for d in ENTITY_MECHANICS.values())


def test_passive_mechanic_reuses_normalization_without_an_entity_compatibility_record(monkeypatch):
    from test_deterministic import live
    from autoslay.decision.normalize import normalize
    identity='RELIC.NATIVE_TEST'
    import importlib
    module=importlib.import_module('autoslay.decision.normalize')
    injected=dict(ENTITY_MECHANICS)
    injected[identity]=EntityMechanics('relic',state_fields=(('hp_loss_reduction',3),))
    monkeypatch.setattr(module,'ENTITY_MECHANICS',injected)
    snapshot=live();snapshot['state']['player']['relics']=[dict(entity_id=identity)]
    observed=normalize(snapshot)
    assert observed.hp_loss_reduction==3 and not observed.warnings


def test_sharp_is_defined_for_powered_attack_mechanics_without_a_card_allowlist():
    for identity in ('CARD.BASH','CARD.STRIKE_IRONCLAD','CARD.NEW_ATTACK'):
        assert modifiers_supported(dict(entity_id=identity,type='Attack',
            enchantment_id='ENCHANTMENT.SHARP',enchantment_amount=2))
    assert not modifiers_supported(dict(type='Skill',enchantment_id='ENCHANTMENT.SHARP',enchantment_amount=2))
    assert not modifiers_supported(dict(type='Attack',enchantment_id='ENCHANTMENT.UNKNOWN',enchantment_amount=2))
