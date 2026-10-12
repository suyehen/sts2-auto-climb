import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path
import pytest
from autoslay.execution.sequence import SequenceExecutor, projection
from autoslay.decision.normalize import normalize
from autoslay.decision.contracts import Operation, Transition, Potion
from autoslay.simulation.engine import CombatSimulator
from autoslay.evaluation.combat import CombatEvaluator
from test_decision import card, state, fixture_snapshot

def snapshot():
    value = fixture_snapshot()
    value.update(ready=True, busy=False, session_epoch="epoch", state_version=1)
    value["state"].update(command_context_id="run", run_id="run")
    value["state"]["player"].update(discard=[], exhaust=[])
    value["state"]["player"]["hand"] = [dict(instance_id=i, entity_id="CARD.STRIKE_IRONCLAD",
        energy_cost=1, target_type="AnyEnemy", description="Deal 6 damage.") for i in ("a", "b")]
    value["state"]["enemies"] = [dict(instance_id="e", entity_id="MONSTER.TEST", hp=100,
        block=0, powers=[], intents=[dict(type="Attack", attack_damage=10, attack_repeats=1)])]
    value["state"]["legal_actions"] = [dict(action_id="play_"+i, type="play_card",
        allowed_target_ids=["e"], allows_no_target=False) for i in ("a", "b")]
    return value

def plan(value, steps=None):
    return dict(plan_id="test-plan", session_epoch="epoch", run_id="run", expected_state_version=1,
                steps=steps or [dict(action_id="play_a", target_id="e"), dict(action_id="play_b", target_id="e")])

class FakeBridge:
    def __init__(self, value=None, mismatch=False, status="completed"):
        self.value = value or snapshot()
        self.calls = []
        self.mismatch, self.status = mismatch, status
    async def fetch(self):
        return copy.deepcopy(self.value)
    async def submit(self, request):
        self.calls.append(request)
        if self.status != "completed":
            return dict(status=self.status)
        s = self.value["state"]
        p = s["player"]
        played = next(c for c in p["hand"] if request.action_id == "play_" + c["instance_id"])
        p["hand"].remove(played)
        p["discard"].append(played)
        p["energy"] -= 1
        s["enemies"][0]["hp"] -= 6
        if self.mismatch:
            p["creature"]["hp"] -= 1
        self.value["state_version"] += 1
        return dict(status="completed", request_id=request.request_id)

def test_two_step_prefix_and_durable_idempotency(tmp_path):
    async def run():
        bridge = FakeBridge()
        runner = SequenceExecutor(tmp_path, bridge.fetch, bridge.submit)
        result = await runner.execute(plan(bridge.value))
        assert result["status"] == "completed"
        assert len(bridge.calls) == 2
        assert [r.expected_state_version for r in bridge.calls] == [1, 2]
        assert all(e["predicted"] for e in result["steps"])
        again = await runner.execute(plan(bridge.value))
        assert again["status"] == "completed" and len(bridge.calls) == 2
        conflict = plan(bridge.value, [dict(action_id="play_a", target_id="e")])
        assert (await runner.execute(conflict))["reason"] == "PLAN_ID_CONFLICT"
    asyncio.run(run())

@pytest.mark.parametrize("change,reason", [("version", "STALE_PLAN"), ("target", "ILLEGAL_STEP"), ("paused", "STATE_NOT_READY")])
def test_preconditions_no_dispatch(tmp_path, change, reason):
    async def run():
        bridge = FakeBridge()
        value = plan(bridge.value)
        if change == "version": value["expected_state_version"] = 0
        if change == "target": value["steps"][0]["target_id"] = "other"
        if change == "paused": bridge.value["ready"] = False
        result = await SequenceExecutor(tmp_path, bridge.fetch, bridge.submit).execute(value)
        assert result["reason"] == reason and not bridge.calls
    asyncio.run(run())

@pytest.mark.parametrize("status", ["awaiting_choice", "failed", "unknown", "queued"])
def test_unsettled_no_second_step_or_replay(tmp_path, status):
    async def run():
        bridge = FakeBridge(status=status)
        runner = SequenceExecutor(tmp_path, bridge.fetch, bridge.submit)
        result = await runner.execute(plan(bridge.value))
        assert result["reason"] == "ACTION_" + status
        await runner.execute(plan(bridge.value))
        assert len(bridge.calls) == 1
    asyncio.run(run())

def test_mismatch_stops_prefix(tmp_path):
    async def run():
        bridge = FakeBridge(mismatch=True)
        result = await SequenceExecutor(tmp_path, bridge.fetch, bridge.submit).execute(plan(bridge.value))
        assert result["reason"] == "PREDICTION_MISMATCH" and len(bridge.calls) == 1
    asyncio.run(run())

@pytest.mark.parametrize("entity,description,boundary", [
    ("POMMEL_STRIKE", "Deal 6 damage. Draw 1 card.", "draw_requires_observation"),
    ("CINDER", "Deal 6 damage. Randomly exhaust 1 card.", "random_exhaust_boundary_requires_observation"),
    ("UNSUPPORTED", "Unknown effect.", "OBSERVATION_REQUIRED")])
def test_information_boundaries_stop_prefix(tmp_path, entity, description, boundary):
    async def run():
        bridge = FakeBridge()
        bridge.value["state"]["player"]["hand"][0].update(entity_id="CARD."+entity, description=description)
        result = await SequenceExecutor(tmp_path, bridge.fetch, bridge.submit).execute(plan(bridge.value))
        assert result["reason"] == boundary and len(bridge.calls) == 1
    asyncio.run(run())

