from copy import deepcopy
from autoslay.decision.mechanisms import inspect, inventory, StateTracker
from autoslay.knowledge_base.behavior import load
from autoslay.agent.context import build
from autoslay.telemetry.decisions import packet, public_snapshot


def snapshot(value=False, version=1, combat='combat1'):
    data=load()
    return dict(schema_version='1.0',game_version=data['game_version'],game_assembly_sha256=data['game_assembly_sha256'],
                connected=True,session_epoch='epoch1',state_version=version,
                state=dict(phase='combat',run_id='run1',combat_instance_id=combat,player={},
                           enemies=[dict(entity_id='MONSTER.FROG_KNIGHT',instance_id='frog1',name='青蛙骑士',
                                         runtime_state=dict(fields={'_hasBeetleCharged':value}))]))


def test_missing_field_never_uses_initial_false_or_intent_inference():
    snap=snapshot();snap['state']['enemies'][0]['runtime_state']={}
    result=inspect(snap,tracker=StateTracker())
    field=result['enemies'][0]['fields'][0]
    assert field['status']=='unknown' and field['value'] is None
    assert field['initial_expression']=='false' and result['unknown_state_count']==1
    assert result['classification']=='ai_required' and not result['execution_authorized']


def test_tracking_changes_are_instance_combat_epoch_and_version_scoped():
    tracker=StateTracker()
    assert inspect(snapshot(),tracker=tracker)['tracking']['changes']==[]
    changed=inspect(snapshot(True,2),tracker=tracker)['tracking']['changes']
    assert changed[0]['before'] is False and changed[0]['after'] is True
    assert inspect(snapshot(False,1),tracker=tracker)['tracking']['status']=='stale'
    assert inspect(snapshot(False,3,combat='combat2'),tracker=tracker)['tracking']['changes']==[]
    new=snapshot(False,4);new['session_epoch']='new'
    assert inspect(new,tracker=tracker)['tracking']['changes']==[]


def test_mismatched_reference_blocks_field_use_and_keeps_unknowns():
    snap=snapshot();snap['game_assembly_sha256']='new_build'
    result=inspect(snap,tracker=StateTracker())
    assert result['unknown_state_count']==1 and not result['enemies'][0]['reference_matches']
    assert result['enemies'][0]['branch_calculation']=='ai_required'


def test_packet_and_archive_preserve_audit_without_mutating_state():
    snap=snapshot();before=deepcopy(snap)
    audit=inspect(snap,dict(combat={'candidates':[{}]}),tracker=StateTracker())
    report=dict(source={},phase='combat',mechanism_audit=audit)
    assert audit['classification']=='estimated'
    assert packet(report)['mechanism_audit']==audit
    assert build(snap,report)['mechanism_audit']==audit
    assert public_snapshot(snap)['state']['combat_instance_id']=='combat1'
    assert snap==before


def test_full_inventory_does_not_claim_reference_is_executable():
    result=inventory()
    assert len(result['entries'])==596+66+299+190
    assert all(e['tier']=='reference_only' for e in result['entries'] if e['category']=='monster')
    assert result['counts']['adapter']>0 and result['counts']['ai_required']>0


def test_local_proof_does_not_claim_full_future_simulation():
    result=inspect(snapshot(),dict(autonomy={'eligible':True}),tracker=StateTracker())
    assert result['classification']=='exact'
    assert result['classification_scope']=='current local execution proof'
    assert result['metric_scopes']['enemy_branches']=='ai_required'
    assert result['metric_scopes']['win_probability']=='not_calibrated'
