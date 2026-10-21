from dataclasses import replace
from types import SimpleNamespace
import pytest
from autoslay.decision.contracts import CombatState,Enemy,Card,ScheduledRelic,Operation,UnsupportedEffect
from autoslay.rules.scheduled_relics import apply_phase,before_hand_draw,after_hand_draw,read_scheduled
from autoslay.rules.monsters import MonsterRules
from autoslay.simulation.engine import CombatSimulator


def state(turn=7,**changes):
    return replace(CombatState(40,80,0,3,(),(Enemy('boss','MONSTER.TEST',52,0,0,None,(999,)),),(),turn=turn),**changes)


def test_delayed_damage_wins_before_lethal_enemy_turn_and_retains_held_cards():
    clock=ScheduledRelic('RELIC.CLOCK','side_turn_end',7,'unpowered_damage',52)
    card=Card('held','CARD.STRIKE_IRONCLAD',1,'AnyEnemy','Deal 6 damage.')
    s=state(scheduled_relics=(clock,),hand=(card,))
    result=CombatSimulator(s).apply(s,Operation('end_turn'))
    assert result.boundary=='combat_victory' and result.state.hp==40 and result.state.hand==(card,)
    assert result.state.enemies[0].hp==0 and not result.state.discard_composition
    model=object.__new__(MonsterRules)
    after=model.step(s)[0][1]
    assert after.combat_won and after.hp==40  # No move graph needed after victory.


def test_delayed_damage_does_not_fire_early_and_cannot_fire_twice_in_same_turn():
    clock=ScheduledRelic('RELIC.CLOCK','side_turn_end',7,'unpowered_damage',52)
    s=state(turn=6,enemies=(replace(state().enemies[0],hp=200),),scheduled_relics=(clock,))
    assert apply_phase(s,'side_turn_end').enemies[0].hp==200
    at_seven=apply_phase(replace(s,turn=7),'side_turn_end')
    assert at_seven.enemies[0].hp==148
    assert apply_phase(at_seven,'side_turn_end').enemies[0].hp==148
    assert apply_phase(replace(at_seven,turn=8),'side_turn_end').enemies[0].hp==148


def test_unpowered_relic_damage_respects_block_caps_and_primary_target_victory():
    clock=ScheduledRelic('RELIC.CLOCK','side_turn_end',7,'unpowered_damage',52)
    boss=replace(state().enemies[0],block=3)
    minion=Enemy('minion','MONSTER.MINION',100,0,2,1,(),minion=True)
    s=state(enemies=(boss,minion),scheduled_relics=(clock,),strength_delta=99)
    after=apply_phase(s,'side_turn_end')
    assert after.enemies[0].hp==3 and after.enemies[0].block==0
    assert after.enemies[1].hp==99 and not after.combat_won
    s=replace(s,enemies=(replace(boss,block=0),minion))
    assert apply_phase(s,'side_turn_end').combat_won


def test_block_and_energy_start_phases_follow_reset_and_do_not_repeat_after_draw():
    wheel=ScheduledRelic('RELIC.WHEEL','after_block_cleared',3,'unpowered_block',18,last_turn=2)
    candle=ScheduledRelic('RELIC.CANDLE','side_turn_start',3,'energy',3,last_turn=2)
    glass=ScheduledRelic('RELIC.GLASS','after_hand_draw',None,'unpowered_damage',3,last_turn=2)
    s=state(turn=3,scheduled_relics=(wheel,candle,glass),player_frail=True,legion_ready=True,dexterity_delta=100)
    before=before_hand_draw(s)
    assert (before.block,before.energy,before.enemies[0].hp)==(18,3,52)
    after=after_hand_draw(before)
    assert after.enemies[0].hp==49 and after.block==18 and after.energy==6
    assert apply_phase(after,'after_hand_draw')==after and before_hand_draw(after)==after


def test_after_hand_draw_victory_precedes_later_side_start_resource_hooks():
    glass=ScheduledRelic('RELIC.GLASS','after_hand_draw',None,'unpowered_damage',3,last_turn=2)
    candle=ScheduledRelic('RELIC.CANDLE','side_turn_start',3,'energy',3,last_turn=2)
    s=state(turn=3,enemies=(replace(state().enemies[0],hp=3),),scheduled_relics=(candle,glass))
    after=after_hand_draw(s)
    assert after.combat_won and after.energy==3


def test_observed_start_hooks_are_marked_complete_but_current_end_hook_is_pending():
    from autoslay.rules.registry import KnowledgeRules,EntityMechanics,Trigger,EffectSpec
    entries={'RELIC.A':EntityMechanics('relic',(Trigger('side_turn_start',(EffectSpec('energy',dict(variable='Energy')),)),)),
             'RELIC.B':EntityMechanics('relic',(Trigger('side_turn_end',(EffectSpec('unpowered_damage',dict(variable='Damage')),),turn=7),))}
    refs={'RELIC.A':dict(dynamic_variables=[dict(name='Energy',base_value=2)]),
          'RELIC.B':dict(dynamic_variables=[dict(name='Damage',base_value=52)])}
    rules=KnowledgeRules(entries,refs)
    start=read_scheduled(dict(entity_id='RELIC.A'),7,rules)[0]
    end=read_scheduled(dict(entity_id='RELIC.B'),7,rules)[0]
    assert start.last_turn==7 and end.last_turn==6
    with pytest.raises(UnsupportedEffect):read_scheduled(dict(entity_id='RELIC.A'),None,rules)
    assert read_scheduled(dict(entity_id='RELIC.MISSING'),7,rules)==()


@pytest.mark.local_knowledge
def test_live_normalization_and_batch_forecast_can_wait_for_calendar_victory():
    from test_combat_batches import combat,actions
    from autoslay.decision.normalize import normalize
    from autoslay.knowledge_base.templates import index
    from autoslay.planning.combat_batches import analyze
    from autoslay.evaluation.combat import CombatPolicy
    from autoslay.planning.search import SearchBudget
    snapshot=combat(turn=7);player=snapshot['state']['player']
    player['hand']=[];player['relics']=[dict(entity_id='RELIC.STONE_CALENDAR')]
    snapshot['state']['enemies'][0].update(hp=52,block=0,intents=[dict(type='Attack',attack_damage=999,attack_repeats=1)])
    actions(snapshot)
    initial=normalize(snapshot)
    assert initial.scheduled_relics and not any('STONE_CALENDAR' in w for w in initial.warnings)
    report=analyze(snapshot,index()[0],CombatPolicy(),dict(horizon=2,branches=4),
                   SearchBudget(max_nodes=100,time_ms=1000,max_depth=2,top_k=5))
    chosen=report['selected_batch']
    assert chosen['steps']==[dict(action_id='end_turn',target_id=None)]
    assert chosen['estimated_death_probability']==0
    assert all(o['combat_finished'] and o['resources']['hp']==initial.hp for o in chosen['joint_outcomes'])