def test_crash_after_dispatch_never_replays(tmp_path):
    async def run():
        bridge = FakeBridge()
        async def fail(request):
            assert (tmp_path / "test-plan.json").exists()
            bridge.calls.append(request)
            raise RuntimeError("interrupted")
        runner = SequenceExecutor(tmp_path, bridge.fetch, fail)
        with pytest.raises(RuntimeError): await runner.execute(plan(bridge.value))
        result = await runner.execute(plan(bridge.value))
        assert result["status"] == "running" and len(bridge.calls) == 1
        assert result["steps"][0]["request"]["request_id"] == "seq-test-plan-0"
    asyncio.run(run())

def test_tungsten_multihit_and_remnant_upper_bound():
    initial = replace(state(hp=30, attacks=(5, 3, 3, 3), block=5), hp_loss_reduction=1)
    result = CombatEvaluator().evaluate(initial, Transition(initial), ())
    assert result["projected_hp_loss"] == 6
    capped = replace(initial, block=0, enemies=(replace(initial.enemies[0], attacks=(40, 40)),), turn_hp_loss_cap=20)
    result = CombatEvaluator().evaluate(capped, Transition(capped), ())
    assert result["projected_hp_loss"] == 20 and result["projected_hp_loss_is_upper_bound"]

def test_self_damage_and_hidden_cap_boundary():
    h = card("h", "HEMOKINESIS", "Lose 2 HP. Deal 15 damage.")
    initial = replace(state(h), hp_loss_reduction=1)
    sim = CombatSimulator(initial)
    assert sim.apply(initial, Operation("play_h", "enemy")).state.hp == 29
    capped = replace(initial, turn_hp_loss_cap=20)
    assert sim.apply(capped, Operation("play_h", "enemy")).boundary == "remaining_turn_hp_cap_requires_observation"

def test_boss_kill_removes_minions():
    hit = card("h", "STRIKE_IRONCLAD", "Deal 6 damage.")
    initial = state(hit, enemy_hp=6)
    initial = replace(initial, enemies=initial.enemies + (replace(initial.enemies[0], instance_id="minion", hp=40, minion=True),))
    result = CombatSimulator(initial).apply(initial, Operation("play_h", "enemy"))
    assert result.state.combat_won and result.state.enemies[1].hp == 40
    metrics = CombatEvaluator().evaluate(initial, result, ())
    assert metrics["all_enemies_killed"] and metrics["effective_hp_damage"] == 6
    assert metrics["projected_hp_loss"] == 0

def test_process_lock_excludes_other_executor(tmp_path):
    from autoslay.execution.lock import dispatch_lock
    async def run():
        bridge = FakeBridge()
        with dispatch_lock(tmp_path) as acquired:
            assert acquired
            result = await SequenceExecutor(tmp_path, bridge.fetch, bridge.submit).execute(plan(bridge.value))
            assert result["reason"] == "EXECUTOR_BUSY" and not bridge.calls
        result = await SequenceExecutor(tmp_path, bridge.fetch, bridge.submit).execute(plan(bridge.value))
        assert result["status"] == "completed"
    asyncio.run(run())

def test_second_wind_excludes_itself_and_exhausts_nonattacks():
    wind = replace(card("w", "SECOND_WIND", "Exhaust nonattacks. Gain 5 Block for each.", target="Self"), card_type="Skill")
    defend = replace(card("d", "DEFEND_IRONCLAD", "Gain 5 Block.", target="Self"), card_type="Skill")
    strike = replace(card("s", "STRIKE_IRONCLAD", "Deal 6 damage."), card_type="Attack")
    initial = state(wind, defend, strike)
    result = CombatSimulator(initial).apply(initial, Operation("play_w"))
    assert result.boundary is None and result.state.block == 5
    assert result.state.hand == (strike,)
    assert result.state.exhausted_composition == (("CARD.DEFEND_IRONCLAD", 1),)
    assert result.state.discard_composition == (("CARD.SECOND_WIND", 1),)
    unknown = replace(defend, entity_id="CARD.UNSUPPORTED")
    initial = state(wind, unknown, strike)
    assert CombatSimulator(initial).apply(initial, Operation("play_w")).boundary == "unknown_exhaust_hooks_requires_observation"

def test_block_potion_is_consumed_once_without_frail_card_modifier():
    initial = replace(state(), player_frail=True,
        potions=(Potion("p", "POTION.BLOCK_POTION", "Gain 12 Block.", "use_p", ("player",)),))
    sim = CombatSimulator(initial)
    result = sim.apply(initial, Operation("use_p", "player"))
    assert result.state.block == 12 and not result.state.potions
    assert Operation("use_p", "player") not in sim.actions(result.state)

def test_resident_authentication_and_shared_transport(tmp_path, monkeypatch):
    import httpx
    from types import SimpleNamespace
    from autoslay.execution import resident
    monkeypatch.setattr(resident, "append_record", lambda *args: None)
    from autoslay.bridge import client as bridge_client
    token_file = tmp_path / "token.txt"
    token_file.write_text("test-secret", encoding="utf-8")
    monkeypatch.setattr(resident, "load_config", lambda: SimpleNamespace(token_file=str(token_file)))
    transports = []
    async def fetch():
        transports.append(bridge_client._transport.get())
        return {"ready": True}
    monkeypatch.setattr(resident, "fetch_state", fetch)
    async def run():
        ready = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(resident.serve(0, ready))
        port = await asyncio.wait_for(ready, 2)
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                url = f"http://127.0.0.1:{port}/state"
                assert (await client.post(url, json={})).status_code == 401
                for _ in range(2):
                    result = await client.post(url, json={}, headers={"Authorization": "Bearer test-secret"})
                    assert result.json() == {"ready": True}
            assert len(transports) == 2 and transports[0] is transports[1] and transports[0] is not None
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())
