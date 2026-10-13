from copy import deepcopy
from dataclasses import replace
import pytest
from autoslay.decision.contracts import CombatState,Enemy,UnsupportedEffect
from autoslay.knowledge.rulebook import entry_fingerprint,reference_matches
from autoslay.rules.monsters import MonsterRules,expression,unmodeled_special_hooks
from autoslay.planning.lookahead import evaluate_lookahead
from autoslay.telemetry.decisions import packet


def fixtures(ascension=0):
    attack=dict(variable='a',state_id='HIT',handler='HitMove',intent_expression='new SingleAttackIntent(Hit)',
                execution_evidence='await DamageCmd.Attack(Hit).FromMonster(this).Execute(null);',
                numeric_variants=[dict(ascension_min=0,ascension_max=10,effects=[dict(kind='attack',damage_expression='Hit',repeats_expression='1')])],state_updates=[])
    status=dict(variable='b',state_id='GOOP',handler='GoopMove',intent_expression='new StatusIntent(2)',
                execution_evidence='await CardPileCmd.AddToCombatAndPreview<Slimed>(targets, PileType.Discard, 2, null);',
                numeric_variants=[dict(ascension_min=0,ascension_max=10,effects=[])],state_updates=[])
    entry=dict(entity_id='MONSTER.TEST',category='monster',name='Test',provenance=dict(game_assembly_sha256='pinned'),
               parameter_expressions=dict(Hit='AscensionHelper.GetValueIfAscension(AscensionLevel.DeadlyEnemies, 5, 4)'),
               moves=[attack,status],mechanics={},state_graph=dict(definitions={},state_fields=[],
               nodes=[dict(variable='a',state_id='HIT',kind='MoveState'),dict(variable='b',state_id='GOOP',kind='MoveState')],
               edges=[dict(source='a',target='b'),dict(source='b',target='a')]))
    behavior=dict(game_version='0.111.0',game_assembly_sha256='pinned',ascension_thresholds={'DeadlyEnemies':9},entries=[entry])
    snapshot=dict(game_version='0.111.0',game_assembly_sha256='pinned',state=dict(ascension=ascension,player={'deck':[]}))
    cards={'CARD.SLIMED':dict(category='card',name='黏液',model_type='Cards.Slimed',variants=[dict(
        upgrade_level=0,energy_cost=1,target_type='None',type='Status',description='Draw 1 card.')])}
    state=CombatState(50,80,0,3,(),(Enemy('e','MONSTER.TEST',30,0,0,None,(),uncertain_intents=('StatusCard',),max_hp=30),),(),discard_known=True,turn=1)
    model=MonsterRules(snapshot,cards,behavior)
    return model,model.prepare(state),entry,behavior,snapshot,cards


def test_relic_pool_metadata_migration_preserves_all_semantics():
    previous=dict(entity_id='RELIC.TEST',category='relic',description='Heal 6',dynamic_variables=[{'base_value':6}])
    rule={'reference_sha256':entry_fingerprint(previous)}
    current=previous|dict(pool='IRONCLAD',pools=['IRONCLAD'])
    assert reference_matches(rule,current)
    for changed in [current|{'description':'Heal 8'},current|{'dynamic_variables':[{'base_value':8}]},current|{'new_effect':True}]:
        assert not reference_matches(rule,changed)
    card=previous|dict(category='card')
    assert not reference_matches({'reference_sha256':entry_fingerprint(card)},card|dict(pool='POOL',pools=['POOL']))


@pytest.mark.parametrize('entity,body',[
    ('MONSTER.NIBBIT','return moveStateId != "SLICE_MOVE";'),
    ('MONSTER.FUZZY_WURM_CRAWLER','return moveStateId != "FIRST_ACID_GOOP";')])
def test_bestiary_filter_does_not_block_combat_but_other_hooks_do(entity,body):
    model,state,entry,behavior,snapshot,cards=fixtures()
    entry['entity_id']=entity
    entry['mechanics']={'ShouldShowMoveInBestiary':body}
    model=MonsterRules(snapshot,cards,behavior)
    state=replace(state,enemies=(replace(state.enemies[0],entity_id=entity),))
    assert unmodeled_special_hooks(entry)==set()
    assert model.audit(state)[0]['unmodeled_special_hooks']==[]
    assert model.step(state)[0][1].discard_composition==(('CARD.SLIMED',2),)
    entry['mechanics']['AfterDeath']='UnknownCombatEffect();'
    assert unmodeled_special_hooks(entry)=={'AfterDeath'}
    with pytest.raises(UnsupportedEffect,match='monster_special_hooks_unmodeled'):
        model.step(state)
    entry['mechanics']={'ShouldShowMoveInBestiary':'UnknownCombatEffect();'}
    assert unmodeled_special_hooks(entry)=={'ShouldShowMoveInBestiary'}


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
    entry['moves'][1]['execution_evidence']='await CreatureCmd.GainBlock(base.Creature, 6, ValueProp.Move, null);'
    assert model.step(state)[0][1].enemies[0].block==6


