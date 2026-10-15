import asyncio
import json
from copy import deepcopy
import pytest
from autoslay.agent.contracts import validate_choice, schema_for, tool_schema_for
from autoslay.agent.context import build
from autoslay.agent.providers import ProviderError
from autoslay.decision.options import decision_options
from autoslay.decision.deterministic import source
from autoslay.archive.repository import Repository
from autoslay.archive.views import live
from test_agent import make_agent
from test_deterministic import live as combat_snapshot
from test_run_observatory import snapshot


def test_model_can_only_select_a_registered_whole_plan():
    s=combat_snapshot()
    steps=[dict(action_id='play_a',target_id='e'),dict(action_id='end_turn',target_id=None)]
    report=dict(source=source(s),phase='combat',combat=dict(candidates=[dict(steps=steps,metrics={'heuristic_score':5})]))
    options=decision_options(report,s)
    raw=dict(state_token=source(s)['state_sha256'],candidate_id=options[0]['candidate_id'],reason='选择已有方案')
    assert [p.model_dump() for p in validate_choice(json.dumps(raw),s,options).steps]==steps
    for changes in [dict(candidate_id='invented'),dict(steps=list(reversed(steps))),dict(action_id='play_b'),dict(state_token='old')]:
        with pytest.raises(ValueError):validate_choice(json.dumps(raw|changes),s,options)
    packet=build(s,report)
    for schema in (schema_for(packet),tool_schema_for(packet)):
        assert set(schema['properties'])=={'state_token','candidate_id','reason'}
        assert schema['properties']['candidate_id']['enum']==[options[0]['candidate_id']]


def test_unscored_screens_offer_fixed_single_operations_before_model_call():
    s=combat_snapshot();s['state']['phase']='rewards'
    s['state']['legal_actions']=[dict(action_id='claim',allows_no_target=True),dict(action_id='leave',allows_no_target=True)]
    options=decision_options({},s)
    assert len(options)==2 and all(len(o['steps'])==1 for o in options)
    assert [o['steps'][0]['action_id'] for o in options]==['claim','leave']


def test_arbitrary_model_steps_are_rejected_before_dispatch(tmp_path):
    async def run():
        agent,s,_,submit=make_agent(tmp_path);agent.running=True;agent.generation=1
        packet=build(s,{})
        class Provider:
            async def decide(self,packet):
                return json.dumps(dict(state_token=packet['state_token'],steps=[dict(action_id='end_turn',target_id=None)],reason='自行组合')),{}
        with pytest.raises(ProviderError,match='MODEL_INVALID_DECISION'):
            await agent.decide_plan(Provider(),packet,s,1,'test',{},agent.store.profiles['codex'])
        assert submit.await_count==0
    asyncio.run(run())


def test_execute_guard_rejects_combination_even_if_each_action_is_legal(tmp_path):
    from test_agent_plans import plan_for
    async def run():
        agent,s,_,submit=make_agent(tmp_path);agent.running=True;agent.generation=1
        with pytest.raises(ValueError,match='UNKNOWN_DECISION_CANDIDATE'):
            await agent.execute_plan(plan_for(s),s,{},1,agent.store.profiles['codex'],'test',0,{})
        assert submit.await_count==0
    asyncio.run(run())


def test_live_returns_all_current_node_analyses_beyond_recent_cap(tmp_path):
    repo=Repository(tmp_path/'runs');s=snapshot()
    repo.store(dict(event_id='state',kind='snapshot',snapshot=s))
    for index in range(25):repo.store(dict(event_id='decision_'+str(index),kind='decision',snapshot=s,report={'analysis_id':str(index)}))
    view=live(repo)
    assert len(view['recent'])==20 and len(view['node_decisions'])==25
    changed=deepcopy(s);changed['state']['map']['current_position']={'column':2,'row':3}
    repo.store(dict(event_id='next_node',kind='snapshot',snapshot=changed))
    assert live(repo)['node_decisions']==[]
    changed=deepcopy(s);changed['state']['map']['act_index']=1
    repo.store(dict(event_id='next_act',kind='snapshot',snapshot=changed))
    assert live(repo)['node_decisions']==[]
