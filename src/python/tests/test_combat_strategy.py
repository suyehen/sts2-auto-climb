from copy import deepcopy
from dataclasses import replace, asdict
import pytest
from autoslay.decision.contracts import Transition, Operation, Potion, UnsupportedEffect
from autoslay.decision.cache import digest
from autoslay.evaluation.combat import CombatPolicy, CombatEvaluator
from autoslay.evaluation.combat_value import ValuePolicy, evaluate_outcome, aggregate
from autoslay.evaluation.potion_value import reserve_cost, compare_pairs
from autoslay.planning.search import search, SearchBudget
from autoslay.simulation.engine import CombatSimulator
from autoslay.planning.combat_strategy import analyze
from autoslay.calibration.combat import build, apply
from autoslay.decision.coverage import diagnose
from autoslay.decision.local_combat import select_calibrated
from autoslay.decision.deterministic import source
from autoslay.telemetry.combat_metrics import summarize
from test_decision import state, card
from test_deterministic import live
from test_monster_simulation import fixtures


def test_diagnostics_expose_modifier_even_when_mechanism_is_primary():
    snapshot=live();snapshot['state']['player']['relics']=[dict(entity_id='RELIC.NEW')]
    snapshot['state']['player']['hand'][0]['affliction_id']='AFFLICTION.UNKNOWN'
    result=diagnose(snapshot,primary='UNMODELED_RELIC:RELIC.NEW')
    assert 'local_combat_card_modifier_not_certified' in {r['code'] for r in result['remaining_after_primary']}
    assert sum(r['is_primary'] for r in result['blockers'])==1


def test_candidate_pool_is_independent_of_display_limit_and_reuses_edges():
    initial=state(card('a','STRIKE_IRONCLAD','Deal 6 damage.'),card('b','STRIKE_IRONCLAD','Deal 6 damage.'),
                  card('c','DEFEND_IRONCLAD','Gain 5 Block.',target='Self'))
    reports=[search(initial,CombatSimulator(initial),CombatEvaluator(),SearchBudget(top_k=k,time_ms=1000)) for k in (1,5)]
    def sequences(r):return {tuple((s['action_id'],s['target_id']) for s in c['steps']) for c in r['decision_candidates']}
    assert sequences(reports[0])==sequences(reports[1])
    assert reports[0]['continuation_cache_hits']>0
    assert all(s['coverage']=='complete' for s in reports[0]['first_action_summaries'])


def test_node_limit_names_unsearched_first_steps_without_fake_upper_bound():
    initial=state(card('a','STRIKE_IRONCLAD','Deal 6 damage.'))
    result=search(initial,CombatSimulator(initial),CombatEvaluator(),SearchBudget(max_nodes=1))
    assert result['truncation_reasons']==['node_limit']
    assert result['unsearched_branches']>0
    assert all(s['upper_bound'] is None for s in result['first_action_summaries'])


def test_utility_tracks_enemy_damage_self_damage_and_healing_separately():
    initial=state(hp=60)
    end=replace(initial,hp=55,enemy_hp_loss=7,self_hp_loss=3,healing=5)
    value=evaluate_outcome(initial,end,2)
    assert (value['enemy_hp_loss'],value['self_hp_loss'],value['healing'])==(7,3,5)
    assert value['ledger_matches']
    assert not evaluate_outcome(initial,replace(end,hp=54),2)['ledger_matches']
    # No extra block bonus once trajectory damage is already accounted for.
    assert evaluate_outcome(initial,replace(end,block=1000),2)['utility']==value['utility']


def test_low_health_increases_hp_cost_and_death_is_a_constraint():
    high=state(hp=80);low=state(hp=10)
    values=[evaluate_outcome(s,replace(s,hp=s.hp-5,enemy_hp_loss=5),1) for s in (high,low)]
    assert values[1]['utility_components']['enemy_hp_loss']<values[0]['utility_components']['enemy_hp_loss']
    dead=evaluate_outcome(low,replace(low,hp=0,enemy_hp_loss=10),1)
    assert aggregate([(1,dead)])['death_upper']==1


