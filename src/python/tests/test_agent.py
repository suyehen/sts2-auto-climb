import asyncio
import json
import os
from copy import deepcopy
from unittest.mock import AsyncMock
import httpx
import pytest
from autoslay.agent.config import Store, Profile, protect
from autoslay.agent.contracts import validate, Decision
from autoslay.agent.context import build
from autoslay.agent.providers import APIProvider, CodexProvider, ProviderError
from autoslay.agent.runtime import Agent
from autoslay.decision.deterministic import source
from test_deterministic import live


def cipher(value, decrypt=False):
    return value[7:] if decrypt else b'sealed:'+value


def payload(s, **changes):
    return json.dumps(dict(state_token=source(s)['state_sha256'],
        action_id=s['state']['legal_actions'][0]['action_id'],
        target_id=s['state']['enemies'][0]['instance_id'], reason='测试决策', **changes))


def store(tmp_path):
    return Store(tmp_path/'config', cipher)


def test_profiles_and_no_secret_roundtrip(tmp_path, monkeypatch):
    st=store(tmp_path);st.set_key('deepseek','example-secret-123');st.active='deepseek';st.save()
    assert 'example-secret-123' not in json.dumps(st.public())
    assert 'example-secret-123' not in st.path.read_text()
    assert Store(st.directory,cipher).key(st.profiles['deepseek'])=='example-secret-123'
    monkeypatch.setenv('DEEPSEEK_API_KEY','env-secret')
    assert st.key(st.profiles['deepseek'])=='env-secret'
    monkeypatch.delenv('DEEPSEEK_API_KEY')
    st.set_key('deepseek','');assert not st.key(st.profiles['deepseek'])


@pytest.mark.skipif(os.name!='nt',reason='Windows credentials')
def test_windows_dpapi_roundtrip():
    encrypted=protect(b'test-local-key')
    assert b'test-local-key' not in encrypted
    assert protect(encrypted,True)==b'test-local-key'


@pytest.mark.parametrize('url,kind',[
    ('http://api.example.com/v1','api'),('https://key:pass@example.com/v1','api'),
    ('https://example.com/v1?key=x','api'),('http://192.168.0.10:11434/v1','local'),
    ('file:///secret','api'),('https://remote.example/v1','local')])
def test_bad_endpoint(url,kind):
    with pytest.raises(ValueError): Profile(id='bad',label='bad',kind=kind,base_url=url)


def test_strict_legal_target_source_validation():
    s=live();assert validate(payload(s),s).reason=='测试决策'
    d=json.loads(payload(s))
    for changes in [dict(target_id='unknown'),dict(action_id='quit_game'),dict(state_token='stale'),
                    dict(target_id=None),dict(extra_field='shell command')]:
        with pytest.raises(ValueError):validate(json.dumps(d|changes),s)


def test_no_target_and_admin_type():
    s=live();d=json.loads(payload(s));d.update(action_id='end_turn',target_id=None)
    assert validate(json.dumps(d),s).action_id=='end_turn'
    s['state']['legal_actions'].append(dict(action_id='admin',type='quit_game',allows_no_target=True))
    d.update(action_id='admin',state_token=source(s)['state_sha256'])
    with pytest.raises(ValueError):validate(json.dumps(d),s)


def test_context_relevant_catalog_not_full_and_byte_budget():
    s=live();packet=build(s,{})
    assert packet['state_token']==source(s)['state_sha256']
    assert 0<len(packet['reference_entries'])<961
    assert packet['visible_state']['state']['legal_actions']==s['state']['legal_actions']
    with pytest.raises(ValueError,match='CONTEXT_BUDGET'):build(s,{},max_bytes=10)


def test_generated_monster_cards_are_referenced_in_agent_packet():
    s=live()
    forecast=dict(enemy_model={},candidates=[dict(scenarios=[dict(enemy_turns=[dict(
        generated_cards=[dict(entity_id='CARD.SLIMED',amount=2,pile='discard')])])])])
    result=build(s,dict(source={},phase='combat',combat={'lookahead':forecast}))
    assert any(e['entity_id']=='CARD.SLIMED' for e in result['reference_entries'])
    assert result['calculator']['combat_forecast']