def test_nonrepeat_and_weighted_random_selection_keep_real_probability():
    model,state,entry,*_=fixtures()
    c=deepcopy(entry['moves'][0]);c.update(variable='c',state_id='HIT2')
    entry['moves'].append(c)
    entry['state_graph']['nodes'] += [dict(variable='c',state_id='HIT2',kind='MoveState'),dict(variable='r',kind='RandomBranchState',branches=[
        dict(target='a',weight_expression='1',repeat_type='CannotRepeat',cooldown=0),
        dict(target='b',weight_expression='100',repeat_type='CannotRepeat',cooldown=0),
        dict(target='c',weight_expression='3',repeat_type='CannotRepeat',cooldown=0)])]
    entry['state_graph']['edges'][1]['target']='r'
    results=model.step(state)
    assert [(p,s.enemies[0].move_state) for p,s,_ in results]==[(.25,'a'),(.75,'c')]


def test_condition_uses_hp_after_player_damage_and_csharp_integer_threshold():
    model,state,entry,*_=fixtures()
    entry['state_graph']['nodes'].append(dict(variable='cond',kind='ConditionalBranchState',branches=[
        dict(target='a',condition_expression='base.Creature.CurrentHp >= base.Creature.MaxHp / 2'),
        dict(target='b',condition_expression='base.Creature.CurrentHp < base.Creature.MaxHp / 2')]))
    entry['state_graph']['edges'][1]['target']='cond'
    for hp,dest in [(50,'a'),(49,'b')]:
        after=model.step(replace(state,enemies=(replace(state.enemies[0],max_hp=101,hp=hp),)))[0][1]
        assert after.enemies[0].move_state==dest


def test_state_update_and_strength_buff_change_followup_damage():
    model,state,entry,*_=fixtures()
    entry['state_graph']['definitions']={'Charged':'_charged'}
    entry['moves'][1]['state_updates']=[dict(variable='Charged',value_expression='true')]
    entry['moves'][1]['execution_evidence']='await PowerCmd.Apply<StrengthPower>(ctx, base.Creature, 5m, base.Creature, null);'
    enemy=replace(state.enemies[0],runtime_fields=(('_charged',False),))
    after=model.step(replace(state,enemies=(enemy,)))[0][1]
    assert after.enemies[0].runtime_fields==(('_charged',True),)
    assert after.enemies[0].strength==5 and after.enemies[0].attacks==(9,)


def test_new_debuff_keeps_full_duration_and_expired_enemy_weak_is_removed():
    model,state,entry,*_=fixtures()
    entry['moves'][1]['execution_evidence']='await PowerCmd.Apply<FrailPower>(ctx, targets, 2m, base.Creature, null);'
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
    entry['moves'][1]['execution_evidence']=source
    with pytest.raises(UnsupportedEffect):model.step(state)


def test_unavailable_history_and_version_mismatch_stop_prediction():
    model,state,entry,*_=fixtures()
    entry['state_graph']['nodes'].append(dict(variable='r',kind='RandomBranchState',branches=[
        dict(target='a',weight_expression='1',repeat_type='UseOnlyOnce',cooldown=0)]))
    entry['state_graph']['edges'][1]['target']='r'
    with pytest.raises(UnsupportedEffect,match='history'):model.step(replace(state,enemies=(replace(state.enemies[0],history_complete=False),)))
    model.compatible=False
    with pytest.raises(UnsupportedEffect,match='version'):model.step(state)


def test_condition_subset_never_executes_calls_or_attributes():
    for value in ['__import__("os").system("echo dangerous")','hp.__class__','broken(']:
        with pytest.raises(UnsupportedEffect):expression(value,{'hp':2})
    assert expression('true || MissingField',{}) is True


def test_missing_runtime_field_is_not_guessed_from_turn_one_initializer():
    model,state,entry,*_=fixtures()
    entry['state_graph']['state_fields']=[dict(name='_phase',initial_expression='false')]
    entry['state_graph']['nodes'].append(dict(variable='c',kind='ConditionalBranchState',branches=[
        dict(target='a',condition_expression='_phase')]))
    entry['state_graph']['edges'][1]['target']='c'
    prepared=model.prepare(state)
    assert not prepared.enemies[0].runtime_fields
    with pytest.raises(UnsupportedEffect,match='unknown_condition'):model.step(prepared)
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
    assert scenario['coverage_kind']=='local_behavior_adapters'
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