@pytest.mark.parametrize('method',['exact','sampled'])
def test_missing_mass_has_unknown_risk_and_interval(method):
    initial=state();value=evaluate_outcome(initial,initial,1)
    result=aggregate([(.5,value),(.5,None)],method=method)
    assert result['lower'] is None and result['upper'] is None and result['death_upper']==1
    assert result['complete'] is False
    if method=='sampled':assert result['covered_probability'] is None
    else:assert result['uncovered_probability']==.5


def planner_fixture():
    model,initial,entry,behavior,snapshot,entries=fixtures()
    strike=card('a','STRIKE_IRONCLAD','Deal 6 damage.')
    initial=replace(initial,hand=(strike,))
    snapshot['state']['legal_actions']=[dict(action_id='end_turn',type='end_turn',allows_no_target=True,allowed_target_ids=[]),
        dict(action_id='play_a',type='play_card',allows_no_target=False,allowed_target_ids=['e'])]
    return model,initial,snapshot,entries


def test_nonattack_and_followup_attack_share_actual_transfer_and_ledger():
    model,initial,snapshot,entries=planner_fixture()
    report=analyze(snapshot,initial,entries,CombatPolicy(),dict(horizon=2,draws=0,branches=32),
                   SearchBudget(time_ms=1000),monster_model=model)
    assert report['search_complete'] and len(report['first_actions'])==2
    end=next(r for r in report['first_actions'] if r['first_step']['action_id']=='end_turn')
    assert end['complete'] and end['loss_upper']==4
    assert end['utility_components']['terminal_pollution']<0
    assert all(r['bound_kind']=='uncalibrated_model' and r['upper'] is None for r in report['first_actions'])


def test_unknown_enemy_compound_effect_keeps_every_alternative_unbounded():
    model,initial,snapshot,entries=planner_fixture()
    model.entries['MONSTER.TEST']['moves'][1]['execution_evidence']+='\nawait UnknownCmd.ChangeResource();'
    report=analyze(snapshot,initial,entries,CombatPolicy(),dict(horizon=1,draws=0),SearchBudget(time_ms=1000),monster_model=model)
    assert not report['search_complete']
    assert 'mechanics_boundary' in report['truncation_reasons']
    assert all(r['upper'] is None for r in report['first_actions'])


@pytest.mark.parametrize('before',[True,False])
def test_attack_and_strength_follow_source_order(before):
    model,initial,entry,*_=fixtures()
    attack=entry['moves'][0]
    buff='await PowerCmd.Apply<StrengthPower>(ctx, base.Creature, 3m, base.Creature, null);'
    attack['execution_evidence']=(buff+attack['execution_evidence']) if before else (attack['execution_evidence']+buff)
    enemy=replace(initial.enemies[0],move_state='a',attacks=(4,),uncertain_intents=())
    outcome=model.step(replace(initial,enemies=(enemy,)),select_next=False)[0][1]
    assert outcome.enemy_hp_loss==(7 if before else 4)
    assert outcome.enemies[0].strength==3


def test_shadow_is_wired_to_full_analysis_without_authorizing_it(monkeypatch):
    from autoslay.decision import unified
    old=unified.decision_config
    def config(path):
        value=old(path)
        if path.name=='autonomy.default.json':value=dict(value,strategy_mode='shadow')
        return value
    monkeypatch.setattr(unified,'decision_config',config)
    snapshot=live();snapshot['state']['enemies'][0]['hp']=100
    report=unified.analyze_decision(snapshot,use_cache=False)
    strategy=report['combat']['strategy']
    assert strategy['mode']=='shadow' and strategy['execution_authorized'] is False
    assert len(strategy['first_actions'])==3
    assert 'combat_diagnostics' in report
    assert report['autonomy'].get('decision_basis')!='calibrated_combat_strategy'