@pytest.mark.parametrize('kind,mode', [('api','json'),('api','schema'),('local','text')])
def test_compatible_api_wire_format(kind,mode):
    async def run():
        calls=[]
        def handler(r):
            body=json.loads(r.content);calls.append(body)
            assert r.url.path=='/v1/chat/completions'
            assert r.headers.get('authorization')==('Bearer test-key' if kind=='api' else None)
            if mode=='text':assert 'response_format' not in body
            else:assert body['response_format']['type']==('json_schema' if mode=='schema' else 'json_object')
            return httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(content='{}'))],
                usage=dict(total_tokens=9,secret='should-not-return')))
        p=Profile(id='test',label='test',kind=kind,base_url='http://127.0.0.1:1234/v1',model='test',output_mode=mode)
        raw,usage=await APIProvider(p,'test-key' if kind=='api' else '',httpx.MockTransport(handler)).decide({})
        assert raw=='{}' and usage==dict(total_tokens=9) and len(calls)==1
    asyncio.run(run())


@pytest.mark.parametrize('response,code',[
    (dict(choices=[dict(finish_reason='length',message=dict(content='{}'))]),'INCOMPLETE'),
    (dict(choices=[dict(finish_reason='stop',message=dict(content=''))]),'EMPTY'),
    (dict(choices=[dict(finish_reason='stop',message=dict(content='{}',tool_calls=[{}]))]),'TOOL_CALL'),
    ({},'INVALID_RESPONSE')])
def test_api_invalid_output(response,code):
    async def run():
        p=Profile(id='local',label='local',kind='local',base_url='http://127.0.0.1:1234/v1',model='test')
        with pytest.raises(ProviderError,match=code):
            await APIProvider(p,transport=httpx.MockTransport(lambda r:httpx.Response(200,json=response))).decide({})
    asyncio.run(run())


def test_api_error_is_sanitized_and_not_retried():
    async def run():
        calls=[]
        def handler(r):calls.append(r);return httpx.Response(401,text='secret-key-and-provider-message')
        p=Profile(id='a',label='a',kind='api',base_url='https://example.test/v1',model='test')
        with pytest.raises(ProviderError,match='^MODEL_HTTP_401$'):
            await APIProvider(p,'test',httpx.MockTransport(handler)).decide({})
        assert len(calls)==1
    asyncio.run(run())


def make_agent(tmp_path, **options):
    s=live();s['state']['enemies'][0]['hp']=100 # no deterministic finish
    fetch=AsyncMock(side_effect=lambda:deepcopy(s))
    control=AsyncMock(return_value={})
    submit=AsyncMock(return_value=dict(status='completed'))
    st=store(tmp_path);st.active='codex'
    agent=Agent(st,fetch=fetch,control=control,submit=submit,record=False,
        journal=tmp_path/'journal',analyze=lambda snap:dict(source=source(snap),phase='combat',analysis_id='test'),**options)
    return agent,s,control,submit


def test_switch_cancels_pending_model_without_dispatch(tmp_path):
    async def run():
        entered=asyncio.Event();cancelled=asyncio.Event()
        class Provider:
            async def decide(self,packet):
                entered.set()
                try:await asyncio.Event().wait()
                finally:cancelled.set()
        agent,s,control,submit=make_agent(tmp_path,factory=lambda p,k:Provider())
        await agent.command('/start',{})
        await asyncio.wait_for(entered.wait(),3)
        result=await agent.command('/switch',dict(id='deepseek'))
        assert cancelled.is_set() and result['active']=='deepseek' and not result['running']
        assert submit.await_count==0 and control.call_args.args==('paused',)
        assert Store(agent.store.directory,cipher).active=='deepseek'
    asyncio.run(run())


def test_stale_result_never_dispatches(tmp_path):
    async def run():
        class Provider:
            async def decide(self,packet):
                before=deepcopy(s);s['state_version']+=1
                raw=payload(before)
                # Stop after verifying one dispatch attempt, avoids infinite fixture loop.
                agent.running=False
                return raw,{}
        agent,s,control,submit=make_agent(tmp_path,factory=lambda p,k:Provider())
        agent.running=True;agent.generation=1
        before=deepcopy(s);d=validate(payload(before),before);s['state_version']+=1
        assert await agent.dispatch(d,before,1) is None
        assert submit.await_count==0
    asyncio.run(run())


def test_dispatch_persists_before_submit_and_recovers_without_retry(tmp_path):
    async def run():
        agent,s,control,submit=make_agent(tmp_path)
        agent.running=True;agent.generation=1
        async def uncertain(request):
            data=json.loads(next(agent.journal.glob('*.json')).read_text())
            assert data['status']=='dispatch_pending' and data['request']==request.model_dump()
            return dict(status='unknown')
        agent.submit=AsyncMock(side_effect=uncertain)
        result=await agent.dispatch(validate(payload(s),s),s,1)
        assert result['status']=='unknown'
        agent.action_status=AsyncMock(return_value=dict(status='unknown'))
        with pytest.raises(ValueError,match='UNRESOLVED'):await agent.unresolved()
        agent.action_status=AsyncMock(return_value=dict(status='completed'))
        await agent.unresolved()
        assert agent.submit.await_count==1
    asyncio.run(run())


