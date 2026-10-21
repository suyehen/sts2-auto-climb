from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import pytest
from autoslay.decision.contracts import CombatState,Enemy,UnsupportedEffect
from autoslay.rules.monsters import MonsterRules,expression,unmodeled_special_hooks
from autoslay.planning.lookahead import evaluate_lookahead
from autoslay.telemetry.decisions import packet


def branch(target,weight=1,repeat='CanRepeatForever',condition=True):
    return dict(target=target,weight=weight,repeat_type=repeat,condition=condition,cooldown=0,max_consecutive=0,gaps=[])


def fixtures(ascension=0):
    hit={'if':[{'greater_equal':[{'ascension':True},9]},5,4]}
    attack=dict(variable='a',state_id='HIT',commands=[dict(operation='attack',amount=hit,repeats=1)],
                announced_attacks=[dict(amount=hit,repeats=1)],intent_types=[],gaps=[])
    status=dict(variable='b',state_id='GOOP',commands=[dict(operation='discard',amount=2,card_id='CARD.SLIMED')],
                announced_attacks=[],intent_types=['StatusCard'],gaps=[])
    entry=dict(entity_id='MONSTER.TEST',category='monster',gaps=[],unmodeled_hooks=[],presentation_hooks=[],
               moves=[attack,status],state_graph=dict(
               nodes=[dict(variable='a',state_id='HIT',kind='MoveState',gaps=[],branches=[]),
                      dict(variable='b',state_id='GOOP',kind='MoveState',gaps=[],branches=[])],
               edges=[dict(source='a',target='b'),dict(source='b',target='a')]))
    behavior=dict(game_version='0.111.0',game_assembly_sha256='pinned',entries=[entry])
    snapshot=dict(game_version='0.111.0',game_assembly_sha256='pinned',state=dict(ascension=ascension,player={'deck':[]}))
    cards={'CARD.SLIMED':dict(category='card',name='黏液',model_type='Cards.Slimed',variants=[dict(
        upgrade_level=0,energy_cost=1,target_type='None',type='Status',description='Draw 1 card.')])}
    state=CombatState(50,80,0,3,(),(Enemy('e','MONSTER.TEST',30,0,0,None,(),uncertain_intents=('StatusCard',),max_hp=30),),(),discard_known=True,turn=1)
    model=MonsterRules(snapshot,cards,behavior)
    return model,model.prepare(state),entry,behavior,snapshot,cards


@pytest.mark.parametrize('entity,body',[
    ('MONSTER.NIBBIT','return moveStateId != "SLICE_MOVE";'),
    ('MONSTER.FUZZY_WURM_CRAWLER','return moveStateId != "FIRST_ACID_GOOP";')])
def test_bestiary_filter_does_not_block_combat_but_other_hooks_do(entity,body):
    model,state,entry,behavior,snapshot,cards=fixtures()
    entry['entity_id']=entity
    entry['presentation_hooks']=['ShouldShowMoveInBestiary']
    model=MonsterRules(snapshot,cards,behavior)
    state=replace(state,enemies=(replace(state.enemies[0],entity_id=entity),))
    assert unmodeled_special_hooks(entry)==set()
    assert model.audit(state)[0]['unmodeled_special_hooks']==[]
    assert model.step(state)[0][1].discard_composition==(('CARD.SLIMED',2),)
    entry['unmodeled_hooks'].append('AfterDeath')
    assert unmodeled_special_hooks(entry)=={'AfterDeath'}
    with pytest.raises(UnsupportedEffect,match='monster_special_hooks_unmodeled'):
        model.step(state)
    entry['unmodeled_hooks']=['UnknownHook']
    assert unmodeled_special_hooks(entry)=={'UnknownHook'}


def test_no_special_hooks_does_not_read_display_evidence(monkeypatch):
    def unexpected_read(*args):
        pytest.fail('Monster without special hooks must not read display evidence')
    monkeypatch.setattr('autoslay.knowledge_base.repository.read_json',unexpected_read)
    for mechanics in ({},{'AfterAddedToRoom':'Initialize();'}):
        assert unmodeled_special_hooks(dict(unmodeled_hooks=[]))==set()


def test_unregistered_hook_does_not_read_behavior(monkeypatch):
    def unexpected_read(*args):raise AssertionError('No per-entity evidence registry should be read')
    monkeypatch.setattr('autoslay.knowledge_base.repository.read_json',unexpected_read)
    assert unmodeled_special_hooks(dict(entity_id='MONSTER.TEST',unmodeled_hooks=['AfterDeath']))=={'AfterDeath'}