def test_potion_cost_uses_only_observed_overflow_and_supports_duplicates():
    snapshot=live();p=Potion('p','POTION.BLOCK_POTION','Gain 12 Block.','use_potion_p',())
    snapshot['state']['player'].update(potion_slots=2,potions=[dict(entity_id=p.entity_id)])
    one=reserve_cost(snapshot,p)
    snapshot['state']['player']['potions'].append(dict(entity_id=p.entity_id))
    duplicate=reserve_cost(snapshot,p)
    assert duplicate['upper']<one['upper'] and not duplicate['observed_overflow']
    snapshot['state']['reward_screen']=dict(rewards=[dict(type='PotionReward',available=True)])
    assert reserve_cost(snapshot,p)['upper']<duplicate['upper']


def test_use_keep_comparison_charges_multiple_bottles_once():
    rows=[dict(first_step={'action_id':'end_turn'},complete=True,Q_hat=10,lower=10,upper=10,consumed_potions=[]),
          dict(first_step={'action_id':'use_potion_p'},complete=True,Q_hat=50,lower=50,upper=50,consumed_potions=['p','p','q'])]
    compared=compare_pairs(rows,{'p':dict(known=True,lower=5,upper=10),'q':dict(known=True,lower=4,upper=8)})[0]
    assert compared['modeled_gain']==40 and compared['reserve_upper']==18 and compared['decision']=='use'


def test_calibrated_potion_interval_does_not_charge_reserve_twice():
    rows=[dict(first_step={'action_id':'end_turn'},complete=True,Q_hat=10,lower=10,upper=10,consumed_potions=[]),
          dict(first_step={'action_id':'use_potion_p'},complete=True,Q_hat=25,lower=11,upper=15,
               bounds_include_reserve=True,consumed_potions=['p'])]
    result=compare_pairs(rows,{'p':dict(known=True,lower=10,upper=14)})[0]
    assert result['decision']=='use' and result['modeled_gain']==15


def reference_records():
    identity=dict(game_version='0.111.0',policy_fingerprint='weights',model_fingerprint='code',knowledge_revision='source')
    actions=[dict(first_step=dict(action_id='end_turn',target_id=None),Q_hat=10,reference_value=11,
                  simulation_error=0,search_error=0,sampling_error=0,terminal_error=1,
                  predicted_safe=True,reference_safe=True)]
    rows=[dict(**identity,run_id=f'run{i}',split='calibration' if i<10 else 'holdout',state_class='known',
               reference_source='exhaustive_reference',legal_first_steps=[a['first_step'] for a in actions],actions=deepcopy(actions)) for i in range(15)]
    return identity,rows


def test_calibration_uses_independent_runs_holdout_and_invalidates_identity():
    identity,records=reference_records();artifact=build(records,identity)
    assert artifact['cohorts']['known']['authorized']
    rows=[dict(complete=True,Q_hat=10,reserve_lower=2,reserve_upper=4)]
    assert apply(rows,artifact,identity,'known')==[]
    assert (rows[0]['lower'],rows[0]['upper'])==(5,9)
    assert apply(rows,artifact,identity|dict(model_fingerprint='changed'),'known')==['combat_calibration_stale']
    artifact['cohorts']['known']['radius']=0
    assert apply(rows,artifact,identity,'known')==['combat_calibration_invalid']


@pytest.mark.parametrize('mutation',['leakage','missing_action','ai_label','mechanics_error','holdout_failure'])
def test_invalid_reference_or_mechanism_error_never_promotes(mutation):
    identity,records=reference_records()
    if mutation=='leakage':records[-1]['run_id']=records[0]['run_id']
    if mutation=='missing_action':records[0]['legal_first_steps'].append(dict(action_id='play_unknown',target_id=None))
    if mutation=='ai_label':records[0]['reference_source']='ai_choice'
    if mutation=='mechanics_error':records[-1]['actions'][0]['simulation_error']=1
    if mutation=='holdout_failure':records[-1]['actions'][0]['reference_value']=50
    if mutation in {'leakage','missing_action','ai_label'}:
        with pytest.raises(ValueError):build(records,identity)
    else:assert not build(records,identity)['cohorts']['known']['authorized']