def test_ai_loop_one_decision_and_no_repeat_on_unchanged_state(tmp_path):
    async def run():
        calls=[];submitted=asyncio.Event()
        class Provider:
            async def decide(self,packet):calls.append(packet);return payload(s),dict(total_tokens=10)
        agent,s,control,submit=make_agent(tmp_path,factory=lambda p,k:Provider())
        async def dispatch(request):submitted.set();return dict(status='completed')
        agent.submit=AsyncMock(side_effect=dispatch)
        await agent.command('/start',{})
        await asyncio.wait_for(submitted.wait(),3)
        await asyncio.sleep(.1)
        await agent.command('/pause',{})
        assert len(calls)==1 and agent.submit.await_count==1
        assert agent.last['source']=='codex' and not agent.running
    asyncio.run(run())


def test_pause_resume_reanalyzes_current_state_and_archives_new_report(tmp_path,monkeypatch):
    async def run():
        calls=[];archived=[];entered=asyncio.Queue();cancelled=[]
        class Provider:
            async def decide(self,packet):
                calls.append(packet)
                entered.put_nowait(len(calls))
                try: await asyncio.Event().wait()
                finally: cancelled.append(len(calls))
        agent,s,control,submit=make_agent(tmp_path,factory=lambda p,k:Provider())
        agent.record=True
        def analyze(snap):
            return dict(source=source(snap),phase='combat',analysis_id='resume-'+str(snap['state_version']))
        agent.analyze=analyze
        monkeypatch.setattr('autoslay.agent.runtime.decisions.archive',lambda report,snap:archived.append((deepcopy(report),deepcopy(snap))))
        monkeypatch.setattr('autoslay.agent.runtime.runs.stage',lambda *args:None)
        await agent.command('/start',{})
        assert await asyncio.wait_for(entered.get(),3)==1
        await agent.command('/pause',{})
        assert cancelled==[1] and agent.task is None
        s['state_version']+=1
        await agent.command('/start',{})
        assert await asyncio.wait_for(entered.get(),3)==2
        await agent.command('/pause',{})
        assert len(archived)==2 and submit.await_count==0
        assert archived[1][0]['source']==source(s)
        assert calls[1]['source']['state_version']==s['state_version']
        assert calls[0]['source']['state_version']!=calls[1]['source']['state_version']
    asyncio.run(run())


def test_invalid_output_pauses_agent(tmp_path):
    async def run():
        class Provider:
            async def decide(self,packet):return '{"shell":"bad"}',{}
        agent,s,control,submit=make_agent(tmp_path,factory=lambda p,k:Provider())
        await agent.command('/start',{})
        await asyncio.wait_for(agent.task,3)
        assert agent.phase=='error' and not agent.running and submit.await_count==0
        assert control.call_args.args==('paused',)
    asyncio.run(run())


def test_local_rule_skips_model(tmp_path):
    async def run():
        agent,s,control,submit=make_agent(tmp_path,factory=lambda *a:pytest.fail('model should not be called'))
        s['state']['enemies'][0]['hp']=2
        class Deterministic:
            async def execute(self,request_id):
                s['state']['phase']='game_over'
                return dict(status='completed')
        agent.deterministic=Deterministic()
        await agent.command('/start',{})
        await asyncio.wait_for(agent.task,3)
        assert agent.last['source']=='local_deterministic' and agent.phase=='finished'
    asyncio.run(run())


def test_codex_command_isolated_and_cancel_cleanup(tmp_path,monkeypatch):
    async def run():
        captured={}
        class Process:
            returncode=None
            async def communicate(self,data):
                captured['prompt']=data
                await asyncio.Event().wait()
            def kill(self):captured['killed']=True;self.returncode=-1
            async def wait(self):return self.returncode
        async def spawn(*args,**kw):captured.update(args=args,kw=kw);return Process()
        monkeypatch.setattr('autoslay.agent.providers.shutil.which',lambda x:'codex.exe')
        monkeypatch.setattr('autoslay.agent.providers.ROOT',tmp_path)
        monkeypatch.setattr('autoslay.agent.providers.asyncio.create_subprocess_exec',spawn)
        monkeypatch.setenv('OPENAI_API_KEY','must-not-pass')
        task=asyncio.create_task(CodexProvider(Profile(id='c',label='c',kind='codex')).decide({}))
        await asyncio.sleep(.02);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert captured['killed'] and '--ignore-user-config' in captured['args']
        assert '--ephemeral' in captured['args'] and 'read-only' in captured['args']
        assert 'OPENAI_API_KEY' not in captured['kw']['env']
    asyncio.run(run())
