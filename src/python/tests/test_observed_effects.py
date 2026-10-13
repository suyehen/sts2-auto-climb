from copy import deepcopy
from autoslay.telemetry.effects import observe,archived


def snapshot():
    return dict(session_epoch='epoch',game_version='0.111.0',state=dict(run_id='run',phase='event',player=dict(
        creature=dict(hp=31,max_hp=80,block=0,powers=[]),gold=139,energy=0,deck=[dict(instance_id='bash',entity_id='CARD.BASH',name='痛击',upgrade_level=0,description='造成8点伤害。')],relics=[],potions=[])))


def test_actual_gold_hp_and_enchantment_are_facts_without_utility():
    before=snapshot();after=deepcopy(before);after['state']['player']['gold']+=64
    result=observe(before,after)
    assert result['resources']['gold']['change']==64 and result['resources']['hp']['change']==0
    assert 'utility' not in result
    after['state']['player']['deck'][0].update(enchantment_id='ENCHANTMENT.SHARP',description='造成10点伤害。')
    assert observe(before,after)['collections']['deck']['modified'][0]['changes']['enchantment_id']['after']=='ENCHANTMENT.SHARP'


def test_unresolved_dispatch_and_other_session_never_confirm_effects():
    before=snapshot();after=deepcopy(before);after['state']['player']['gold']+=64
    assert observe(before,after,'unknown')['status']=='unavailable'
    after['session_epoch']='different'
    assert observe(before,after)['status']=='unavailable'


def test_draw_result_and_enemy_disappearance_do_not_invent_damage_attribution():
    before=snapshot();after=deepcopy(before)
    before['state']['enemies']=[dict(instance_id='minion',name='爪牙',hp=50)]
    after['state']['enemies']=[]
    result=observe(before,after)
    assert result['creatures'][-1]['removed'] and '不能' in result['creatures'][-1]['note']


def test_archive_matches_exact_request_epoch_run_and_version():
    before=snapshot();before['state_version']=1;after=deepcopy(before);after['state_version']=2;after['state']['player']['gold']+=64
    decision=dict(event_id='decision',snapshot=before,report=dict(source=dict(session_epoch='epoch',run_id='run',state_version=1)))
    entries=[dict(event_id='choice',kind='ai_choice',details=dict(analysis_id='decision',steps=[dict(action_id='take_gold',target_id=None)])),
             dict(event_id='submit',kind='action_submitted',request=dict(request_id='req',session_epoch='epoch',run_id='run',expected_state_version=1,action_id='take_gold')),
             dict(event_id='result',kind='action_result',request=dict(request_id='req'))]
    class Repo:
        def load_event(self,identity):return {'submit':{'snapshot':before},'result':{'snapshot':after,'receipt':{'status':'completed'}}}[identity]
    assert archived(Repo(),decision,entries)[0]['resources']['gold']['change']==64
    entries[1]['request']['session_epoch']='different'
    assert archived(Repo(),decision,entries)==[]


def test_later_step_without_before_snapshot_cannot_reuse_plan_start():
    before=snapshot();after=deepcopy(before);after['state']['player']['gold']+=64
    decision=dict(event_id='decision',snapshot=before,report=dict(source=dict(
        session_epoch='epoch',run_id='run',state_version=1)))
    request=dict(request_id='req',session_epoch='epoch',run_id='run',expected_state_version=2)
    entries=[dict(event_id='submit',kind='action_submitted',request=request),
             dict(event_id='outcome',kind='agent_outcome',details=dict(
                 analysis_id='decision',request=request,plan_step=2))]
    class Repo:
        def load_event(self,identity):
            return {'submit':{},'outcome':dict(snapshot=after,details=dict(
                status='completed',request=request))}[identity]
    assert archived(Repo(),decision,entries)[0]['status']=='unavailable'
    request['expected_state_version']=1
    assert archived(Repo(),decision,entries)[0]['resources']['gold']['change']==64


def test_awaiting_choice_records_observation_without_applying_enchantment():
    before=snapshot();after=deepcopy(before);after['state']['phase']='card_selection'
    result=observe(before,after,'awaiting_choice')
    assert result['status']=='observed'
    assert result['collections']['deck']['modified']==[]


def test_route_first_action_is_joined_with_exact_source():
    from unittest.mock import patch
    before=snapshot();after=deepcopy(before);after['state']['phase']='combat'
    decision=dict(event_id='decision',snapshot=before,report=dict(source=dict(
        session_epoch='epoch',run_id='run',state_version=1)))
    entries=[dict(event_id='choice',kind='local_choice',details=dict(analysis_id='decision',group='route',index=0)),
             dict(event_id='submit',kind='action_submitted',request=dict(request_id='req',
                 session_epoch='epoch',run_id='run',expected_state_version=1,action_id='go')),
             dict(event_id='result',kind='action_result',request=dict(request_id='req'))]
    class Repo:
        def load_event(self,identity):
            return {'submit':{},'result':dict(snapshot=after,receipt=dict(status='completed'))}[identity]
    with patch('autoslay.calibration.store.candidates',return_value=[dict(first_action_id='go')]):
        assert archived(Repo(),decision,entries)[0]['phase_after']=='combat'
