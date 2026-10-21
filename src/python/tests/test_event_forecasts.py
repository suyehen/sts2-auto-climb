from copy import deepcopy
from autoslay.knowledge_base.behavior import load
from autoslay.knowledge_base.templates import index
from autoslay.evaluation.event_sequences import evaluate_event_sequence
from autoslay.knowledge_base.event_model import terminal_resource_evidence
from autoslay.evaluation.event_sequences import evaluate_graph
from autoslay.evaluation.run import RunContext,RunPolicy
from autoslay.calibration.events import compare,summarize


def fixture(identity,stage,keys,variables=None):
    data=load()
    return dict(entity_id='EVENT.'+identity,game_assembly_sha256=data['game_assembly_sha256'],
                runtime_state={'fields':{('_numberOfDigs' if identity=='COLOSSAL_FLOWER' else '_decipherCount'):stage}},
                dynamic_variables=[dict(name=k,value=v) for k,v in (variables or {}).items()],
                options=[dict(text_key=identity+'.pages.'+k,action_id=k,option_id=k) for k in keys])


def evaluate(event,hp=40,maximum=80):
    deck=[dict(entity_id='CARD.STRIKE_IRONCLAD',instance_id='strike',upgrade_level=0)]
    return evaluate_event_sequence(event,deck,RunContext(hp,maximum,0),index()[0],RunPolicy(),'0.111.0')


def test_unknown_future_choice_prevents_claim_of_optimum():
    graph={'now':[dict(option_id='stop',effects=[]),dict(option_id='continue',effects=[],next_state='later')],
           'later':[dict(option_id='known',effects=[]),dict(option_id='unknown',effects=[dict(kind='mystery')])]}
    result=evaluate_graph(graph,'now',[dict(entity_id='CARD.STRIKE_IRONCLAD')],RunContext(40,80,0),index()[0],RunPolicy())
    assert not result['known'] and not result['comparison_complete']
    assert result['best_known']['option_id']=='stop'
    assert next(c for c in result['candidates'] if c['option_id']=='continue')['utility'] is None


def test_flower_reads_current_counter_and_keeps_unmodeled_relic_unknown():
    event=fixture('COLOSSAL_FLOWER',1,['REACH_DEEPER_1.options.EXTRACT_CURRENT_PRIZE_2','REACH_DEEPER_1.options.REACH_DEEPER_2'])
    result=evaluate(event); rows={r['option_id']:r for r in result['candidates']}
    assert result['supported'] and not result['comparison_complete']
    assert rows['ExtractCurrentPrize']['expected_gold_change']==75
    assert rows['ReachDeeper']['branches'][0]['immediate']['hp_after']==34
    assert rows['ReachDeeper']['utility'] is None
    event['runtime_state']['fields'].clear();assert not evaluate(event)['supported']
    event['game_assembly_sha256']='changed';assert not evaluate(event)['supported']


def test_tablet_random_upgrade_consumes_current_pool_then_reobserves():
    event=fixture('TABLET_OF_TRUTH',0,['INITIAL.options.DECIPHER_1','INITIAL.options.SMASH'],{'DecipherMaxHpLoss':3})
    before=deepcopy(event); result=evaluate(event)
    assert result['supported'] and result['comparison_complete']
    row=next(r for r in result['candidates'] if r['option_id']=='Decipher')
    branch=row['branches'][0]; assert branch['probability']==1
    assert branch['immediate']['max_hp_after']==77 and branch['immediate']['deck_after'][0]['upgrade_level']==1
    again=next(c for c in branch['continuation']['candidates'] if c['option_id']=='Decipher')
    assert again['branches'][0]['immediate']['deck_after'][0]['upgrade_level']==1
    assert event==before and not result['execution_authorized']


