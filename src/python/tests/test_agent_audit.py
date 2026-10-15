import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock
import httpx
import pytest
from autoslay.telemetry.audit import Audit
from autoslay.telemetry.runs import location
from autoslay.archive.repository import Repository
from autoslay.archive import collector
from autoslay.agent.config import Profile
from autoslay.agent.providers import APIProvider
from test_agent import make_agent, payload, choice_payload
from test_execution import snapshot


def events(agent):
    return sorted((json.loads(p.read_text(encoding='utf-8')) for p in
                   (agent.journal/'audit/events').glob('*.json')), key=lambda e: e['recorded_at'])


def test_historical_action_names_survive_changed_state(tmp_path):
    s=snapshot();s['state']['player']['hand'][0]['name']='打击'
    s['state']['enemies'][0]['name']='史莱姆'
    audit=Audit(tmp_path,archive=False)
    identity=audit.emit('action_submitted',s,'trace',action_id='play_a',target_id='e',
                        api_key='never-store',usage={'total_tokens':8,'secret':'never-store'})
    s['state']['player']['hand'][0]['name']='新名称'
    saved=json.loads((tmp_path/'events'/(identity+'.json')).read_text(encoding='utf-8'))
    d=saved['details']
    assert d['action_label']=='打出「打击」' and d['target_label']=='史莱姆'
    assert d['event_label']=='已提交游戏动作' and d['action_id']=='play_a'
    assert d['usage']=={'total_tokens':8} and 'never-store' not in json.dumps(saved)


@pytest.mark.parametrize('phase', ['event', 'rewards', 'map', 'shop', 'rest_site'])
def test_noncombat_plan_audit_accepts_null_combat_fields(tmp_path, phase):
    async def run():
        agent, s, _, submit = make_agent(tmp_path)
        s['state'].update(phase=phase, enemies=None)
        s['state']['player'].update(hand=None, potions=None)
        s['state']['legal_actions'] = [dict(action_id='choose_option',
            type='choose_event_option', allows_no_target=True, allowed_target_ids=[])]
        s['state']['event_screen'] = dict(options=[dict(action_id='choose_option', title='离开')])
        agent.running = True
        agent.generation = 1
        agent.trace_id = 'trace'
        class Provider:
            async def decide(self, packet):
                from autoslay.decision.deterministic import source
                return json.dumps(dict(state_token=source(s)['state_sha256'],
                    candidate_id='option_1', reason='离开事件')), {}
        plan, _ = await agent.decide_plan(Provider(), {}, s, 1, 'plan', {}, agent.store.profiles['codex'])
        assert plan.steps[0].action_id == 'choose_option'
        validated = next(e['details'] for e in events(agent) if e['details']['event'] == 'plan_validated')
        assert validated['step_labels'][0]['action_label'] == '选择「离开」'
        assert validated['step_labels'][0]['target_label'] == '无目标'
        assert not agent.audit.status()['degraded']
        assert submit.await_count == 0
    asyncio.run(run())


def test_archive_failure_is_durable_visible_and_recoverable(tmp_path,monkeypatch):
    repo=Repository(tmp_path/'runs')
    monkeypatch.setattr(collector,'for_legacy',lambda directory=None:repo)
    real_store=repo.store
    monkeypatch.setattr(repo,'store',lambda _:(_ for _ in ()).throw(OSError('disk')))
    audit=Audit(tmp_path/'audit',pending=repo.root/'.pending')
    identity=audit.emit('trace_started',snapshot(),'trace')
    assert audit.status()['degraded'] and audit.status()['pending_count']==1
    assert (tmp_path/'audit/events'/(identity+'.json')).exists()
    restarted=Audit(tmp_path/'audit',pending=repo.root/'.pending')
    assert restarted.status()['write_failures']==1
    monkeypatch.setattr(repo,'store',real_store)
    repo.recover_pending()
    restarted.emit('trace_finished',snapshot(),'trace',status='completed')
    assert restarted.status()['pending_count']==0 and not restarted.status()['degraded']
    assert repo.load_event(identity)['details']['trace_id']=='trace'


