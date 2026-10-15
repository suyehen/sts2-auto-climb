"""Transport tests: no network, no game execution."""
import asyncio
import json
from copy import deepcopy

import httpx
import pytest

from autoslay.agent.config import Profile, defaults
from autoslay.agent.contracts import DecisionPlan, prompt, tool_schema_for
from autoslay.agent.providers import APIProvider, ProviderError
from autoslay.decision.deterministic import source
from test_agent import make_agent


def profile(**changes):
    return Profile(id='deepseek', label='DeepSeek', kind='api',
        base_url='https://api.deepseek.com/v1', model='deepseek-flash',
        output_mode='deepseek_strict', **changes)


def response(arguments=None, **changes):
    arguments = arguments if arguments is not None else json.dumps(dict(
        state_token='current', steps=[dict(action_id='end_turn', target_id='')], reason='结束回合'))
    message = dict(content=None, tool_calls=[dict(id='call_1', type='function',
        function=dict(name='submit_decision', arguments=arguments))])
    return dict(choices=[dict(finish_reason='tool_calls', message=message, **changes)],
        usage=dict(total_tokens=12, secret='not-for-logs'))


def test_strict_wire_schema_and_target_translation():
    async def run():
        def handler(request):
            assert str(request.url)=='https://api.deepseek.com/beta/chat/completions'
            body=json.loads(request.content)
            assert 'response_format' not in body
            assert body['tool_choice']=='auto'
            function=body['tools'][0]['function']
            assert function['strict'] is True
            assert function['parameters']==tool_schema_for(dict(state_token='current'))
            assert 'Do not use tools' not in body['messages'][0]['content']
            return httpx.Response(200,json=response())
        raw,usage=await APIProvider(profile(),'test',httpx.MockTransport(handler)).decide(dict(state_token='current'))
        plan=DecisionPlan.model_validate_json(raw)
        assert plan.state_token=='current' and plan.steps[0].target_id is None
        assert usage==dict(total_tokens=12)
    asyncio.run(run())


def test_non_thinking_mode_forces_named_tool():
    async def run():
        def handler(request):
            body=json.loads(request.content)
            assert body['reasoning_effort']=='none'
            assert body['tool_choice']==dict(type='function',function=dict(name='submit_decision'))
            return httpx.Response(200,json=response())
        await APIProvider(profile(reasoning_effort='none'),'test',httpx.MockTransport(handler)).decide({})
    asyncio.run(run())


def test_strict_schema_only_uses_supported_subset():
    schema=tool_schema_for(dict(state_token='current'))
    def visit(value):
        if isinstance(value, dict):
            assert not {'$defs','$ref','minLength','maxLength','minItems','maxItems'} & value.keys()
            assert value.get('type') != 'null'
            if value.get('type')=='object':
                assert set(value['required'])==set(value['properties'])
                assert value['additionalProperties'] is False
            for child in value.values(): visit(child)
        elif isinstance(value,list):
            for child in value: visit(child)
    visit(schema)
    assert schema['properties']['state_token']['enum']==['current']
    assert 'exactly ONE step' in prompt(dict(visible_state=dict(state=dict(phase='map'))),tool_mode=True)
    assert next(p for p in defaults() if p.id=='deepseek').output_mode=='deepseek_strict'


@pytest.mark.parametrize('arguments',[
    '{broken',
    json.dumps(dict(state_token='current', steps=[], reason='empty')),
    json.dumps(dict(state_token='current', steps=[dict(action_id='end_turn',target_id='',extra=1)],reason='extra')),
    json.dumps(dict(state_token='current', steps=[dict(action_id='end_turn',target_id='')]*17,reason='too many')),
])
def test_strict_output_does_not_repair_invalid_plans(arguments):
    async def run():
        raw,_=await APIProvider(profile(),'test',httpx.MockTransport(
            lambda r:httpx.Response(200,json=response(arguments)))).decide(dict(state_token='current'))
        with pytest.raises(ValueError): DecisionPlan.model_validate_json(raw)
    asyncio.run(run())


def test_target_ids_are_preserved():
    async def run():
        args=json.dumps(dict(state_token='current',steps=[dict(action_id='attack',target_id='enemy_1')],reason='攻击'))
        raw,_=await APIProvider(profile(),'test',httpx.MockTransport(
            lambda r:httpx.Response(200,json=response(args)))).decide({})
        assert DecisionPlan.model_validate_json(raw).steps[0].target_id=='enemy_1'
    asyncio.run(run())