def test_gate_needs_every_first_step_bound_and_shadow_never_executes():
    snapshot=live();report=dict(source=source(snapshot),combat=dict(strategy=dict(search_complete=True,calibration_reasons=[],
          calibration_version='test-reference',suggested_first_step=dict(action_id='play_a',target_id='e'),first_actions=[
          dict(first_step=dict(action_id='play_a',target_id='e'),complete=True,death_upper=0,lower=30,upper=32,bound_kind='validated_empirical_interval'),
          dict(first_step=dict(action_id='end_turn',target_id=None),complete=True,death_upper=0,lower=5,upper=8,bound_kind='validated_empirical_interval')])))
    from autoslay.decision.deterministic import operations
    existing={Operation(**r['first_step']) for r in report['combat']['strategy']['first_actions']}
    for op in operations(snapshot['state']['legal_actions']):
        if Operation(**op) not in existing:
            report['combat']['strategy']['first_actions'].append(dict(first_step=op,complete=True,death_upper=0,
                lower=5,upper=8,bound_kind='validated_empirical_interval'))
    policy=dict(strategy_mode='shadow')
    assert select_calibrated(report,snapshot,policy)[0] is None
    policy['strategy_mode']='execute'
    assert select_calibrated(report,snapshot,policy)[0]['decision_basis']=='calibrated_combat_strategy'
    report['combat']['strategy']['first_actions'][1]['upper']=None
    assert 'combat_strategy_alternative_bound_unknown' in select_calibrated(report,snapshot,policy)[1]


def test_combat_metrics_deduplicate_shadow_requests_and_execution():
    def event(identity,kind,**more):return dict(event_id=identity,kind=kind,location=dict(phase='combat',turn=1,visit_id='battle'),**more)
    actions=[dict(action_id='a',allows_no_target=True,allowed_target_ids=[]),dict(action_id='end_turn',allows_no_target=True,allowed_target_ids=[])]
    analysis=event('d','decision',snapshot=dict(state=dict(legal_actions=actions)),report=dict(request_elapsed_ms=5))
    choice=event('c','local_choice',details=dict(analysis_id='d',rule_id='bounded_combat_search'))
    attempt=event('m','agent_audit',details=dict(event='model_attempt',status='started',attempt=2,validation_attempt=2))
    submitted=event('s','action_submitted',request=dict(request_id='r'))
    result=event('r','action_result',request=dict(request_id='r'),receipt=dict(status='completed'))
    metrics=summarize([analysis,choice,choice,attempt,attempt,submitted,result,event('shadow','combat_shadow')])
    assert metrics['choices']['total']==1 and metrics['active_choices']['local']==1
    assert metrics['model_requests']['count']==1 and metrics['model_requests']['correction_event_ids']==['m']
    assert metrics['execution']['success_rate']==1
    assert metrics['cost']['analysis_ms']['local']['median']==5


def test_configuration_accepts_modes_but_requires_explicit_calibration_for_execution():
    from autoslay.configuration import validate_policies
    assert validate_policies({'autonomy':{'strategy_mode':'shadow'}})['autonomy']['strategy_mode']=='shadow'
    with pytest.raises(ValueError,match='CALIBRATION_REQUIRED'):validate_policies({'autonomy':{'strategy_mode':'execute'}})


def test_public_card_variants_keep_upgrade_cost_and_effect_separate():
    from autoslay.planning.lookahead import templates_for,draw_branches
    from autoslay.knowledge_base.templates import index
    snapshot=live();entries=index()[0]
    raw=[dict(entity_id='CARD.STRIKE_IRONCLAD',upgrade_level=level,variant_key=f'CARD.STRIKE_IRONCLAD|{level}',energy_cost=cost,base_energy_cost=cost)
         for level,cost in [(0,1),(1,0)]]
    snapshot['state']['player'].update(deck=raw,hand=[],discard=[],exhaust=[])
    templates=templates_for(snapshot,entries)
    initial=replace(state(),hand=(),draw_composition=tuple((r['variant_key'],1) for r in raw),discard_known=True)
    meta={};branches,method=draw_branches(initial,2,templates,8,42,meta)
    assert method=='exact' and meta['probability_kind']=='exact'
    drawn=branches[0][1].hand
    assert {(c.upgrade_level,c.cost) for c in drawn}=={(0,1),(1,0)}
    sim=CombatSimulator(branches[0][1])
    assert {sim.compiled[c.instance_id].effects[0].amount for c in drawn}=={6,9}


