"""Shared card destinations, held-card lifecycles and restricted source translation."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from autoslay.decision.contracts import Card,CombatState,Enemy,Operation,UnsupportedEffect
from autoslay.rules.registry import CardRule,Effect,KnowledgeRules
from autoslay.simulation.engine import CombatSimulator
from autoslay.simulation.card_flow import flush_hand,refresh_retained
from autoslay.planning.lookahead import draw_branches


def card(identity,keywords=(),**kwargs):
    return Card(identity,'CARD.'+identity,1,'Self','',keywords=keywords,**kwargs)


def state(*cards,**kwargs):
    return replace(CombatState(30,80,0,3,cards,(Enemy('e','MONSTER.TEST',100,0,0,None,()),),(),discard_known=True),**kwargs)


class Rules:
    def card(self,card,state):
        if card.entity_id=='CARD.UNKNOWN':raise UnsupportedEffect('unknown held hook')
        return CardRule((Effect('strength',2),),exhaust='Exhaust' in (card.keywords or ()),
                        playable='Unplayable' not in (card.keywords or ()))


def test_held_cards_use_keywords_not_entity_allowlists():
    retained=card('RETAIN',('Retain',));ethereal=card('ETHEREAL',('Ethereal','Retain'))
    ordinary=card('NORMAL');unplayable=card('STATUS',('Unplayable',))
    initial=state(retained,ethereal,ordinary,unplayable)
    after=flush_hand(initial,Rules())
    assert after.hand==(retained,)
    assert dict(after.discard_composition)=={'CARD.NORMAL':1,'CARD.STATUS':1}
    assert after.exhausted_composition==(('CARD.ETHEREAL',1),)
    assert initial.hand==(retained,ethereal,ordinary,unplayable)


def test_unknown_held_hooks_cannot_be_discarded_as_a_noop():
    initial=state(card('UNKNOWN'))
    with pytest.raises(UnsupportedEffect,match='unknown held hook'):flush_hand(initial,Rules())


def test_power_removal_takes_precedence_over_exhaust_and_does_not_reshuffle():
    power=card('POWER',('Exhaust',),card_type='Power')
    initial=state(power);sim=CombatSimulator(initial,Rules())
    after=sim.apply(initial,Operation('play_POWER')).state
    assert after.strength_delta==2 and after.removed_composition==(('CARD.POWER',1),)
    assert not after.exhausted_composition and not after.discard_composition and not after.hand


def test_unplayable_card_is_known_without_becoming_an_action():
    status=card('STATUS',('Unplayable',))
    initial=state(status);sim=CombatSimulator(initial,Rules())
    assert not sim.unsupported and not sim.actions(initial)
    with pytest.raises(UnsupportedEffect):sim.apply(initial,Operation('play_STATUS'))


def test_retained_cards_keep_identity_and_use_actual_hand_capacity():
    held=tuple(card(str(i),('Retain',)) for i in range(9))
    initial=state(*held,draw_composition=(('CARD.NEW',4),))
    templates={c.entity_id:replace(c,cost=2,description='canonical') for c in held}
    refreshed=refresh_retained(initial,templates)
    assert [c.instance_id for c in refreshed.hand]==[c.instance_id for c in held]
    assert all(c.canonical and c.cost==2 for c in refreshed.hand)
    branches,_=draw_branches(refreshed,5,dict(templates,**{'CARD.NEW':card('NEW')}),8,1)
    assert len(branches)==1 and len(branches[0][1].hand)==10
    assert branches[0][1].draw_composition==(('CARD.NEW',3),)
    with pytest.raises(UnsupportedEffect):refresh_retained(initial,{})


def test_energy_doubling_uses_remaining_energy_after_payment():
    class EnergyRules(Rules):
        def card(self,c,s):return CardRule((Effect('multiply_energy',2),),exhaust=True)
    initial=state(card('DOUBLE'))
    after=CombatSimulator(initial,EnergyRules()).apply(initial,Operation('play_DOUBLE')).state
    assert after.energy==4 and after.exhausted_composition==(('CARD.DOUBLE',1),)
    initial=replace(initial,energy=1)
    assert CombatSimulator(initial,EnergyRules()).apply(initial,Operation('play_DOUBLE')).state.energy==0
