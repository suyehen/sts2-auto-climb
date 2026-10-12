import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from autoslay.agent.contracts import DecisionPlan, PlanStep, validate_plan, schema_for, prompt
from autoslay.agent.plans import prepare
from autoslay.decision.deterministic import source
from test_agent import make_agent, payload
from test_deterministic import live


def plan_for(snapshot, actions=('play_a', 'play_b', 'end_turn')):
    return DecisionPlan(state_token=source(snapshot)['state_sha256'], reason='连续攻击后结束回合',
        steps=[PlanStep(action_id=a, target_id='e' if a.startswith('play_') else None) for a in actions])


def apply_strike(snapshot, action):
    player = snapshot['state']['player']
    card = next(c for c in player['hand'] if 'play_'+c['instance_id'] == action)
    player['hand'].remove(card)
    player['discard'].append(card)
    player['energy'] -= 1
    snapshot['state']['enemies'][0]['hp'] -= 6
    snapshot['state']['legal_actions'] = [a for a in snapshot['state']['legal_actions'] if a['action_id'] != action]
    snapshot['state_version'] += 1


def test_plan_schema_legacy_and_limits():
    s = live()
    assert len(validate_plan(payload(s), s).steps) == 1
    assert len(validate_plan(plan_for(s).model_dump_json(), s).steps) == 3
    with pytest.raises(ValueError):
        DecisionPlan(state_token='x', reason='x', steps=[])
    with pytest.raises(ValueError):
        DecisionPlan(state_token='x', reason='x', steps=[PlanStep(action_id='end_turn', target_id=None)]*17)
    with pytest.raises(ValueError, match='STALE'):
        validate_plan(plan_for(s).model_copy(update={'state_token':'wrong'}).model_dump_json(), s)
    with pytest.raises(ValueError, match='ILLEGAL_ACTION'):
        validate_plan(plan_for(s, ('play_a', 'quit_game')).model_dump_json(), s)


@pytest.mark.parametrize('phase',['rewards','card_reward_selection','event','map','shop','rest_site','card_selection'])
def test_noncombat_schema_requires_one_step(phase):
    packet={'visible_state':{'state':{'phase':phase}}}
    assert schema_for(packet)['properties']['steps']['maxItems']==1
    assert 'Return exactly ONE step' in prompt(packet)
    assert schema_for({'visible_state':{'state':{'phase':'combat'}}})['properties']['steps']['maxItems']==16


@pytest.mark.parametrize('outcome',['valid','invalid_again','state_changed','paused'])
def test_reward_multistep_reply_is_corrected_once_before_any_dispatch(tmp_path,outcome):
    async def run():
        calls=[]
        class Provider:
            async def decide(self,packet):
                calls.append(packet)
                if len(calls)==1 and outcome=='state_changed':s['state_version']+=1
                if len(calls)==1 and outcome=='paused':agent.running=False
                steps=[{'action_id':'collect_a','target_id':None}]
                if len(calls)==1 or outcome=='invalid_again':steps.append({'action_id':'collect_b','target_id':None})
                return json.dumps(dict(state_token=source(origin)['state_sha256'],reason='领取奖励',steps=steps)),{'total_tokens':10}
        agent,s,_,submit=make_agent(tmp_path)
        s['state']['phase']='rewards'
        s['state']['legal_actions']=[dict(action_id=a,type='claim_reward',allows_no_target=True,allowed_target_ids=[]) for a in ['collect_a','collect_b']]
        origin=deepcopy(s);agent.running=True;agent.generation=1;agent.model_wait={}
        packet={'visible_state':origin}
        if outcome=='invalid_again':
            with pytest.raises(ValueError,match='MULTI_ACTION_REQUIRES_COMBAT'):
                await agent.decide_plan(Provider(),packet,origin,1,'request',{},agent.store.profiles['codex'])
        else:
            plan,usage=await agent.decide_plan(Provider(),packet,origin,1,'request',{},agent.store.profiles['codex'])
            if outcome=='valid':
                assert len(plan.steps)==1 and plan.steps[0].action_id=='collect_a'
                assert usage['total_tokens']==20 and usage['validation_attempts']==2
                assert calls[1]['validation_feedback']['code']=='MULTI_ACTION_REQUIRES_COMBAT'
            else:assert plan is None
        assert len(calls)==(2 if outcome in {'valid','invalid_again'} else 1)
        assert submit.await_count==0
    asyncio.run(run())