def test_local_write_failure_cannot_interrupt_execution(tmp_path,monkeypatch):
    audit=Audit(tmp_path,archive=False)
    monkeypatch.setattr('autoslay.telemetry.audit.atomic',lambda *args:(_ for _ in ()).throw(OSError('secret-error')))
    audit.emit('trace_started',snapshot(),'trace')
    assert audit.status()['degraded'] and audit.status()['write_failures']==1
    assert 'secret-error' not in json.dumps(audit.status())


def test_transport_retry_and_validation_retry_have_distinct_attempts(tmp_path,monkeypatch):
    async def run():
        agent,s,_,_=make_agent(tmp_path)
        agent.running=True;agent.generation=1;agent.trace_id='trace'
        profile=Profile(id='test',label='测试后端',kind='api',base_url='https://api.deepseek.com',model='test')
        calls=[]
        def handler(request):
            calls.append(request)
            if len(calls)==1:return httpx.Response(503)
            raw='invalid-private-response' if len(calls)==2 else choice_payload(s)
            return httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(content=raw))],
                                  usage={'total_tokens':10,'secret':'never-store'}))
        monkeypatch.setattr('autoslay.agent.providers.asyncio.sleep',AsyncMock())
        provider=APIProvider(profile,'never-store',httpx.MockTransport(handler))
        plan,usage=await agent.decide_plan(provider,{},deepcopy(s),1,'plan',{},profile)
        assert plan.steps and usage['total_tokens']==20
        rows=events(agent);details=[r['details'] for r in rows]
        attempts=[d for d in details if d['event']=='model_attempt' and d['status']=='started']
        assert [(d['validation_attempt'],d['attempt']) for d in attempts]==[(1,1),(1,2),(2,1)]
        assert [d['event'] for d in details].count('plan_rejected')==1
        assert all(d['trace_id']=='trace' for d in details)
        assert 'invalid-private-response' not in json.dumps(rows) and 'never-store' not in json.dumps(rows)
    asyncio.run(run())


def test_pause_finishes_same_trace_without_dispatch(tmp_path):
    async def run():
        entered=asyncio.Event()
        class Provider:
            async def decide(self,packet):
                entered.set();await asyncio.Event().wait()
        agent,_,_,submit=make_agent(tmp_path,factory=lambda *_:Provider())
        await agent.command('/start',{})
        await asyncio.wait_for(entered.wait(),3)
        await agent.command('/pause',{})
        rows=[r['details'] for r in events(agent)]
        started=next(d for d in rows if d['event']=='trace_started')
        terminals=[d for d in rows if d['event']=='trace_finished']
        assert len(terminals)==1 and terminals[0]['status']=='cancelled'
        assert terminals[0]['trace_id']==started['trace_id']
        assert submit.await_count==0
    asyncio.run(run())


def test_observer_callback_failure_does_not_retry_completed_request():
    async def run():
        profile=Profile(id='test',label='test',kind='api',base_url='https://api.deepseek.com',model='test')
        calls=[]
        def handler(request):
            calls.append(request)
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}]})
        provider=APIProvider(profile,'test',httpx.MockTransport(handler))
        provider.on_attempt=lambda _:(_ for _ in ()).throw(OSError('audit failure'))
        assert (await provider.decide({}))[0]=='{}' and len(calls)==1
    asyncio.run(run())


def test_recovery_keeps_original_saved_run_location_and_trace(tmp_path):
    async def run():
        agent,s,_,submit=make_agent(tmp_path)
        s['state']['run_history_id']='saved-run'
        original_location=location(s)
        record=dict(status='dispatch_pending',trace_id='original-trace',audit_location=original_location,
                    request=dict(request_id='agent-interrupted',run_id=s['state']['run_id'],session_epoch=s['session_epoch']))
        agent.journal.mkdir(parents=True)
        (agent.journal/'agent-interrupted.json').write_text(json.dumps(record),encoding='utf-8')
        agent.action_status=AsyncMock(return_value=dict(status='completed'))
        await agent.unresolved()
        rows=events(agent)
        assert len(rows)==2 and all(e['location']==original_location for e in rows)
        assert all(e['details']['trace_id']=='original-trace' for e in rows)
        assert submit.await_count==0
    asyncio.run(run())