def test_tablet_lethal_cost_and_multi_card_probabilities():
    event=fixture('TABLET_OF_TRUTH',1,['DECIPHER_1.options.DECIPHER','DECIPHER.options.GIVE_UP'],{'DecipherMaxHpLoss':6})
    result=evaluate(event,hp=4,maximum=6)
    row=next(r for r in result['candidates'] if r['option_id']=='Decipher')
    assert row['fatal_probability']==1
    deck=[dict(entity_id='CARD.STRIKE_IRONCLAD',instance_id='s',upgrade_level=0),dict(entity_id='CARD.DEFEND_IRONCLAD',instance_id='d',upgrade_level=0)]
    result=evaluate_event_sequence(event,deck,RunContext(40,80,0),index()[0],RunPolicy(),'0.111.0')
    row=next(r for r in result['candidates'] if r['option_id']=='Decipher')
    assert [b['probability'] for b in row['branches']]==[.5,.5]


def test_terminal_does_not_ignore_hidden_effect_call_or_dynamic_variable():
    entry=next(e for e in load()['entries'] if e['entity_id']=='EVENT.THE_LANTERN_KEY')
    option=next(o for o in entry['options'] if o.get('handler')=='ReturnTheKey')
    assert terminal_resource_evidence(option,entry['event_model'],{})==[dict(kind='gain_gold',amount=100)]
    model=deepcopy(entry['event_model']); model['methods']['ReturnTheKey']['evidence']+=';UnknownReward();'
    import pytest
    with pytest.raises(ValueError): terminal_resource_evidence(option,model,{})


def test_random_residual_is_not_a_rule_failure_and_no_weight_update():
    before=dict(creature=dict(hp=40,max_hp=80),gold=0);after=dict(creature=dict(hp=37,max_hp=80),gold=10)
    candidate=dict(branches=[dict(probability=.5,immediate=dict(hp_after=37,max_hp_after=80,gold_after=10)),dict(probability=.5,immediate=dict(hp_after=37,max_hp_after=80,gold_after=20))])
    result=compare(candidate,before,after)
    assert result['residual']['gold']==-5 and result['within_modeled_support']['gold']
    assert summarize([result])['metrics']['gold']['mean_absolute_error']==5
    assert summarize([])['status']=='awaiting_live_samples'
    assert not result['weight_update_applied']


def test_agent_action_records_only_matching_settled_live_outcomes(tmp_path):
    from autoslay.calibration.events import record_action,runtime_validation
    event=fixture('TABLET_OF_TRUTH',0,['INITIAL.options.DECIPHER_1','INITIAL.options.SMASH'],{'DecipherMaxHpLoss':3})
    before=dict(connected=True,session_epoch='s',state_version=1,game_version='0.111.0',game_assembly_sha256=event['game_assembly_sha256'],
                state=dict(phase='event',floor=2,run_id='r',event_screen=event,player=dict(creature=dict(hp=40,max_hp=80),gold=0)))
    after=deepcopy(before);after['state_version']=2;after['state']['player']['creature']['hp']=60
    report=dict(analysis_id='test',event_sequence=evaluate(event))
    result=record_action(report,before,after,'INITIAL.options.SMASH',{'status':'completed'},tmp_path)
    assert result['residual']['hp']==0 and len(list((tmp_path/'observations').glob('*.json')))==1
    unmodeled=dict(analysis_id='unknown')
    unknown=record_action(unmodeled,before,after,'INITIAL.options.SMASH',{'status':'completed'},tmp_path)
    assert unknown['predicted_change']=={} and unknown['actual_change']['hp']==20
    after['state']['run_id']='other'
    assert record_action(report,before,after,'INITIAL.options.SMASH',{'status':'completed'},tmp_path) is None
    assert runtime_validation(before)['status']=='observed'
    before['state']['event_screen']['runtime_state']['fields'].clear()
    assert runtime_validation(before)['missing_fields']==['_decipherCount']


def test_ai_packet_keeps_forecast_but_full_tree_stays_local():
    from autoslay.telemetry.decisions import event_sequence_packet
    event=fixture('TABLET_OF_TRUTH',0,['INITIAL.options.DECIPHER_1','INITIAL.options.SMASH'],{'DecipherMaxHpLoss':3})
    report=evaluate(event);before=deepcopy(report);packet=event_sequence_packet(report)
    branch=next(c for c in packet['candidates'] if c['option_id']=='Decipher')['branches'][0]
    assert 'candidates' not in branch['continuation'] and branch['continuation']['full_tree_available_locally']
    assert report==before
