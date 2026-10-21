"""Deterministic creation, fresh identities, branch templates and observation stops."""
from dataclasses import replace
from types import SimpleNamespace
import pytest
from autoslay.decision.contracts import Card,CombatState,Enemy,Operation,UnsupportedEffect
from autoslay.rules.registry import KnowledgeRules,EntityMechanics,Trigger,EffectSpec
from autoslay.simulation.engine import CombatSimulator
from autoslay.simulation.card_flow import generate_known
from autoslay.planning.lookahead import draw_branches


def entry(identity,kind='Attack',keywords=(),cost=1,description='Deal 6 damage.'):
    return dict(entity_id=identity,category='card',variants=[dict(upgrade_level=0,description=description,
        energy_cost=cost,star_cost=-1,type=kind,target_type='AnyEnemy' if kind=='Attack' else 'Self',
        keywords=list(keywords),dynamic_variables=[dict(name='Damage',base_value=6)])])


def fixture(destination='hand',draw_first=False):
    references={'CARD.NEW':entry('CARD.NEW'),'CARD.SOURCE':entry('CARD.SOURCE',kind='Skill')}
    generation=EffectSpec('generate_card',2,generation=('CARD.NEW',destination,0))
    effects=([EffectSpec('draw',1)] if draw_first else [])+[generation,EffectSpec('energy',2)]
    definitions={'CARD.SOURCE':EntityMechanics('card',(Trigger('on_play',tuple(effects)),)),
                 'CARD.NEW':EntityMechanics('card',(Trigger('on_play',(EffectSpec('damage',dict(variable='Damage')),)),))}
    source=Card('source','CARD.SOURCE',1,'Self','',card_type='Skill')
    state=CombatState(40,80,0,3,(source,),(Enemy('e','MONSTER.TEST',100,0,0,None,()),),
                      (('CARD.NEW',3),),discard_known=True,base_strength=2,turn=1)
    return state,KnowledgeRules(definitions,references),references


def test_hand_generation_uses_observation_boundary_before_any_synthetic_action():
    initial,rules,_=fixture();sim=CombatSimulator(initial,rules)
    transition=sim.apply(initial,Operation('play_source'))
    assert transition.boundary=='generated_cards_requires_observation' and transition.state.energy==2
    assert len(transition.state.hand)==2 and len({c.instance_id for c in transition.state.hand})==2
    assert not transition.state.discard_composition  # source remains in play
    resumed=sim.resume_generated(transition)
    assert resumed.boundary is None and resumed.state.energy==4
    assert resumed.state.discard_composition==(('CARD.SOURCE',1),)
    generated=resumed.state.hand[0]
    after=sim.apply(resumed.state,Operation('play_'+generated.instance_id,'e')).state
    assert after.enemies[0].hp==92  # canonical generated card includes existing Strength


def test_discard_generation_is_a_multiset_and_survives_reshuffle_with_its_template():
    initial,rules,_=fixture('discard');sim=CombatSimulator(initial,rules)
    result=sim.apply(initial,Operation('play_source'))
    assert result.boundary is None and not result.state.hand
    generated=result.state.generated_templates[0]
    assert dict(result.state.discard_composition)[generated.variant_key]==2
    only_generated=replace(result.state,draw_composition=(),discard_composition=((generated.variant_key,2),))
    branches,_=draw_branches(only_generated,2,{},8,1)
    assert len(branches)==1 and len(branches[0][1].hand)==2
    assert all(c.entity_id=='CARD.NEW' and c.canonical for c in branches[0][1].hand)


def test_generation_does_not_obey_no_draw_and_overflow_does_not_invent_hand_ids():
    initial,rules,refs=fixture()
    held=tuple(Card(str(i),'CARD.NEW',1,'AnyEnemy','Deal 6 damage.',card_type='Attack') for i in range(10))
    full=replace(initial,hand=held,effect_draw_blocked=True)
    after,cards=generate_known(full,'CARD.NEW','hand',0,2,refs)
    assert after.hand==held and len(cards)==2 and sum(n for _,n in after.discard_composition)==2
    after,cards=generate_known(replace(full,hand=held[:9]),'CARD.NEW','hand',0,2,refs)
    assert len(after.hand)==10 and sum(n for _,n in after.discard_composition)==1


def test_draw_then_generation_preserves_descriptor_and_pays_only_once():
    initial,rules,_=fixture(draw_first=True);sim=CombatSimulator(initial,rules)
    draw=sim.apply(initial,Operation('play_source'))
    assert draw.boundary=='draw_requires_observation'
    generated=sim.resume_draw(draw,draw.state)
    assert generated.boundary=='generated_cards_requires_observation'
    assert generated.state.energy==2 and generated.resume_effects==(('energy',2,1),)
    assert sim.resume_generated(generated).state.energy==4


def test_generation_rejects_unknown_identity_or_unbounded_counts():
    initial,_,refs=fixture()
    with pytest.raises(ValueError):generate_known(initial,'CARD.UNKNOWN','hand',0,1,refs)
    with pytest.raises(UnsupportedEffect):generate_known(initial,'CARD.NEW','hand',0,101,refs)
    unchanged,cards=generate_known(initial,'CARD.UNKNOWN','hand',0,0,refs)
    assert unchanged==initial and not cards


def test_draw_hooks_run_on_effect_draw_and_opening_draw_but_not_generation():
    initial,_,refs=fixture(draw_first=True)
    refs['CARD.NEW']=entry('CARD.NEW',kind='Status',keywords=('Unplayable','Ethereal'),cost=-1,description='')
    refs['CARD.NEW']['variants'][0]['dynamic_variables']=[dict(name='Energy',base_value=1)]
    void=EntityMechanics('card',(Trigger('on_play',()),
        Trigger('on_draw',(EffectSpec('lose_energy',dict(variable='Energy')),))))
    source=EntityMechanics('card',(Trigger('on_play',(EffectSpec('draw',1),EffectSpec('energy',2))),))
    rules=KnowledgeRules({'CARD.SOURCE':source,'CARD.NEW':void},refs)
    template=Card('void','CARD.NEW',-1,'None','',canonical=True,card_type='Status',keywords=('Unplayable','Ethereal'))
    sim=CombatSimulator(initial,rules);transition=sim.apply(initial,Operation('play_source'))
    drawn=draw_branches(transition.state,1,{'CARD.NEW':template},8,1)[0][0][1]
    assert drawn.energy==2  # draw distribution itself has not double-run the hook
    after=sim.resume_draw(transition,drawn).state
    assert after.energy==3  # 3 - cost 1 - Void 1 + source's remaining gain 2
    opening=replace(initial,hand=())
    hand=draw_branches(opening,1,{'CARD.NEW':template},8,1,rules=rules,from_hand_draw=True)[0][0][1]
    assert hand.energy==2
    generated,_=generate_known(opening,'CARD.NEW','hand',0,1,refs)
    assert generated.energy==3