@pytest.mark.parametrize('ascension,damage',[(0,4),(8,4),(9,5),(10,5)])
def test_status_turn_inserts_actual_card_then_selects_next_attack(ascension,damage):
    model,state,*_=fixtures(ascension)
    branches=model.step(state)
    probability,after,trace=branches[0]
    assert probability==1 and after.hp==50
    assert after.discard_composition==(('CARD.SLIMED',2),)
    assert after.enemies[0].attacks==(damage,)
    assert trace[0]['next_move']=='HIT'


def test_dead_monster_does_not_generate_cards_and_enemy_block_survives():
    model,state,entry,*_=fixtures()
    assert model.step(replace(state,enemies=(replace(state.enemies[0],hp=0),)))[0][1].discard_composition==()
    entry['moves'][1]['commands']=[dict(operation='block',amount=6)]
    assert model.step(state)[0][1].enemies[0].block==6


def test_nonrepeat_and_weighted_random_selection_keep_real_probability():
    model,state,entry,*_=fixtures()
    c=deepcopy(entry['moves'][0]);c.update(variable='c',state_id='HIT2')
    entry['moves'].append(c)
    entry['state_graph']['nodes'] += [dict(variable='c',state_id='HIT2',kind='MoveState',gaps=[],branches=[]),dict(variable='r',state_id='r',kind='RandomBranchState',gaps=[],branches=[
        branch('a',1,'CannotRepeat'),
        branch('b',100,'CannotRepeat'),
        branch('c',3,'CannotRepeat')])]
    entry['state_graph']['edges'][1]['target']='r'
    results=model.step(state)
    assert [(p,s.enemies[0].move_state) for p,s,_ in results]==[(.25,'a'),(.75,'c')]


def test_condition_uses_hp_after_player_damage_and_csharp_integer_threshold():
    model,state,entry,*_=fixtures()
    entry['state_graph']['nodes'].append(dict(variable='cond',state_id='cond',kind='ConditionalBranchState',gaps=[],branches=[
        branch('a',condition={'greater_equal':[{'field':'hp'},{'divide':[{'field':'max_hp'},2]}]}),
        branch('b',condition={'less':[{'field':'hp'},{'divide':[{'field':'max_hp'},2]}]})]))
    entry['state_graph']['edges'][1]['target']='cond'
    for hp,dest in [(50,'a'),(49,'b')]:
        after=model.step(replace(state,enemies=(replace(state.enemies[0],max_hp=101,hp=hp),)))[0][1]
        assert after.enemies[0].move_state==dest


def test_state_update_and_strength_buff_change_followup_damage():
    model,state,entry,*_=fixtures()
    entry['moves'][1]['commands']=[dict(operation='set_field',field='_charged',amount=True),dict(operation='strength',amount=5)]
    enemy=replace(state.enemies[0],runtime_fields=(('_charged',False),))
    after=model.step(replace(state,enemies=(enemy,)))[0][1]
    assert after.enemies[0].runtime_fields==(('_charged',True),)
    assert after.enemies[0].strength==5 and after.enemies[0].attacks==(9,)


def test_new_debuff_keeps_full_duration_and_expired_enemy_weak_is_removed():
    model,state,entry,*_=fixtures()
    entry['moves'][1]['commands']=[dict(operation='frail',amount=2)]
    after=model.step(replace(state,frail_turns=1))[0][1]
    assert after.frail_turns==2
    enemy=replace(state.enemies[0],move_state='a',attacks=(3,),weak=True,weak_turns=1,move_history=('a',))
    after=model.step(replace(state,enemies=(enemy,)))[0][1]
    assert after.hp==47 and not after.enemies[0].weak


@pytest.mark.parametrize('source',[
    'await CreatureCmd.Kill(base.Creature);',
    'if (HasCondition) { await CardPileCmd.AddToCombatAndPreview<Slimed>(targets, PileType.Discard, 2, null); }',
    'await PowerCmd.Apply<PoisonPower>(ctx, targets, 8m, base.Creature, null);'])
def test_unknown_effects_are_not_flattened_to_zero(source):
    model,state,entry,*_=fixtures()
    entry['moves'][1]['gaps']=[source]
    with pytest.raises(UnsupportedEffect):model.step(state)