@pytest.mark.parametrize('stars',[0,1,2,3])
def test_draw_continuation_preserves_and_pays_observed_star_cost(stars):
    from autoslay.planning.lookahead import templates_for,draw_branches,ScenarioRules
    from autoslay.knowledge_base.templates import index
    snapshot=live();snapshot['state']['player'].update(hand=[],discard=[],exhaust=[],deck=[
        dict(entity_id='CARD.STRIKE_IRONCLAD',variant_key='star-2',upgrade_level=0,
             energy_cost=1,base_energy_cost=1,star_cost=2)])
    initial=replace(state(),hand=(),stars=stars,draw_composition=(('star-2',1),),discard_known=True)
    drawn=draw_branches(initial,1,templates_for(snapshot,index()[0]),8,42)[0][0][1]
    assert drawn.hand[0].star_cost==2
    sim=CombatSimulator(drawn,ScenarioRules());action=Operation('play_'+drawn.hand[0].instance_id,'enemy')
    assert (action in sim.actions(drawn))==(stars>=2)
    if stars>=2:assert sim.apply(drawn,action).state.stars==stars-2
    else:
        with pytest.raises(UnsupportedEffect):sim.apply(drawn,action)


@pytest.mark.parametrize('changes',[{'star_cost':-2},{'star_cost':True},{'star_cost':None},
                                   {'costs_star_x':True},{'costs_x':True}])
def test_unknown_or_variable_draw_star_cost_retains_unknown_mass(changes):
    from autoslay.planning.lookahead import templates_for,draw_branches
    from autoslay.knowledge_base.templates import index
    raw=dict(entity_id='CARD.STRIKE_IRONCLAD',variant_key='unknown-cost',upgrade_level=0,star_cost=2)
    raw.update(changes)
    snapshot=live();snapshot['state']['player'].update(hand=[],discard=[],exhaust=[],deck=[raw])
    initial=replace(state(),hand=(),stars=10,draw_composition=(('unknown-cost',1),),discard_known=True)
    assert draw_branches(initial,1,templates_for(snapshot,index()[0]),8,42)[0]==[(1.0,None)]


@pytest.mark.parametrize('split',['calibration','holdout'])
def test_false_survival_prediction_blocks_calibration_even_with_zero_value_error(split):
    identity,records=reference_records()
    for row in records:
        value=row['actions'][0];value.update(reference_value=value['Q_hat'],terminal_error=0)
    row=next(r for r in records if r['split']==split)
    row['actions'][0]['reference_safe']=False
    cohort=build(records,identity)['cohorts']['known']
    assert not cohort['authorized'] and not cohort['survival_validated']
    assert cohort['radius']==0 and cohort['misses']==[]
    assert cohort['survival_misses'][0]['run_id']==row['run_id']
    assert cohort['holdout_false_safe_actions']==(1 if split=='holdout' else 0)


@pytest.mark.parametrize('value',[None,1,'true'])
def test_calibration_rejects_missing_or_non_boolean_survival_labels(value):
    identity,records=reference_records()
    records[0]['actions'][0]['predicted_safe']=value
    with pytest.raises(ValueError,match='survival labels'):build(records,identity)


def test_legacy_calibration_without_survival_validation_is_rejected():
    identity,records=reference_records();artifact=build(records,identity)
    artifact['schema_version']='combat-calibration/1.0'
    artifact['cohorts']['known'].pop('survival_validated')
    artifact['fingerprint']=digest({k:v for k,v in artifact.items() if k!='fingerprint'})
    assert apply([],artifact,identity,'known')==['combat_calibration_invalid']


