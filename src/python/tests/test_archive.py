import io,zipfile
import pytest
from autoslay.archive.repository import Repository,read
from autoslay.archive import transfer
from autoslay.archive.migration import migrate
from test_run_observatory import snapshot

def put(repo,id='event',at='2026-10-11T15:59:59+00:00',snap=None,**extra):
    return repo.store(dict(event_id=id,kind='snapshot',recorded_at=at,snapshot=snap or snapshot(),**extra))

def test_date_resume_and_multiple_acts(tmp_path):
    r=Repository(tmp_path/'runs');p=put(r);put(r,'next','2026-10-11T16:00:01+00:00',snapshot(3,epoch='two'))
    assert r.folder(p['run_id']).parent.name=='2026-10-11'
    assert len(r.catalog()['records'])==1
    assert set(r.tree(p['run_id'])['acts'])=={'0','3'}
    assert r.manifest(p['run_id'])['visit_count']==2

def test_exact_request_origin_and_dedup(tmp_path):
    r=Repository(tmp_path/'runs');p=put(r);assert put(r)['duplicate']
    r.store(dict(event_id='submit',kind='action_submitted',snapshot=snapshot(),request=dict(request_id='req')))
    r.store(dict(event_id='done',kind='action_result',snapshot=snapshot(1),request=dict(request_id='req'),receipt=dict(status='completed')))
    assert r.load_event('done')['location']['act_index']==0
    assert r.load_event('done')['location']['visit_id']==r.load_event('submit')['location']['visit_id']

def test_clock_gap_pause_and_terminal(tmp_path):
    r=Repository(tmp_path/'runs');s=snapshot();s['state']['control_mode']='auto';s['run_clock']=dict(run_time_seconds=12)
    p=put(r,snap=s);put(r,'b','2026-10-11T16:00:01+00:00',s)
    assert r.manifest(p['run_id'])['observed_auto_seconds']==2
    s['run_clock'].update(result='loss',game_paused=True)
    put(r,'c','2026-10-11T16:00:03+00:00',s);put(r,'d','2026-10-11T16:01:00+00:00',s)
    m=r.manifest(p['run_id']);assert m['observed_auto_seconds']==2 and m['timing_gaps']==1
    assert m['ended_at']=='2026-10-11T16:00:03+00:00' and m['result']=='loss'

def test_export_import_offline_and_source_unchanged(tmp_path):
    r=Repository(tmp_path/'source');p=put(r);before=r.folder(p['run_id']).joinpath('manifest.json').read_bytes()
    target=Repository(tmp_path/'target');result=transfer.import_zip(target,transfer.export(r,p['run_id']))
    assert target.tree(result['archive_id'])['manifest']['origin']=='imported'
    event=target.entries(result['archive_id'])[0];assert target.load_event(event['event_id'])['snapshot']['state']['run_history_id']=='saved-run'
    assert r.folder(p['run_id']).joinpath('manifest.json').read_bytes()==before
    assert transfer.import_zip(target,transfer.export(r,p['run_id']))['reused']
    # Later observing the original save must not write into an imported archive.
    live=put(target,'real_later');assert live['run_id']!=result['archive_id']
    assert target.manifest(result['archive_id'])['origin']=='imported'

def test_import_rejects_paths_and_changed_snapshot(tmp_path):
    r=Repository(tmp_path/'runs');p=put(r)
    blob=io.BytesIO()
    with zipfile.ZipFile(blob,'w') as z:z.writestr('../escape.json','{}')
    with pytest.raises(ValueError):transfer.import_zip(r,blob.getvalue())
    folder=r.folder(p['run_id']);next((folder/'snapshots').glob('*.json')).write_text('{}')
    with pytest.raises(ValueError):transfer.validate(folder)

def test_pending_index_rebuild_and_repeatable_migration(tmp_path):
    r=Repository(tmp_path/'runs');s=snapshot();r.pending_store(dict(event_id='pending',kind='snapshot',snapshot=s))
    assert r.recover_pending()[0]['recorded'];assert r.recover_pending()==[]
    assert r.rebuild()['indexed_events']==1
    legacy=tmp_path/'legacy';(legacy/'observations').mkdir(parents=True)
    from autoslay.archive.repository import atomic
    atomic(legacy/'observations'/'old.json',dict(event_id='old',kind='snapshot',recorded_at='2026-10-10T00:00:00+00:00',snapshot=s))
    assert migrate(r,legacy)['eligible']==1
    migrate(r,legacy,apply=True);migrate(r,legacy,apply=True)
    assert len(r.entries())==2 and (legacy/'observations'/'old.json').exists()

def test_orphan_event_recovery_preserves_original(tmp_path):
    r=Repository(tmp_path/'runs');p=put(r);folder=r.folder(p['run_id']);original=(folder/'events/event.json').read_bytes()
    # Simulate interruption before the first derived manifest was written.
    (folder/'manifest.json').replace(folder/'interrupted-manifest.bak')
    (folder/'timeline.jsonl').write_text('{broken tail')
    report=r.rebuild();assert report['indexed_events']==1 and not report['issues']
    assert r.manifest(p['run_id'])['recovered_manifest']
    assert r.load_event('event')['snapshot']['state']['run_history_id']=='saved-run'
    assert (folder/'events/event.json').read_bytes()==original