def test_unavailable_history_and_version_mismatch_stop_prediction():
    model,state,entry,*_=fixtures()
    entry['state_graph']['nodes'].append(dict(variable='r',state_id='r',kind='RandomBranchState',gaps=[],branches=[
        branch('a',1,'UseOnlyOnce')]))
    entry['state_graph']['edges'][1]['target']='r'
    with pytest.raises(UnsupportedEffect,match='history'):model.step(replace(state,enemies=(replace(state.enemies[0],history_complete=False),)))
    model.compatible=False
    with pytest.raises(UnsupportedEffect,match='version'):model.step(state)


def test_condition_subset_never_executes_calls_or_attributes():
    for value in ['hp.__class__',{'call':'dangerous'},{'field':'hp.__class__'}]:
        with pytest.raises(ValueError):expression(value,{'hp':2})
    assert expression({'or':[True,{'field':'MissingField'}]}, {}) is True


def test_missing_runtime_field_is_not_guessed_from_turn_one_initializer():
    model,state,entry,*_=fixtures()
    entry['state_graph']['nodes'].append(dict(variable='c',state_id='c',kind='ConditionalBranchState',gaps=[],branches=[
        branch('a',condition={'field':'_phase'})]))
    entry['state_graph']['edges'][1]['target']='c'
    prepared=model.prepare(state)
    assert not prepared.enemies[0].runtime_fields
    with pytest.raises(UnsupportedEffect,match='public_monster_field_missing'):model.step(prepared)
    assert model.step(prepared,select_next=False)[0][1].discard_composition==(('CARD.SLIMED',2),)


@pytest.mark.local_knowledge
def test_default_unknown_monster_has_no_repeated_attack_prediction(monkeypatch):
    model,state,_,behavior,snapshot,cards=fixtures()
    behavior['entries']=[]
    monkeypatch.setattr('autoslay.rules.monsters.load',lambda:behavior)
    report=evaluate_lookahead(snapshot,state,[{'steps':[]}],[],cards,{'horizon':2,'time_ms':1000})
    scenario=report['candidates'][0]['scenarios'][0]
    assert scenario['result'] is None or scenario['result']['covered_probability']==0
    assert report['unmodeled'] and report['enemy_model']['mode']=='local_behavior_graph'
    assert 'repeat_visible_attack_scenario' not in str(report)


@pytest.mark.local_knowledge
def test_local_behavior_replaces_default_repeated_intent_through_two_turns(monkeypatch):
    model,state,_,behavior,snapshot,cards=fixtures()
    monkeypatch.setattr('autoslay.rules.monsters.load',lambda:behavior)
    report=evaluate_lookahead(snapshot,state,[{'steps':[]}],[],cards,{'horizon':2,'draws':0,'time_ms':1000})
    scenario=report['candidates'][0]['scenarios'][0]
    assert scenario['coverage_kind']=='local_behavior_mechanics'
    assert scenario['result']['covered_probability']==1
    assert scenario['result']['conditional_metrics']['hp_loss']==4
    assert any(row['generated_cards'][0]['entity_id']=='CARD.SLIMED' for row in scenario['enemy_turns'] if row['generated_cards'])


@pytest.mark.local_knowledge
def test_generated_slimed_uses_draw_and_exhaust_primitives():
    from autoslay.decision.contracts import Card,Operation
    from autoslay.simulation.engine import CombatSimulator
    model,state,*_=fixtures()
    card=Card('slimed','CARD.SLIMED',1,'None','Draw 1 card.',card_type='Status')
    state=replace(state,hand=(card,),energy=1,draw_composition=(('CARD.STRIKE_IRONCLAD',1),))
    sim=CombatSimulator(state)
    transition=sim.apply(state,Operation('play_slimed'))
    assert transition.boundary=='draw_requires_observation' and transition.draw_count==1
    resumed=sim.resume_draw(transition,replace(transition.state,draw_composition=(),hand=(Card('drawn','CARD.STRIKE_IRONCLAD',1,'AnyEnemy','Deal 6 damage.'),)))
    assert resumed.state.energy==0 and dict(resumed.state.exhausted_composition)['CARD.SLIMED']==1


def test_ai_packet_receives_bounded_forecast_without_mutating_archive():
    forecast=dict(horizon=2,candidates=[dict(scenarios=[dict(enemy_turns=[{'move':str(i)} for i in range(20)],result=None)])])
    report=dict(source={},phase='combat',combat=dict(lookahead=forecast))
    result=packet(report)['combat_forecast']
    assert result['horizon']==2 and len(result['candidates'][0]['scenarios'][0]['enemy_turns'])==8
    assert result['candidates'][0]['scenarios'][0]['additional_turn_records_local']==12
    assert len(forecast['candidates'][0]['scenarios'][0]['enemy_turns'])==20