@pytest.mark.parametrize('field,change,power,amount',[
    ('player.strength',{'strength_delta':2},'POWER.STRENGTH_POWER',2),
    ('player.weak_turns',{'weak_turns':2,'player_weak':True},'POWER.WEAK_POWER',2),
    ('player.frail_turns',{'frail_turns':2,'player_frail':True},'POWER.FRAIL_POWER',2),
    ('player.colossus',{'colossus':True},'POWER.COLOSSUS_POWER',1),
    ('player.draw_blocked',{'effect_draw_blocked':True},'POWER.NO_DRAW_POWER',1)])
def test_transition_verification_detects_missing_player_power_and_accepts_matching(field,change,power,amount):
    from autoslay.decision.normalize import normalize
    from autoslay.telemetry.verification import compare_transition
    snapshot=live();initial=normalize(snapshot);prediction=Transition(replace(initial,**change))
    result=compare_transition(prediction,snapshot)
    assert not result['numeric_fields_match']
    assert any(c['field']==field and not c['matches'] for c in result['checks'])
    snapshot['state']['player']['creature']['powers']=[dict(entity_id=power,amount=amount)]
    assert compare_transition(prediction,snapshot)['numeric_fields_match']


@pytest.mark.parametrize('matches',[False,True])
def test_calibrated_executor_stops_on_missing_strength_gain_without_marking(tmp_path,monkeypatch,matches):
    import asyncio
    from autoslay.execution import deterministic
    from autoslay.decision.normalize import normalize
    before=live();before['state']['enemies'][0]['hp']=100
    # Use a reviewed Strength potion; this is a fake local bridge, never a game dispatch.
    step=dict(action_id='use_potion_p',target_id=None)
    before['state']['player']['potions']=[dict(instance_id='p',entity_id='POTION.STRENGTH_POTION',
        description='Gain 2 Strength.',use_action_id=step['action_id'])]
    before['state']['legal_actions'].append(dict(action_id=step['action_id'],type='use_potion',
        allows_no_target=True,allowed_target_ids=[]))
    initial=normalize(before);transition=CombatSimulator(initial).apply(initial,Operation(**step))
    assert transition.boundary is None and transition.state.strength_delta==2
    after=deepcopy(before);after['state_version']+=1
    after['state']['player']['potions']=[]
    if matches:after['state']['player']['creature']['powers']=[dict(entity_id='POWER.STRENGTH_POWER',amount=2)]
    def qualify(snapshot,bounded):
        return dict(eligible=True,source=source(snapshot),selected_steps=[step],reason='test',
            rule_id='calibrated_combat_strategy',decision_basis='calibrated_combat_strategy',
            evidence=dict(policy_identity=dict(policy_fingerprint='survival-policy')))
    monkeypatch.setattr(deterministic,'qualify',qualify)
    async def run():
        submitted=[]
        async def fetch():return deepcopy(after if submitted else before)
        async def submit(request):submitted.append(request);return dict(status='completed')
        executor=deterministic.DeterministicExecutor(tmp_path/'execution',fetch,submit,record=False)
        from autoslay.decision.model_errors import CombatModelError
        if matches:
            result=await executor.execute('strength-check',bounded=True)
        else:
            with pytest.raises(CombatModelError,match='COMBAT_MODEL_PREDICTION_MISMATCH'):
                await executor.execute('strength-check',bounded=True)
            import json
            result=json.loads((tmp_path/'execution/strength-check.json').read_text('utf-8'))
        assert result['prediction_comparison']['numeric_fields_match']==matches
        assert not (deterministic.ROOT/'.runtime/local/agent/combat-strategy-disabled.json').exists()
        # The durable receipt retains mismatch evidence and cannot resubmit.
        again=await executor.execute('strength-check',bounded=True)
        assert again['status']==('completed' if matches else 'failed') and len(submitted)==1
    asyncio.run(run())


