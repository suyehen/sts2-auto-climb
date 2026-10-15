import json,threading
from urllib.request import Request,urlopen
from urllib.error import HTTPError
import pytest
from autoslay.dashboard.server import make_server
from autoslay.archive.repository import for_legacy
from test_run_observatory import snapshot

@pytest.fixture
def service(tmp_path):
    legacy=tmp_path/'decisions';repo=for_legacy(legacy)
    s=snapshot();s['state']['player']['character']='IRONCLAD';s['state']['ascension']=5
    repo.store(dict(event_id='test_api',kind='snapshot',recorded_at='2026-10-10T00:00:00+00:00',snapshot=s))
    server=make_server(0,legacy);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield repo,'http://127.0.0.1:'+str(server.server_port)
    server.shutdown();server.server_close();thread.join()

def get(url):return urlopen(Request(url,headers={'X-Autoslay-ReadOnly':'1'}),timeout=3)


def test_live_projects_monster_state_machines_without_any_decision_report(service):
    from test_monster_graphs import snapshot as combat_snapshot
    repo,url=service;s=combat_snapshot()
    from test_run_observatory import snapshot as run_snapshot
    location=run_snapshot()['state'];s['state'].update({k:location[k] for k in ('run_id','run_history_id','map')})
    repo.store(dict(event_id='graph_state',kind='snapshot',snapshot=s))
    data=json.load(get(url+'/api/live'))
    assert data['decision'] is None
    assert len(data['monster_graphs'])==3
    assert all(g['next_moves'][0]['probability']==1 for g in data['monster_graphs'])

def test_catalog_filters_tree_offline_and_pagination(service):
    repo,url=service
    data=json.load(get(url+'/api/archives?date=2026-10-10&character=IRONCLAD&ascension=5'))
    assert data['total']==1
    id=data['records'][0]['archive_id']
    tree=json.load(get(url+'/api/archive?id='+id));assert tree['statistics']['visits'][0]['node_key']=='1:2'
    page=json.load(get(url+'/api/archive/timeline?id='+id+'&limit=1'))
    assert page['events'][0]['event_id']=='test_api'
    assert json.load(get(url+'/api/archive/timeline?id='+id+'&after='+str(page['next_cursor'])))['events']==[]
    assert json.load(get(url+'/api/archives?date=2026-10-11'))['total']==0

def test_origin_guard_and_sse_cursor(service):
    repo,url=service
    with pytest.raises(HTTPError) as exc:urlopen(url+'/api/archives')
    assert exc.value.code==403
    with get(url+'/api/stream?after=0') as stream:
        assert stream.readline().decode().strip()=='event: archive'
        value=json.loads(stream.readline().decode()[6:]);assert value['events'][0]['event_id']=='test_api'
        assert value['cursor']==repo.latest_cursor()


def test_evidence_matches_local_and_ai_results_to_their_analysis(service):
    repo,url=service
    s=snapshot()
    for identity in ('flow_local','flow_ai'):
        repo.store(dict(event_id=identity,kind='decision',snapshot=s,
                        report=dict(analysis_id=identity,source={})))
    for event_id,kind,detail in [
        ('flow_local_choice','local_choice',dict(analysis_id='flow_local',ai_called=False,steps=[dict(action_id='end_turn')])),
        ('flow_local_result','local_outcome',dict(analysis_id='flow_local',status='completed',actual=dict(hp=42))),
        ('flow_ai_choice','ai_choice',dict(analysis_id='flow_ai',reason='保留资源')),
        ('flow_ai_result','agent_outcome',dict(analysis_id='flow_ai',status='failed')),
    ]:
        repo.store(dict(event_id=event_id,kind=kind,snapshot=s,details=detail))
    local=json.load(get(url+'/api/evidence?id=flow_local'))
    assert local['choice']['ai_called'] is False
    assert [o['status'] for o in local['observations']]==['completed']
    ai=json.load(get(url+'/api/evidence?id=flow_ai'))
    assert ai['choice']['reason']=='保留资源'
    assert [o['status'] for o in ai['observations']]==['failed']

def test_dashboard_modules_and_bounded_local_catalog(service):
    _,url=service
    for asset,kind in [('workspace.js','text/javascript'),('presentation.js','text/javascript'),
                       ('map.js','text/javascript'),('history-model.js','text/javascript'),('library.js','text/javascript'),('workbench.css','text/css'),('sidebar.js','text/javascript'),('sidebar-combat.js','text/javascript'),('sidebar-view.js','text/javascript'),('combat-forecast.js','text/javascript'),('services.js','text/javascript')]:
        with urlopen(url+'/'+asset) as response:
            assert response.status==200
            assert response.headers['Content-Type'].startswith(kind)
            assert "connect-src 'self' http://127.0.0.1:18770" in response.headers['Content-Security-Policy']
    all_data=json.load(get(url+'/api/catalog'))
    assert len(all_data['entries'])==100 and all_data['total']>100
    from urllib.parse import quote
    identity=all_data['entries'][0]['entity_id']
    subset=json.load(get(url+'/api/catalog?q='+quote(identity)))
    assert any(e['entity_id']==identity for e in subset['entries'])
    assert json.load(get(url+'/api/catalog?q=does_not_exist_173819'))['entries']==[]
    with pytest.raises(HTTPError) as exc:urlopen(url+'/api/catalog')
    assert exc.value.code==403

def test_classified_catalog_and_local_portrait_routes(service):
    _,url=service
    page=json.load(get(url+'/api/catalog?category=card&owner=ironclad&include_shared=0&limit=2&page=1'))
    next_page=json.load(get(url+'/api/catalog?category=card&owner=ironclad&include_shared=0&limit=2&page=2'))
    assert len(page['entries'])==2 and page['total']>2
    assert all(e['classification']['scope']=='ironclad' for e in page['entries'])
    assert {e['entity_id'] for e in page['entries']}.isdisjoint(e['entity_id'] for e in next_page['entries'])
    assert all(e['variants'] for e in page['entries'])
    for path in ['/assets/cards/CARD.BRIGHTEST_FLAME.webp','/assets/potions/POTION.FIRE_POTION.webp','/assets/relics/RELIC.BURNING_BLOOD.webp','/assets/map/MAP.MAP_MONSTER.webp']:
        with urlopen(url+path) as response:
            assert response.headers['Content-Type'].startswith('image/webp')
            assert response.read(4)==b'RIFF'
    for path,status in [('/assets/cards/portraits.json',400),('/assets/cards/..%2Fportraits.json',400),('/assets/cards/POTION.FIRE_POTION.webp',400),('/assets/cards/CARD.DOES_NOT_EXIST.webp',404),('/assets/map/MAP...%2Foutside.webp',400),('/assets/map/CARD.STRIKE_IRONCLAD.webp',400)]:
        with pytest.raises(HTTPError) as exc:urlopen(url+path)
        assert exc.value.code==status
