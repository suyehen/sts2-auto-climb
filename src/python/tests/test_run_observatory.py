from autoslay.telemetry import runs,decisions

def snapshot(act=0,epoch='one',identity='saved-run',version=1,nodes=True):
    return dict(connected=True,session_epoch=epoch,state_version=version,state=dict(run_id='runtime-'+epoch,
        run_history_id=identity,act_count=4,phase='combat',floor=act*20+2,room_type='MonsterRoom',player=dict(turn=2),
        map=dict(act_index=act,generation=act,current_position=dict(column=1,row=2),visited=[dict(column=1,row=2)],
                 nodes=[dict(node_id='n',coordinate=dict(column=1,row=2),children=[],type='Monster')] if nodes else [])))

def test_fourth_act_has_its_own_map_and_closed_map_does_not_erase(tmp_path):
    for act in range(4): runs.observe(snapshot(act,version=act+1),tmp_path)
    runs.observe(snapshot(3,version=9,nodes=False),tmp_path)
    tree=runs.tree(tmp_path)
    assert len(tree)==1 and set(tree[0]['acts'])=={'0','1','2','3'}
    assert tree[0]['acts']['3']['maps'][0]['map']['nodes'][0]['node_id']=='n'
    assert len(tree[0]['acts']['3']['timeline'])==2

def test_saved_identity_survives_restart_but_new_run_separates(tmp_path):
    runs.observe(snapshot(epoch='one'),tmp_path); runs.observe(snapshot(epoch='two'),tmp_path)
    assert len(runs.tree(tmp_path))==1
    runs.observe(snapshot(epoch='two',identity='different'),tmp_path)
    assert len(runs.tree(tmp_path))==2

def test_action_status_without_snapshot_joins_exact_request_node(tmp_path):
    snap=snapshot(); request=dict(request_id='exact',run_id='runtime-one',session_epoch='one',expected_state_version=1,action_id='play_a')
    runs.action('action_submitted',request,dict(snapshot=snap),tmp_path)
    runs.action('action_result',dict(request_id='exact'),dict(status='completed'),tmp_path)
    entries=runs.tree(tmp_path)[0]['acts']['0']['timeline']
    result=next(e for e in entries if e['kind']=='action_result')
    assert result['receipt']['status']=='completed' and result['location']['node_key']=='1:2'

def test_full_run_history_exceeds_flat_dropdown_limit_and_replays_stay_separate(tmp_path):
    snap=snapshot()
    for i in range(105): decisions.archive(dict(analysis_id=f'd_{i}',source={},phase='combat'),snap,'live',tmp_path)
    decisions.archive(dict(analysis_id='offline',source={},phase='combat'),snap,'replay',tmp_path)
    timeline=runs.tree(tmp_path)[0]['acts']['0']['timeline']
    assert len([e for e in timeline if e['kind']=='decision'])==105
    assert not any(e.get('record_id')=='offline' for e in timeline)

def test_runtime_only_ids_do_not_merge_restarts(tmp_path):
    for epoch in ('one','two'):
        s=snapshot(epoch=epoch);s['state'].pop('run_history_id');runs.observe(s,tmp_path)
    assert len(runs.tree(tmp_path))==2

def test_no_secrets_or_rng_in_observations(tmp_path):
    s=snapshot();s['token']='secret';s['state']['rng']='secret';runs.observe(s,tmp_path)
    assert 'secret' not in str(runs.events(tmp_path))

def test_explicit_comparison_and_legacy_combat_are_archived(tmp_path,monkeypatch):
    monkeypatch.setattr(decisions,'DIRECTORY',tmp_path)
    snap=snapshot()
    result=dict(candidates=[dict(utility=3)])
    pointer=decisions.archive_tool_result(result,snap,'add',[dict(entity_id='CARD.A')])
    record=decisions.load(pointer['record_id'])
    assert record['report']['choices']['candidates'][0]['utility']==3
    assert record['location']['node_key']=='1:2'
    pointer=decisions.archive_tool_result(dict(candidates=[],source={}),snap,'combat')
    assert 'combat' in decisions.load(pointer['record_id'])['report']

def test_travel_result_does_not_store_next_act_map_under_previous_act(tmp_path):
    before=snapshot(0);after=snapshot(1,version=9);after.pop('connected')
    request=dict(request_id='travel',run_id='runtime-one',session_epoch='one',expected_state_version=1,action_id='travel')
    runs.action('action_submitted',request,dict(snapshot=before),tmp_path)
    runs.action('action_result',dict(request_id='travel'),dict(status='completed',snapshot=after),tmp_path)
    acts=runs.tree(tmp_path)[0]['acts']
    assert set(acts)=={'0','1'}
    assert acts['0']['maps'][0]['map']['act_index']==0
    assert acts['1']['maps'][0]['map']['act_index']==1
    assert any(e['kind']=='action_result' for e in acts['0']['timeline'])
    assert not any(e['kind']=='action_result' for e in acts['1']['timeline'])
