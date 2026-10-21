from dataclasses import replace
from types import SimpleNamespace
import pytest
from autoslay.decision.contracts import Card,CombatState,Enemy,Operation,TriggeredRelic,UnsupportedEffect
from autoslay.rules.registry import CardRule,Effect,KnowledgeRules,EntityMechanics,Trigger,EffectSpec
from autoslay.rules.triggered_relics import read_triggered,reset_for_turn
from autoslay.rules.scheduled_relics import before_hand_draw
from autoslay.simulation.engine import CombatSimulator


def state(*cards,**kwargs):
    return replace(CombatState(40,80,0,3,cards,(Enemy('e','MONSTER.TEST',100,0,0,None,()),),(),turn=1,discard_known=True),**kwargs)


def relic(name='R',kind='Attack',effect=('energy',1,1),counter=2,reset=True):
    return TriggeredRelic('RELIC.'+name,kind,3,counter,reset,1,(effect,))


class Rules:
    def card(self,c,s):return CardRule((Effect('damage',3),) if c.card_type=='Attack' else ())


def card(name='c',kind='Attack'):
    return Card(name,'CARD.TEST',1,'AnyEnemy' if kind=='Attack' else 'Self','',card_type=kind)


def test_post_play_counter_increments_once_after_actual_play_and_filters_type():
    initial=state(card(),triggered_relics=(relic(),))
    after=CombatSimulator(initial,Rules()).apply(initial,Operation('play_c','e')).state
    assert after.energy==3 and after.triggered_relics[0].counter==0
    assert initial.triggered_relics[0].counter==2
    initial=state(card(kind='Skill'),triggered_relics=(relic(),))
    after=CombatSimulator(initial,Rules()).apply(initial,Operation('play_c')).state
    assert after.energy==2 and after.triggered_relics[0].counter==2


def test_draw_continuation_fires_hook_only_after_entire_card_finishes():
    class DrawRules(Rules):
        def card(self,c,s):return CardRule((Effect('draw',1),Effect('energy',2)))
    initial=state(card(kind='Skill'),draw_composition=(('CARD.TEST',2),),triggered_relics=(relic(kind='Skill'),))
    sim=CombatSimulator(initial,DrawRules());transition=sim.apply(initial,Operation('play_c'))
    assert transition.boundary=='draw_requires_observation' and transition.state.triggered_relics[0].counter==2
    after=sim.resume_draw(transition,transition.state).state
    assert after.energy==5 and after.triggered_relics[0].counter==0


def test_lethal_card_advances_counters_but_combat_ending_commands_grant_no_resources():
    initial=state(card(),enemies=(replace(state().enemies[0],hp=3),),triggered_relics=(relic(),))
    after=CombatSimulator(initial,Rules()).apply(initial,Operation('play_c','e')).state
    assert after.combat_won and after.triggered_relics[0].counter==0 and after.energy==2
    initial=state(card(kind='Skill'),triggered_relics=(relic('D','Skill',('unpowered_damage',100,1)),relic('E','Skill')))
    after=CombatSimulator(initial,Rules()).apply(initial,Operation('play_c')).state
    assert after.combat_won and after.triggered_relics[0].counter==0 and after.triggered_relics[1].counter==0
    assert after.energy==2


def test_turn_counters_reset_before_draw_but_cross_turn_counters_survive():
    initial=state(turn=2,triggered_relics=(relic('A'),relic('B',reset=False)))
    after=before_hand_draw(initial)
    assert [r.counter for r in after.triggered_relics]==[0,2]
    assert reset_for_turn(after)==after


def test_missing_ambiguous_or_animating_counter_is_not_assumed_zero():
    identity='RELIC.TEST'
    trigger=Trigger('after_card_played',(EffectSpec('energy',1),),
                    counter=dict(period=3,card_type='Attack',reset='turn'))
    rules=KnowledgeRules({identity:EntityMechanics('relic',(trigger,))},{identity:{}})
    for value in (None,True,-1,3):
        with pytest.raises(ValueError):read_triggered(dict(entity_id=identity,counter=value),1,rules)
    assert read_triggered(dict(entity_id=identity,counter=2),1,rules)[0].counter==2
    trigger.counter['field']='_attacksPlayed'
    observed=dict(entity_id=identity,counter=3,runtime_state={'fields':{'_attacksPlayed':8}})
    assert read_triggered(observed,1,rules)[0].counter==2
    observed['runtime_state']['fields']['_attacksPlayed']=True
    with pytest.raises(UnsupportedEffect):read_triggered(observed,1,rules)