def test_unobserved_cost_expiration_and_unknown_enchantment_keep_draw_mass_unknown():
    from autoslay.planning.lookahead import templates_for,draw_branches
    from autoslay.knowledge_base.templates import index
    snapshot=live();snapshot['state']['player'].update(hand=[],discard=[],exhaust=[],deck=[
        dict(entity_id='CARD.STRIKE_IRONCLAD',variant_key='local-cost',energy_cost=0,base_energy_cost=1,has_local_cost_modifiers=True)])
    initial=replace(state(),hand=(),draw_composition=(('local-cost',1),),discard_known=True)
    branches,_=draw_branches(initial,1,templates_for(snapshot,index()[0]),8,42)
    assert branches==[(1.0,None)]


def test_certified_enchantment_amount_is_not_mixed_or_double_counted():
    from autoslay.planning.lookahead import templates_for,draw_branches,ScenarioRules
    from autoslay.knowledge_base.templates import index
    snapshot=live();snapshot['state']['player'].update(hand=[],discard=[],exhaust=[],deck=[
        dict(entity_id='CARD.BASH',variant_key='sharp-2',upgrade_level=0,enchantment_id='ENCHANTMENT.SHARP',enchantment_amount=2),
        dict(entity_id='CARD.BASH',variant_key='sharp-4',upgrade_level=0,enchantment_id='ENCHANTMENT.SHARP',enchantment_amount=4)])
    initial=replace(state(),hand=(),base_strength=3,draw_composition=(('sharp-2',1),('sharp-4',1)),discard_known=True)
    drawn=draw_branches(initial,2,templates_for(snapshot,index()[0]),8,42)[0][0][1]
    sim=CombatSimulator(drawn,ScenarioRules())
    losses={drawn.enemies[0].hp-sim.apply(drawn,Operation('play_'+c.instance_id,'enemy')).state.enemies[0].hp for c in drawn.hand}
    assert losses=={13,15}  # Bash 8 + enchantment 2/4 + observed Strength 3.


def test_canonical_strength_under_weak_recompiles_rounding_before_next_attack():
    from autoslay.planning.lookahead import ScenarioRules
    grow=replace(card('grow','FIGHT_ME','Deal 6 damage twice. Gain 2 Strength.'),canonical=True)
    attack=replace(card('hit','STRIKE_IRONCLAD','Deal 6 damage.'),canonical=True)
    initial=replace(state(grow,attack),player_weak=True,base_strength=1)
    sim=CombatSimulator(initial,ScenarioRules())
    changed=sim.apply(initial,Operation('play_grow','enemy'))
    assert changed.boundary is None
    hit=sim.apply(changed.state,Operation('play_hit','enemy'))
    assert changed.state.enemies[0].hp-hit.state.enemies[0].hp==6


def test_fuzzy_visual_guard_is_reviewed_but_modified_body_is_not(monkeypatch):
    from autoslay.knowledge_base.behavior import load
    from autoslay.rules.monsters import MonsterRules
    from autoslay.knowledge_base.templates import index
    from autoslay.decision.contracts import Enemy
    snapshot=live();snapshot['state']['ascension']=0
    model=MonsterRules(snapshot,index()[0]);entry=model.entries['MONSTER.FUZZY_WURM_CRAWLER']
    enemy=Enemy('w','MONSTER.FUZZY_WURM_CRAWLER',55,0,0,None,(),uncertain_intents=('Buff',),move_state='INHALE')
    initial=replace(state(),enemies=(enemy,),hand=(),turn=2,discard_known=True)
    prepared=model.prepare(initial)
    after=model.step(prepared,select_next=True)[0][1]
    assert after.enemies[0].strength==7 and after.enemies[0].attacks==(11,)
    again=model.step(after,select_next=False)[0][1]
    assert after.hp-again.hp==11
    acid=model.moves(entry)[after.enemies[0].move_state]
    acid['execution_evidence']+='\nawait CreatureCmd.Kill(base.Creature);'
    with pytest.raises(UnsupportedEffect,match='unreviewed_visual_guard|unmodeled_move_call'):
        model.step(after,select_next=False)


