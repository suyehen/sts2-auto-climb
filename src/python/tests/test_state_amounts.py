from dataclasses import replace
from types import SimpleNamespace
import pytest
from autoslay.decision.contracts import Card,CombatState,Enemy,Operation,UnsupportedEffect
from autoslay.rules.amounts import validate_expression,evaluate_expression
from autoslay.rules.registry import KnowledgeRules, EntityMechanics, Trigger, EffectSpec
from autoslay.simulation.engine import CombatSimulator


def state(card,**kwargs):
    return replace(CombatState(40,80,21,3,(card,),(Enemy('e','MONSTER.TEST',100,0,0,None,()),),(),discard_known=True),**kwargs)


def rules(effects,variables=()):
    definition=EntityMechanics('card',(Trigger('on_play',tuple(EffectSpec(**e) for e in effects)),))
    return KnowledgeRules({'CARD.TEST':definition},
                         {'CARD.TEST':dict(variants=[dict(upgrade_level=0,dynamic_variables=list(variables))])})


def test_state_expression_rebinds_after_energy_payment_and_hand_removal():
    card=Card('c','CARD.TEST',1,'Self','')
    initial=state(card,energy=3)
    rule=rules([dict(operation='stars',amount=dict(add=[dict(state='energy'),dict(state='hand_count')]))])
    sim=CombatSimulator(initial,rule)
    after=sim.apply(initial,Operation('play_c')).state
    assert after.energy==2 and after.stars==2


def test_dynamic_damage_uses_updated_block_and_current_strength_at_execution():
    card=Card('c','CARD.TEST',1,'AnyEnemy','')
    rule=rules([dict(operation='damage',amount=dict(add=[dict(variable='CalculationBase'),
        dict(multiply=[dict(variable='ExtraDamage'),dict(state='block')])]))],
        [dict(name='CalculationBase',base_value=0),dict(name='ExtraDamage',base_value=1)])
    initial=state(card,base_strength=2);sim=CombatSimulator(initial,rule)
    changed=replace(initial,block=30,strength_delta=3)
    after=sim.apply(changed,Operation('play_c','e')).state
    assert after.enemies[0].hp==65


def test_canonical_weak_and_vulnerable_multiply_before_one_final_rounding():
    card=Card('c','CARD.TEST',1,'AnyEnemy','Deal 21 damage.',canonical=True)
    rule=rules([dict(operation='damage',amount=dict(variable='Damage'))],[dict(name='Damage',base_value=21)])
    initial=state(card,player_weak=True,enemies=(replace(state(card).enemies[0],vulnerable=1),))
    after=CombatSimulator(initial,rule).apply(initial,Operation('play_c','e')).state
    assert after.enemies[0].hp==77  # floor(21 * .75 * 1.5) = 23, not floor(15 * 1.5)


def test_live_raw_variables_recover_rounding_without_using_exported_base():
    card=Card('c','CARD.TEST',1,'AnyEnemy','Deal 15 damage.',dynamic_variables=(('Damage',21),))
    rule=rules([dict(operation='damage',amount=dict(variable='Damage'))], [dict(name='Damage',base_value=6)])
    initial=state(card,player_weak=True,enemies=(replace(state(card).enemies[0],vulnerable=1),))
    after=CombatSimulator(initial,rule).apply(initial,Operation('play_c','e')).state
    assert after.enemies[0].hp==77
    # Explicit raw values remain the source even after canonical rounding is selected.
    raw=replace(card,canonical=True)
    assert CombatSimulator(replace(initial,hand=(raw,)),rule).apply(replace(initial,hand=(raw,)),Operation('play_c','e')).state.enemies[0].hp==77


def test_frail_legion_and_dexterity_round_after_combined_modifiers():
    card=Card('c','CARD.TEST',1,'Self','Gain 5 Block.',canonical=True)
    rule=rules([dict(operation='block',amount=dict(variable='Block'))],[dict(name='Block',base_value=5)])
    initial=state(card,block=0,player_frail=True,legion_ready=True)
    after=CombatSimulator(initial,rule).apply(initial,Operation('play_c')).state
    assert after.block==7 and not after.legion_ready  # floor(5 * 2 * .75)


def test_same_card_dexterity_changes_are_seen_by_later_block_effects():
    card=Card('c','CARD.TEST',1,'Self','',canonical=True)
    rule=rules([dict(operation='dexterity',amount=2),dict(operation='block',amount=5),dict(operation='block',amount=5)])
    initial=state(card,block=0,legion_ready=True)
    after=CombatSimulator(initial,rule).apply(initial,Operation('play_c')).state
    assert after.block==21  # first (5 + 2) * 2; second 5 + 2


@pytest.mark.parametrize('expression',[
    {'state':'__class__'}, {'eval':'x'}, {'add':[True,1]}, {'multiply':[1]}, {'variable':'x()'}])
def test_untrusted_amount_expressions_are_rejected(expression):
    with pytest.raises(ValueError):validate_expression(expression)


def test_amounts_reject_unknown_state_and_division_by_zero():
    with pytest.raises(UnsupportedEffect):evaluate_expression({'state':'block'},[],None,KnowledgeRules.variable)
    with pytest.raises(UnsupportedEffect):evaluate_expression({'floor_divide':[2,0]},[],None,KnowledgeRules.variable)
