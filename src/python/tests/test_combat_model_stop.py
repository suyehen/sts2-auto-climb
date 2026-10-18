import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from autoslay.agent.runtime import Agent
from autoslay.decision.model_errors import CombatModelError, require_known_combat, require_known_batch
from autoslay.execution.deterministic import DeterministicExecutor
from test_agent import store
from test_deterministic import live


@pytest.mark.parametrize('effect', ['player_power', 'enemy_power', 'relic', 'card', 'hidden_power', 'modifier'])
def test_unknown_effect_stops_agent_without_dispatch_or_persistent_marker(tmp_path, effect):
    snapshot = live()
    player = snapshot['state']['player']
    enemy = snapshot['state']['enemies'][0]
    if effect == 'player_power':
        player['creature']['powers'] = [dict(entity_id='POWER.UNKNOWN', amount=1)]
    elif effect == 'enemy_power':
        enemy['powers'] = [dict(entity_id='POWER.UNKNOWN', amount=1)]
    elif effect == 'relic':
        player['relics'] = [dict(entity_id='RELIC.UNKNOWN')]
    elif effect == 'card':
        player['hand'][0]['entity_id'] = 'CARD.UNKNOWN'
    elif effect == 'hidden_power':
        enemy['hidden_power_ids'] = ['POWER.UNKNOWN']
    else:
        player['hand'][0]['enchantment_id'] = 'ENCHANTMENT.UNKNOWN'

    async def run():
        controls = []
        async def control(mode):
            controls.append(mode)
            return {}
        agent = Agent(store(tmp_path), fetch=AsyncMock(return_value=snapshot), control=control,
                      factory=lambda *a: pytest.fail('unknown effects must not reach the model'),
                      deterministic=AsyncMock(), journal=tmp_path/'agent', record=False)
        await agent.command('/start', {})
        await asyncio.wait_for(agent.task, 5)
        assert not agent.running and agent.phase == 'error'
        assert agent.error == 'COMBAT_MODEL_UNKNOWN_EFFECT'
        assert agent.error_details['blockers']
        assert controls[-1] == 'paused'
        agent.deterministic.execute.assert_not_called()
        assert (tmp_path/'agent/diagnostics/latest.json').exists()
        assert not (tmp_path/'.runtime/local/agent/combat-batch-models.json').exists()
        assert not (tmp_path/'.runtime/local/agent/combat-strategy-disabled.json').exists()
        # Removing the actual unknown effect permits this state again.
        require_known_combat(live())
    asyncio.run(run())


def test_unknown_effect_also_blocks_direct_executor_before_submit(tmp_path):
    snapshot = live()
    snapshot['state']['player']['creature']['powers'] = [dict(entity_id='POWER.UNKNOWN', amount=1)]
    submit = AsyncMock()
    async def run():
        executor = DeterministicExecutor(tmp_path/'executor', AsyncMock(return_value=snapshot), submit, record=False)
        with pytest.raises(CombatModelError, match='COMBAT_MODEL_UNKNOWN_EFFECT'):
            await executor.execute('unknown', bounded=True)
        submit.assert_not_called()
    asyncio.run(run())


def test_unknown_forecast_is_a_failure_without_a_persistent_exclusion():
    report = dict(analysis_id='test-analysis', combat=dict(batches=dict(
        execution_blockers=['unknown_generated_card:CARD.UNKNOWN'])))
    with pytest.raises(CombatModelError) as failure:
        require_known_batch(report)
    assert failure.value.details['analysis_id'] == 'test-analysis'
    assert failure.value.details['blockers'] == ['unknown_generated_card:CARD.UNKNOWN']
    require_known_batch(dict(combat=dict(batches=dict(execution_blockers=[]))))


def test_model_mismatch_stops_agent_and_exposes_differences(tmp_path, monkeypatch):
    snapshot = live()
    comparison = dict(checks=[dict(field='enemy.hp', predicted=10, observed=12, matches=False)])
    async def execute(*args, **kwargs):
        raise CombatModelError('COMBAT_MODEL_PREDICTION_MISMATCH', comparison=comparison,
                               analysis_id='test-analysis', request_id='test-request')
    async def run():
        local = AsyncMock()
        local.execute.side_effect = execute
        agent = Agent(store(tmp_path), fetch=AsyncMock(return_value=deepcopy(snapshot)),
                      control=AsyncMock(return_value={}), deterministic=local,
                      analyze=lambda s: dict(analysis_id='test-analysis'),
                      journal=tmp_path/'agent', record=False)
        # Exercise the local execution error path with a known combat snapshot.
        import autoslay.agent.runtime as runtime
        monkeypatch.setattr(runtime, 'assess', lambda *args: dict(eligible=True))
        await agent.command('/start', {})
        await asyncio.wait_for(agent.task, 5)
        assert not agent.running and agent.phase == 'error'
        assert agent.error == 'COMBAT_MODEL_PREDICTION_MISMATCH'
        assert agent.error_details['comparison'] == comparison
        assert agent.error_details['request_id'] == 'test-request'
        assert local.execute.call_count == 1
        agent.control.assert_awaited_with('paused')
    asyncio.run(run())
