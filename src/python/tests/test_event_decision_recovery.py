"""Event loading must not be mistaken for a decision about potion disposal."""
import asyncio
import json
import time
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from autoslay.agent.contracts import tool_schema_for
from autoslay.agent.providers import ProviderError
from autoslay.decision.deterministic import assess,source,event_options_settling
from test_agent import make_agent


def event_state(snapshot,ready=False):
    state=snapshot['state'];state['phase']='event'
    state['event_screen']=dict(supported=True,screen_id='flower',decision_ready=ready,
        options=[dict(available=ready,action_id='collect' if ready else None)])
    state['legal_actions']=[dict(action_id='discard',type='discard_potion',allows_no_target=True,allowed_target_ids=[]),
        dict(action_id='return_to_main_menu',type='return_to_main_menu',allows_no_target=True,allowed_target_ids=[])]
    if ready:state['legal_actions'].append(dict(action_id='collect',type='choose_event_option',allows_no_target=True,allowed_target_ids=[]))


def test_event_loading_blocks_local_and_model_paths(tmp_path,monkeypatch):
    async def run():
        agent,s,_,submit=make_agent(tmp_path)
        event_state(s)
        assert event_options_settling(s)
        assert assess({},s)['mode']=='wait'
        assert 'event_options_not_ready' in assess({},s)['reasons']
        first=deepcopy(s); event_state(s,True)
        assert not event_options_settling(s)
        initial_fetch=agent.fetch
        reads=0
        async def fetch():
            nonlocal reads
            reads+=1
            return deepcopy(first) if reads==1 else await initial_fetch()
        agent.fetch=fetch
        entered=asyncio.Event();packets=[]
        class Provider:
            async def decide(self,packet):
                packets.append(packet);entered.set();await asyncio.Event().wait()
        agent.factory=lambda *_:Provider()
        async def no_delay(_):return None
        monkeypatch.setattr('autoslay.agent.runtime.asyncio.sleep',no_delay)
        await agent.command('/start',{})
        try:
            await asyncio.wait_for(entered.wait(),3)
            assert reads>=2 and len(packets)==1
            assert packets[0]['visible_state']['state']['event_screen']['decision_ready'] is True
            assert submit.await_count==0
        finally:await agent.pause()
    asyncio.run(run())


def test_event_loading_timeout_is_specific_and_never_calls_model(tmp_path,monkeypatch):
    async def run():
        agent,s,control,submit=make_agent(tmp_path)
        event_state(s)
        model=AsyncMock();agent.factory=model
        # Use an independent clock object so asyncio's own monotonic clock is unaffected.
        class Clock:
            ticks=0
            time=staticmethod(time.time)
            @classmethod
            def monotonic(cls):cls.ticks+=16;return cls.ticks
        monkeypatch.setattr('autoslay.agent.runtime.time',Clock)
        async def no_delay(_):return None
        monkeypatch.setattr('autoslay.agent.runtime.asyncio.sleep',no_delay)
        await agent.command('/start',{})
        await asyncio.wait_for(agent.task,3)
        assert agent.error=='EVENT_OPTIONS_NOT_READY'
        model.assert_not_called();assert submit.await_count==0
    asyncio.run(run())


@pytest.mark.parametrize('fault',['UNKNOWN_DECISION_CANDIDATE','STALE_MODEL_RESULT','EXTRA_ACTION','RAW_STEPS'])
@pytest.mark.parametrize('outcome',['corrected','invalid_again','state_changed','paused'])
def test_candidate_reply_errors_get_one_state_checked_correction(tmp_path,fault,outcome):
    code=fault if fault in {'UNKNOWN_DECISION_CANDIDATE','STALE_MODEL_RESULT'} else 'MODEL_INVALID_DECISION'
    async def run():
        agent,s,_,submit=make_agent(tmp_path);origin=deepcopy(s)
        agent.running=True;agent.generation=1
        packets=[]
        class Provider:
            async def decide(self,packet):
                packets.append(packet)
                value=dict(state_token=source(origin)['state_sha256'],reason='测试',candidate_id='option_1')
                if len(packets)==1 or outcome=='invalid_again':
                    if fault=='UNKNOWN_DECISION_CANDIDATE':value['candidate_id']='old-screen-option'
                    if fault=='STALE_MODEL_RESULT':value['state_token']='old-state'
                    if fault=='EXTRA_ACTION':value.update(action_id='quit_game',target_id='old-enemy')
                    if fault=='RAW_STEPS':
                        value.pop('candidate_id')
                        value['steps']=[dict(action_id='play_a',target_id=None)]
                if len(packets)==1:
                    if outcome=='state_changed':s['state']['legal_actions'].pop()
                    if outcome=='paused':agent.running=False
                return json.dumps(value),dict(total_tokens=9)
        packet=dict(state_token=source(origin)['state_sha256'],visible_state=origin)
        if outcome=='invalid_again':
            with pytest.raises(ProviderError,match=code) as err:
                await agent.decide_plan(Provider(),packet,origin,1,'test',{},agent.store.profiles['codex'])
            assert err.value.details['validation_attempts']==2
            assert err.value.details['usage']['total_tokens']==18
        else:
            plan,usage=await agent.decide_plan(Provider(),packet,origin,1,'test',{},agent.store.profiles['codex'])
            if outcome=='corrected':
                assert plan.steps[0].action_id=='play_a' and usage['total_tokens']==18
                assert packets[1]['validation_feedback']['code']==code
            else:assert plan is None
        assert len(packets)==(1 if outcome in {'state_changed','paused'} else 2)
        assert submit.await_count==0
    asyncio.run(run())


def test_noncombat_native_schema_keeps_action_and_target_pairs_together():
    from jsonschema import validate,ValidationError
    packet=dict(visible_state=dict(state=dict(phase='event',legal_actions=[
        dict(action_id='collect',type='choose_event_option',allows_no_target=True,allowed_target_ids=[]),
        dict(action_id='choose',type='choose_event_option',allows_no_target=False,allowed_target_ids=['card-a','card-b']),
        dict(action_id='return_to_main_menu',type='return_to_main_menu',allows_no_target=True,allowed_target_ids=[])])))
    schema=tool_schema_for(packet)
    def value(action,target):return dict(state_token='test',reason='测试',steps=[dict(action_id=action,target_id=target)])
    validate(value('collect',''),schema);validate(value('choose','card-b'),schema)
    for action,target in [('collect','card-a'),('choose',''),('invented',''),('return_to_main_menu','')]:
        with pytest.raises(ValidationError):validate(value(action,target),schema)
    packet['visible_state']['state']['phase']='combat'
    # A later combat step can become legal after a preceding energy gain.
    assert 'anyOf' not in tool_schema_for(packet)['properties']['steps']['items']