def test_entire_turn_uses_one_model_request(tmp_path):
    async def run():
        calls = []
        class Provider:
            async def decide(self, packet):
                calls.append(packet)
                return plan_for(s).model_dump_json(), {}
        agent, s, _, _ = make_agent(tmp_path, factory=lambda *_: Provider())
        s['state']['card_selection'] = dict(screen_id=None, screen_type=None,
            decision_ready=False, cancelable=False, cards=[], controls=[])
        async def submit(request):
            if request.action_id == 'end_turn':
                s['state']['phase'] = 'game_over'
                s['state_version'] += 1
            else:
                apply_strike(s, request.action_id)
            return dict(status='completed')
        agent.submit = AsyncMock(side_effect=submit)
        await agent.command('/start', {})
        await asyncio.wait_for(agent.task, 3)
        assert len(calls) == 1
        assert [c.args[0].action_id for c in agent.submit.call_args_list] == ['play_a', 'play_b', 'end_turn']
        assert [c.args[0].expected_state_version for c in agent.submit.call_args_list] == [1, 2, 3]
        assert agent.last['plan_step'] == 3 and agent.last['plan_length'] == 3
    asyncio.run(run())


@pytest.mark.parametrize('change', ['hp', 'extra_card', 'legal_action', 'run', 'turn', 'hidden_power', 'relic', 'busy', 'disconnected', 'paused', 'selection'])
def test_changed_state_discards_remaining_plan(tmp_path, change):
    async def run():
        agent, s, _, _ = make_agent(tmp_path)
        initial = deepcopy(s)
        agent.running = True
        agent.generation = 1
        async def submit(request):
            apply_strike(s, request.action_id)
            if change == 'hp': s['state']['player']['creature']['hp'] -= 1
            if change == 'extra_card': s['state']['player']['hand'].append(dict(s['state']['player']['discard'][0], instance_id='new'))
            if change == 'legal_action': s['state']['legal_actions'] = []
            if change == 'run': s['state']['run_id'] = 'other'
            if change == 'turn': s['state']['player']['turn'] = 99
            if change == 'hidden_power': s['state']['player']['creature']['hidden_power_ids'] = ['unknown']
            if change == 'relic': s['state']['player']['relics'] = [{'entity_id':'RELIC.NEW'}]
            if change == 'busy': s['busy'] = True
            if change == 'disconnected': s['connected'] = False
            if change == 'paused': s['state']['control_mode'] = 'paused'
            if change == 'selection': s['state']['card_selection'] = dict(screen_id='choice', decision_ready=True)
            return dict(status='completed')
        agent.submit = AsyncMock(side_effect=submit)
        await agent.execute_plan(plan_for(initial), initial, {}, 1, agent.store.profiles['codex'], 'plan', 1, {})
        assert agent.submit.await_count == 1
        assert agent.last.get('plan_interruption')
    asyncio.run(run())


def test_pause_or_nested_choice_stops_after_current_action(tmp_path):
    async def run():
        for status in ['completed', 'awaiting_choice']:
            agent, s, _, _ = make_agent(tmp_path / status)
            initial = deepcopy(s)
            agent.running = True
            agent.generation = 1
            async def submit(request):
                apply_strike(s, request.action_id)
                if status == 'completed': await agent.pause()
                return dict(status=status)
            agent.submit = AsyncMock(side_effect=submit)
            await agent.execute_plan(plan_for(initial), initial, {}, 1, agent.store.profiles['codex'], 'plan', 1, {})
            assert agent.submit.await_count == 1
    asyncio.run(run())


def test_draw_and_end_turn_truncate_unseen_continuation():
    s = live()
    s['state']['enemies'][0]['hp'] = 100
    s['state']['player']['hand'][0].update(entity_id='CARD.SHRUG_IT_OFF', target_type='Self', description='Gain 8 Block. Draw 1 card.')
    p = plan_for(s)
    p.steps[0].target_id = None
    assert len(prepare(p, s)) == 1
    assert len(prepare(plan_for(s, ('end_turn', 'play_b')), s)) == 1


def test_unmodeled_effect_only_allows_one_fresh_action():
    s = live()
    s['state']['player']['hand'][0]['entity_id'] = 'CARD.UNKNOWN'
    assert len(prepare(plan_for(s), s)) == 1
