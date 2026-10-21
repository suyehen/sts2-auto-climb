"""Arithmetic and lifecycle checks for native resource mechanics."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from autoslay.decision.contracts import Card,CombatState,Enemy,Operation,Potion,UnsupportedEffect
from autoslay.rules.registry import KnowledgeRules,Effect,CardRule,EntityMechanics,Trigger,EffectSpec
from autoslay.simulation.engine import CombatSimulator
from autoslay.rules.registry import KnowledgeRules


def state(**changes):
    return replace(CombatState(40,79,0,3,(),(Enemy('e','MONSTER.TEST',100,0,0,None,(12,)),),(),
                               potions=(Potion('p','POTION.TEST','','use_p',('player',)),)),**changes)


def simulator(initial,effects):
    class Rules:
        def potion(self,p):return SimpleNamespace(effects=tuple(effects),target_type='AnyPlayer')
        def card(self,c,s):return CardRule((Effect('block',5),))
    return CombatSimulator(initial,Rules())


def test_heal_percentage_rounding_capping_and_trajectory_counter():
    s=state(hp=40,healing=2)
    result=simulator(s,[Effect('heal_percent',20)]).apply(s,Operation('use_p','player'))
    assert result.state.hp==55 and result.state.healing==17 and not result.state.potions
    full=replace(s,hp=78)
    result=simulator(full,[Effect('heal_percent',20)]).apply(full,Operation('use_p','player'))
    assert result.state.hp==79 and result.state.healing==3


def test_max_hp_gain_heals_only_actual_increase_and_respects_game_cap():
    s=state()
    result=simulator(s,[Effect('gain_max_hp',5)]).apply(s,Operation('use_p','player'))
    assert (result.state.max_hp,result.state.hp,result.state.healing)==(84,45,5)
    capped=replace(s,max_hp=999999998)
    result=simulator(capped,[Effect('gain_max_hp',5)]).apply(capped,Operation('use_p','player'))
    assert (result.state.max_hp,result.state.hp,result.state.healing)==(999999999,41,1)


def test_stars_enable_a_real_card_and_are_spent_once():
    card=Card('c','CARD.TEST',1,'Self','',star_cost=5)
    s=state(hand=(card,),stars=2);sim=simulator(s,[Effect('stars',3)])
    assert Operation('play_c') not in sim.actions(s)
    after=sim.apply(s,Operation('use_p','player')).state
    assert after.stars==5 and Operation('play_c') in sim.actions(after)
    after=sim.apply(after,Operation('play_c')).state
    assert after.stars==0 and after.energy==2 and not after.potions


def test_dexterity_changes_powered_card_block_and_never_unpowered_block():
    card=Card('c','CARD.TEST',1,'Self','')
    s=state(hand=(card,));sim=simulator(s,[Effect('dexterity',2),Effect('unpowered_block',12)])
    after=sim.apply(s,Operation('use_p','player')).state
    assert after.block==12 and after.dexterity_delta==2
    assert sim.apply(after,Operation('play_c')).state.block==19


def test_observed_frail_block_does_not_guess_pre_rounding_base():
    card=Card('c','CARD.TEST',1,'Self','')
    s=state(hand=(card,),player_frail=True,dexterity_delta=2)
    result=simulator(s,[]).apply(s,Operation('play_c'))
    assert result.boundary=='frail_dexterity_rounding_requires_observation'


def test_canonical_block_adds_base_and_new_dexterity_before_frail_rounding():
    raw=Card('c','CARD.TEST',1,'Self','Gain 5 Block.',canonical=True)
    rules=KnowledgeRules({'CARD.TEST':EntityMechanics('card',(
        Trigger('on_play',(EffectSpec('block',dict(variable='Block')),)),))},
        {'CARD.TEST':{'variants':[dict(upgrade_level=0,dynamic_variables=[dict(name='Block',base_value=5)])]}})
    s=state(hand=(raw,),base_dexterity=1,dexterity_delta=2,player_frail=True)
    result=CombatSimulator(s,rules).apply(s,Operation('play_c'))
    assert result.state.block==6


def test_weak_reduces_each_visible_hit_once_and_extends_duration_without_reducing_twice():
    s=state(enemies=(replace(state().enemies[0],attacks=(13,8)),))
    class Rules:
        def potion(self,p):return SimpleNamespace(effects=(Effect('weak',3),),target_type='AnyEnemy')
    s=replace(s,potions=(Potion('p','POTION.TEST','','use_p',('e',)),))
    first=CombatSimulator(s,Rules()).apply(s,Operation('use_p','e')).state
    assert first.enemies[0].attacks==(9,6) and first.enemies[0].weak_turns==3
    again=replace(first,potions=s.potions)
    second=CombatSimulator(again,Rules()).apply(again,Operation('use_p','e')).state
    assert second.enemies[0].attacks==(9,6) and second.enemies[0].weak_turns==6


def test_temporary_dexterity_and_no_draw_expire_before_next_hand_without_losing_permanent_ban():
    from autoslay.rules.monsters import MonsterRules
    model=object.__new__(MonsterRules)
    for permanent,expected in [(False,False),(True,True)]:
        s=state(enemies=(),base_dexterity=7,dexterity_delta=3,temporary_dexterity=5,
            effect_draw_blocked=True,temporary_draw_blocked=True,permanent_draw_blocked=permanent)
        _,after,_=model.step(s)[0]
        assert after.base_dexterity+after.dexterity_delta==5
        assert after.temporary_dexterity==0 and after.temporary_draw_blocked is False
        assert after.effect_draw_blocked is expected


def test_variable_bindings_are_integral_unique_and_never_executable_expressions():
    rules=KnowledgeRules()
    rule=(EffectSpec('stars',dict(variable='Stars')),)
    assert rules.effects(rule,'',variables=[dict(name='Stars',base_value=3)])[0].amount==3
    for variables in ([],[dict(name='Stars',base_value=.5)],
                      [dict(name='Stars',base_value=2),dict(name='Stars',base_value=2)],
                      [dict(name='Stars',base_value='__import__("os")')]):
        with pytest.raises(UnsupportedEffect):rules.effects(rule,'',variables=variables)
    with pytest.raises(ValueError):EffectSpec('stars',{'variable':'x.__class__'})


def test_observed_variable_values_override_catalog_and_unknown_instance_mechanics_stop():
    rules=KnowledgeRules({'POTION.TEST':EntityMechanics('potion',(
        Trigger('on_use',(EffectSpec('stars',dict(variable='Stars')),)),),target_type='AnyPlayer')},
        {'POTION.TEST':dict(dynamic_variables=[dict(name='Stars',base_value=3)])})
    potion=Potion('p','POTION.TEST','','use_p',('player',),dynamic_variables=(('Stars',7),))
    assert rules.potion(potion).effects[0].amount==7
    for kwargs in [dict(affliction_id='AFFLICTION.UNKNOWN'),dict(enchantment_id='ENCHANTMENT.UNKNOWN')]:
        with pytest.raises(UnsupportedEffect):rules.card(Card('c','CARD.TEST',1,'Self','',**kwargs),state())
