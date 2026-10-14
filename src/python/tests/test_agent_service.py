import asyncio
import json
import threading
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from autoslay.agent.server import make_server
from autoslay.dashboard import server as dashboard


@pytest.fixture
def agent_http(tmp_path,monkeypatch):
    token=tmp_path/'token';token.write_text('local-test-token')
    monkeypatch.setattr('autoslay.agent.server.load_config',lambda:SimpleNamespace(token_file=str(token)))
    calls=[]
    class Fake:
        async def command(self,route,body):
            calls.append((route,body));return dict(active='deepseek',running=False,profiles=[])
    loop=asyncio.new_event_loop()
    thread=threading.Thread(target=loop.run_forever,daemon=True);thread.start()
    srv=make_server(Fake(),loop,0)
    http_thread=threading.Thread(target=srv.serve_forever,daemon=True);http_thread.start()
    yield 'http://127.0.0.1:'+str(srv.server_port),calls
    srv.shutdown();srv.server_close();http_thread.join();loop.call_soon_threadsafe(loop.stop);thread.join();loop.close()


def post(url,body=None,headers=None):
    return urlopen(Request(url,data=json.dumps(body or {}).encode(),headers=headers or {},method='POST'),timeout=3)


def test_service_auth_and_origin_guard(agent_http):
    url,calls=agent_http
    for headers in [{},{'Authorization':'Bearer wrong'},
                    {'Authorization':'Bearer local-test-token','Origin':'http://127.0.0.1:18767'}]:
        with pytest.raises(HTTPError) as exc:post(url+'/status',headers=headers)
        assert exc.value.code==403
    assert not calls
    value=json.load(post(url+'/status',headers={'Authorization':'Bearer local-test-token'}))
    assert value['running'] is False and calls==[('/status',{})]


def test_service_rejects_non_object(agent_http):
    url,calls=agent_http
    with pytest.raises(HTTPError) as exc:
        post(url+'/switch',['bad'],{'Authorization':'Bearer local-test-token'})
    assert exc.value.code==400 and not calls


def test_dashboard_configuration_routes_guard_and_proxy(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(dashboard,'agent_request',lambda route,body:(calls.append((route,body)) or {'ok':True},200))
    srv=dashboard.make_server(0,tmp_path/'archive')
    thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    url='http://127.0.0.1:'+str(srv.server_port)
    try:
        with pytest.raises(HTTPError) as exc:post(url+'/api/config/export',{'mode':'standard'})
        assert exc.value.code==403 and not calls
        for route in ('export','preview','import','backups','restore'):
            assert json.load(post(url+'/api/config/'+route,{'mode':'standard'},{'X-Autoslay-ReadOnly':'1'}))['ok']
            assert calls[-1]==('/config/'+route,{'mode':'standard'})
        assert b'config-controls' in urlopen(url+'/').read()
        assert b'autoslay-config' in urlopen(url+'/configuration.js').read()
        assert b'controlView' in urlopen(url+'/agent-state.js').read()
    finally:
        srv.shutdown();srv.server_close();thread.join()


def test_dashboard_switch_proxy_and_assets(tmp_path,monkeypatch):
    calls=[]
    def fake(route,body=None):
        calls.append((route,body));return dict(active='ollama',running=False),200
    monkeypatch.setattr(dashboard,'agent_request',fake)
    srv=dashboard.make_server(0,tmp_path/'archive');thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    url='http://127.0.0.1:'+str(srv.server_port)
    try:
        with pytest.raises(HTTPError) as exc:post(url+'/api/agent/switch',dict(id='ollama'))
        assert exc.value.code==403 and not calls
        data=json.load(post(url+'/api/agent/switch',dict(id='ollama'),{'X-Autoslay-ReadOnly':'1'}))
        assert data['active']=='ollama' and calls==[('/switch',dict(id='ollama'))]
        for path in ['/','/sidebar.html']:
            html=urlopen(url+path).read().decode()
            assert 'agent-controls' in html and 'agent.js' in html
        assert b'api_key' in urlopen(url+'/agent.js').read()
        sidebar=urlopen(url+'/sidebar.html').read().decode()
        # Candidate details are collapsed; the always-visible decision flow is separate.
        assert 'id="decision-flow"' in sidebar
        assert 'id="decision-disclosure"><summary>' in sidebar
        assert 'id="enemy-intents"' in sidebar and 'id="decision-forecast"' in sidebar
        assert b'renderSidebarCombat' in urlopen(url+'/sidebar.js').read()
        with urlopen(url+'/sidebar-combat.js') as response:
            assert response.headers.get_content_type()=='text/javascript'
            assert b'candidateIntentFacts' in response.read()
    finally:srv.shutdown();srv.server_close();thread.join()


def test_dashboard_launch_routes_are_guarded_and_preserve_request_identity(tmp_path,monkeypatch):
    calls=[]
    def fake(route,body=None):
        calls.append((route,body));return dict(job=None,agent=dict(running=False)),200
    monkeypatch.setattr(dashboard,'agent_request',fake)
    srv=dashboard.make_server(0,tmp_path/'archive');thread=threading.Thread(target=srv.serve_forever,daemon=True);thread.start()
    url='http://127.0.0.1:'+str(srv.server_port)
    body=dict(request_id='play-123',mode='continue',profile_id='codex')
    try:
        with pytest.raises(HTTPError) as exc:post(url+'/api/play/start',body)
        assert exc.value.code==403 and not calls
        json.load(post(url+'/api/play/start',body,{'X-Autoslay-ReadOnly':'1'}))
        assert calls[-1]==('/play/start',body)
        json.load(urlopen(Request(url+'/api/play/state?request_id=play-123',headers={'X-Autoslay-ReadOnly':'1'})))
        assert calls[-1]==('/play/state',{'request_id':'play-123'})
        assert b'play-controls' in urlopen(url+'/').read()
        assert b'playView' in urlopen(url+'/play.js').read()
        assert b'launchPhases' in urlopen(url+'/play-state.js').read()
    finally:srv.shutdown();srv.server_close();thread.join()