@pytest.mark.parametrize('change', ['name','multiple','missing','refusal','argument_type','type','length'])
def test_rejects_invalid_tool_envelopes(change):
    async def run():
        data=response(); choice=data['choices'][0]; message=choice['message']
        if change=='name': message['tool_calls'][0]['function']['name']='execute_shell'
        if change=='multiple': message['tool_calls']*=2
        if change=='missing': message.pop('tool_calls')
        if change=='refusal': message['refusal']='private text'
        if change=='argument_type': message['tool_calls'][0]['function']['arguments']={}
        if change=='type': message['tool_calls'][0]['type']='other'
        if change=='length': choice['finish_reason']='length'
        calls=[]
        def handler(r): calls.append(r); return httpx.Response(200,json=data)
        with pytest.raises(ProviderError) as err:
            await APIProvider(profile(max_tokens=131072),'test',httpx.MockTransport(handler)).decide({})
        assert len(calls)==1 and 'private text' not in str(err.value.details)
    asyncio.run(run())


def test_explicit_unsupported_falls_back_once_and_reports_retry():
    async def run():
        calls=[]; progress=[]
        def handler(r):
            body=json.loads(r.content); calls.append(body)
            if len(calls)==1:
                return httpx.Response(400,json=dict(error=dict(code='unsupported_parameter',param='strict')))
            assert str(r.url)=='https://api.deepseek.com/v1/chat/completions'
            assert 'tools' not in body and body['response_format']==dict(type='json_object')
            return httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(content='{}'))]))
        provider=APIProvider(profile(),'test',httpx.MockTransport(handler)); provider.on_retry=progress.append
        raw,usage=await provider.decide({})
        assert raw=='{}' and usage['request_attempts']==2
        assert len(calls)==2 and progress[0]['retry_reason']=='MODEL_STRICT_UNSUPPORTED'
    asyncio.run(run())


@pytest.mark.parametrize('status,error',[
    (400,dict(code='invalid_json_schema',param='tools',message='private schema')),
    (400,dict(code='invalid_request_error',message='unknown failure')),
    (400,dict(code='unsupported_parameter',param='reasoning_effort')),
    (401,dict(code='unsupported_parameter',param='tools')),
    (404,dict(code='model_not_found',param='model')),
])
def test_no_silent_downgrade_on_schema_auth_or_unrelated_errors(status,error):
    async def run():
        calls=[]
        def handler(r): calls.append(r); return httpx.Response(status,json=dict(error=error))
        with pytest.raises(ProviderError,match='MODEL_HTTP_'+str(status)) as err:
            await APIProvider(profile(),'test',httpx.MockTransport(handler)).decide({})
        assert len(calls)==1 and 'private schema' not in str(err.value.details)
    asyncio.run(run())


@pytest.mark.parametrize('changes',[
    dict(base_url='https://example.com'),dict(base_url='https://api.deepseek.com/custom'),
    dict(kind='codex'), dict(kind='local',base_url='http://127.0.0.1:1234'),
])
def test_strict_mode_is_limited_to_official_deepseek(changes):
    values=profile().model_dump() | changes
    with pytest.raises(ValueError): Profile.model_validate(values)


@pytest.mark.parametrize('outcome',['corrected','invalid_again','state_changed','paused','malformed_json'])
def test_tool_response_correction_is_state_checked_and_never_dispatches(tmp_path,outcome):
    async def run():
        agent,s,_,submit=make_agent(tmp_path)
        origin=deepcopy(s); agent.running=True; agent.generation=1
        calls=[]
        def handler(request):
            body=json.loads(request.content); calls.append(body)
            if len(calls)==1:
                if outcome=='state_changed': s['state']['legal_actions'].pop()
                if outcome=='paused': agent.running=False
                if outcome=='malformed_json': return httpx.Response(200,json=response('{broken'))
            if len(calls)==1 or outcome=='invalid_again':
                return httpx.Response(200,json=dict(choices=[dict(finish_reason='stop',message=dict(content='private prose'))],usage=dict(total_tokens=7)))
            args=json.dumps(dict(state_token=source(origin)['state_sha256'],
                candidate_id='option_3',reason='结束回合'))
            assert 'submit_decision tool call' in body['messages'][0]['content']
            assert 'private prose' not in body['messages'][0]['content']
            return httpx.Response(200,json=response(args))
        p=profile(); provider=APIProvider(p,'test',httpx.MockTransport(handler))
        packet=dict(state_token=source(origin)['state_sha256'],visible_state=origin)
        if outcome=='invalid_again':
            with pytest.raises(ProviderError,match='MODEL_TOOL_CALL_REQUIRED') as err:
                await agent.decide_plan(provider,packet,origin,1,'test',{},p)
            assert err.value.details['validation_attempts']==2
            assert err.value.details['usage']['total_tokens']==14
        else:
            plan,usage=await agent.decide_plan(provider,packet,origin,1,'test',{},p)
            if outcome in {'corrected','malformed_json'}:
                assert plan.steps[0].target_id is None and usage['validation_attempts']==2
                assert usage['total_tokens']==(24 if outcome=='malformed_json' else 19)
            else: assert plan is None
        assert len(calls)==(1 if outcome in {'paused','state_changed'} else 2)
        assert submit.await_count==0
    asyncio.run(run())