def test_potion_null_and_explicit_self_targets_are_both_compared():
    initial=replace(state(),potions=(Potion('p','POTION.BLOCK_POTION','Gain 12 Block.','use_potion_p',('self',),True),))
    actions=CombatSimulator(initial).actions(initial)
    assert Operation('use_potion_p') in actions and Operation('use_potion_p','self') in actions


def test_other_first_step_sampling_does_not_contaminate_exact_lethal():
    from autoslay.knowledge_base.templates import index
    model,initial,snapshot,_=planner_fixture()
    entries=index()[0]
    initial=replace(initial,enemies=(replace(initial.enemies[0],hp=2),),
                    draw_composition=(('CARD.STRIKE_IRONCLAD',1),('CARD.DEFEND_IRONCLAD',6)))
    snapshot['state']['player']['deck']=[dict(entity_id=i,upgrade_level=0) for i in ('CARD.STRIKE_IRONCLAD','CARD.DEFEND_IRONCLAD')]
    report=analyze(snapshot,initial,entries,CombatPolicy(),dict(horizon=2,draws=1,branches=1),
                   SearchBudget(time_ms=1000),monster_model=model)
    assert any(d['method']=='sampled' for d in report['draw_distributions'])
    lethal=next(r for r in report['first_actions'] if r['first_step']['action_id']=='play_a')
    assert lethal['complete'] and lethal['distribution_method']=='exact' and lethal['death_upper']==0


@pytest.mark.parametrize('boundary',[None,'unknown_relic','unknown_card','mechanics','missing_calibration'])
def test_interrupted_upper_bound_requires_closed_reviewed_universe(boundary):
    from autoslay.knowledge_base.templates import index
    model,initial,snapshot,_=planner_fixture()
    entries=index()[0]
    snapshot['state']['player']['deck']=[dict(entity_id='CARD.STRIKE_IRONCLAD',upgrade_level=0)]
    settings=dict(horizon=1,draws=0)
    budget=SearchBudget(max_nodes=1,time_ms=1000)
    baseline=analyze(snapshot,initial,entries,CombatPolicy(),settings,budget,monster_model=model)
    identity,records=reference_records()
    for row in records:
        row.update(baseline['policy_identity'],state_class=baseline['state_class'])
    artifact=build(records,baseline['policy_identity'])
    if boundary=='unknown_relic':snapshot['state']['player']['relics']=[dict(entity_id='RELIC.NEW')]
    if boundary=='unknown_card':initial=replace(initial,draw_composition=(('CARD.NEW',1),))
    if boundary=='mechanics':model.entries['MONSTER.TEST']['mechanics']['AfterDeath']='Unknown();'
    if boundary=='missing_calibration':artifact=None
    report=analyze(snapshot,initial,entries,CombatPolicy(),settings,budget,calibration=artifact,monster_model=model)
    assert not report['search_complete']
    if boundary is None:
        assert report['comparison_bounds_complete']
        assert report['unsearched_global_model_upper']==50
        # Even a complete chosen trajectory has unsearched continuations.
        assert all(r['upper']==51 for r in report['first_actions'])
        assert any(r['complete'] and r['lower'] is not None for r in report['first_actions'])
        assert any(not r['complete'] and r['lower'] is None for r in report['first_actions'])
    else:
        assert report['unsearched_global_model_upper'] is None
        assert all(r['upper'] is None for r in report['first_actions'])


def test_development_profile_selection_keeps_holdout_untouched():
    from autoslay.calibration.combat_profiles import compare
    identity,records=reference_records()
    row=records[0];row.update(split='development',state_id='s')
    row['actions'][0].update(predicted_safe=True,reference_safe=True)
    profiles=[dict(name='baseline',identity=identity,records=[row])]
    assert compare(profiles)['suggested_profile']=='baseline'
    other=deepcopy(profiles[0]);other['name']='other'
    other['records'][0]['actions'][0]['reference_value']=12
    with pytest.raises(ValueError,match='identical'):compare(profiles+[other])
    row['split']='holdout'
    with pytest.raises(ValueError,match='development runs only'):compare(profiles)
