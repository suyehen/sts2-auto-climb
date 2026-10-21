"""Source-ordered stack effects, including victory before enemy actions."""
from dataclasses import replace
import hashlib
from pathlib import Path
import pytest
from autoslay.decision.contracts import CombatState,Enemy,ScheduledRelic,UnsupportedEffect
from autoslay.rules.statuses import apply_player_phase,enemy_poison_start
from autoslay.rules.scheduled_relics import apply_phase,after_hand_draw


def state(**kwargs):
    return replace(CombatState(40,80,0,3,(),(Enemy('e','MONSTER.TEST',10,100,0,None,(100,)),),(),turn=1),**kwargs)


def test_regen_caps_healing_decrements_once_and_never_revives():
    initial=state(hp=78,regeneration=5)
    after=apply_player_phase(initial,'side_turn_end_early')
    assert (after.hp,after.healing,after.regeneration)==(80,2,4)
    assert apply_player_phase(after,'side_turn_end_early')==after
    after=apply_player_phase(replace(after,turn=2),'side_turn_end_early')
    assert after.hp==80 and after.healing==2 and after.regeneration==3
    dead=replace(initial,hp=0)
    assert apply_player_phase(dead,'side_turn_end_early')==dead
    with pytest.raises(UnsupportedEffect):apply_player_phase(replace(initial,turn=None),'side_turn_end_early')


def test_plating_grants_unpowered_block_early_and_decrements_on_later_starts():
    initial=state(plating=4,player_frail=True,dexterity_delta=99)
    after=apply_phase(initial,'side_turn_end')
    assert after.block==4 and after.plating==4
    assert apply_phase(after,'side_turn_end')==after
    next_turn=replace(after,turn=2,block=0)
    start=after_hand_draw(next_turn)
    assert start.plating==3 and apply_phase(start,'side_turn_start')==start
    assert apply_phase(start,'side_turn_end').block==3
    assert after_hand_draw(initial).plating==4


def test_early_regen_precedes_calendar_victory_and_is_not_replayed():
    clock=ScheduledRelic('RELIC.CLOCK','side_turn_end',7,'unpowered_damage',52,last_turn=6)
    initial=state(turn=7,regeneration=5,scheduled_relics=(clock,),
                  enemies=(replace(state().enemies[0],hp=52,block=0),))
    after=apply_phase(initial,'side_turn_end')
    assert after.combat_won and after.hp==45 and after.regeneration==4
    assert apply_phase(after,'side_turn_end')==after


def test_poison_ignores_block_and_respects_damage_cap_without_consuming_it():
    enemy=replace(state().enemies[0],poison=6)
    after=enemy_poison_start(state(enemies=(enemy,)))
    assert after.enemies[0].hp==4 and after.enemies[0].block==100 and after.enemies[0].poison==5
    after=enemy_poison_start(state(enemies=(replace(enemy,damage_cap=1),)))
    assert after.enemies[0].hp==9 and after.enemies[0].poison==5


def test_poison_finishes_necessary_target_before_requiring_enemy_move_data():
    from autoslay.rules.monsters import MonsterRules
    # No model/intent is needed when source-known poison ends combat first.
    model=MonsterRules.__new__(MonsterRules)
    initial=state(enemies=(replace(state().enemies[0],hp=6,poison=6),
                           Enemy('m','MONSTER.MINION',99,0,0,None,(999,),minion=True)))
    branches=model.step(initial)
    assert len(branches)==1 and branches[0][1].combat_won
    assert branches[0][1].hp==40 and branches[0][1].enemies[1].hp==99


def test_deferred_resources_wait_for_the_right_phase_and_are_consumed_once():
    from autoslay.rules.scheduled_relics import before_hand_draw
    initial=state(turn=2,next_turn_block=4,next_turn_energy=2,next_turn_stars=3,next_turn_draw=2)
    assert apply_phase(initial,'side_turn_end').energy==3
    before=before_hand_draw(initial)
    assert (before.block,before.energy,before.stars,before.next_turn_draw)==(4,5,3,2)
    assert before.next_turn_block==before.next_turn_energy==before.next_turn_stars==0
    assert before_hand_draw(before)==before
    after=after_hand_draw(before)
    assert after.next_turn_draw==0 and after_hand_draw(after)==after


def test_ritual_grows_after_player_side_ends_even_if_poison_then_ends_combat():
    from autoslay.rules.monsters import MonsterRules
    initial=state(ritual=2,enemies=(replace(state().enemies[0],hp=1,poison=1),))
    after=MonsterRules.__new__(MonsterRules).step(initial)[0][1]
    assert after.combat_won and after.strength_delta==2 and after.ritual==2


def test_dodge_and_roll_freezes_actual_gained_block_for_next_turn():
    from autoslay.decision.contracts import Card,Operation
    from autoslay.rules.registry import CardRule,Effect
    from autoslay.simulation.engine import CombatSimulator
    class Rules:
        def card(self,c,s):return CardRule((Effect('block_and_next_turn',5),))
    initial=state(hand=(Card('c','CARD.TEST',1,'Self',''),),dexterity_delta=2)
    after=CombatSimulator(initial,Rules()).apply(initial,Operation('play_c')).state
    assert after.block==7 and after.next_turn_block==7
    from autoslay.rules.scheduled_relics import before_hand_draw
    next_turn=before_hand_draw(replace(after,turn=2,block=0,dexterity_delta=100))
    assert next_turn.block==7 and next_turn.next_turn_block==0
