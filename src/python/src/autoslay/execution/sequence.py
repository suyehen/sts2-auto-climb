"""Guarded combat prefixes. Unknown effects always return control to Codex."""
import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from time import perf_counter
from pydantic import BaseModel, ConfigDict, Field
from autoslay.bridge.client import ActionRequest, fetch_state, submit_action
from autoslay.decision.contracts import Operation, UnsupportedEffect
from autoslay.decision.normalize import normalize
from autoslay.simulation.engine import CombatSimulator
from autoslay.paths import ROOT
from autoslay.execution.lock import dispatch_lock
from autoslay.actions import operation_error, validate_catalog_identity

VERSION = "1.0.0"

class Step(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action_id: str = Field(min_length=1)
    target_id: str | None = None

class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    session_epoch: str
    run_id: str
    expected_state_version: int = Field(ge=0)
    steps: list[Step] = Field(min_length=1, max_length=12)

def projection(state):
    return dict(hp=state.hp, block=state.block, energy=state.energy,stars=state.stars,
                hand=sorted((c.instance_id, c.entity_id, c.cost, c.target_type, c.upgrade_level,c.star_cost) for c in state.hand),
                strength=state.base_strength + state.strength_delta, colossus=state.colossus,
                weak=state.player_weak, frail=state.player_frail, turn=state.turn,
                draw=state.draw_composition, discard=state.discard_composition, exhaust=state.exhausted_composition,
                potions=sorted(p.instance_id for p in state.potions),
                enemies=sorted((e.instance_id, e.hp, e.block, e.vulnerable, e.attacks, e.strength, e.weak, e.minion)
                               for e in state.enemies if e.hp > 0))

def save(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)

class SequenceExecutor:
    def __init__(self, directory: Path | None = None, fetch=fetch_state, submit=submit_action):
        self.directory = directory or ROOT / ".runtime/logs/plans"
        self.fetch, self.submit = fetch, submit
        self.lock = asyncio.Lock()

    async def execute(self, value: dict):
        plan = Plan.model_validate(value)
        async with self.lock:
            with dispatch_lock(self.directory) as acquired:
                if not acquired:
                    return {"status": "rejected", "reason": "EXECUTOR_BUSY"}
                return await self._execute(plan)

    async def _execute(self, plan):
        path = self.directory / (plan.plan_id + ".json")
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old["plan"] != plan.model_dump():
                return {"status": "rejected", "reason": "PLAN_ID_CONFLICT"}
            # Includes interrupted/in-flight plans. Never restart or retry a mutation.
            return old
        record = dict(version=VERSION, plan=plan.model_dump(), status="running", steps=[])
        started = perf_counter()
        def finish(reason, snapshot=None):
            record.update(status="stopped", reason=reason, snapshot=snapshot,
                          total_ms=(perf_counter() - started) * 1000)
            save(path, record)
            return record
        before = await self.fetch()
        validate_catalog_identity(before)
        s = before.get("state") or {}
        if (before.get("session_epoch") != plan.session_epoch or
            before.get("state_version") != plan.expected_state_version or
            (s.get("command_context_id") or s.get("run_id")) != plan.run_id):
            return finish("STALE_PLAN", before)
        try:
            model = normalize(before)
            simulator = CombatSimulator(model)
        except (ValueError, KeyError, TypeError):
            model = simulator = None
        for index, step in enumerate(plan.steps):
            validate_catalog_identity(before)
            s = before.get("state") or {}
            if (not before.get("ready") or before.get("busy") or s.get("phase") != "combat" or
                before.get("session_epoch") != plan.session_epoch or
                (s.get("command_context_id") or s.get("run_id")) != plan.run_id):
                return finish("STATE_NOT_READY", before)
            legal = next((a for a in s.get("legal_actions", []) if a["action_id"] == step.action_id), None)
            if operation_error(legal,step.target_id,combat_only=True):
                return finish("ILLEGAL_STEP", before)
            transition = None
            if simulator is not None:
                try:
                    transition = simulator.apply(model, Operation(step.action_id, step.target_id))
                except UnsupportedEffect:
                    pass
            request = ActionRequest(request_id=f"seq-{plan.plan_id}-{index}", run_id=plan.run_id,
                session_epoch=plan.session_epoch, expected_state_version=before["state_version"],
                action_id=step.action_id, target_id=step.target_id)
            entry = dict(request=request.model_dump(), before=before,
                         predicted=asdict(transition) if transition else None)
            record["steps"].append(entry)
            save(path, record)  # Durable identity BEFORE dispatch.
            action_started = perf_counter()
            receipt = await self.submit(request)
            entry.update(receipt=receipt, action_ms=(perf_counter() - action_started) * 1000)
            save(path, record)
            after = await self.fetch()
            entry["after"] = after
            if receipt.get("status") != "completed":
                return finish("ACTION_" + str(receipt.get("status", "unresolved")), after)
            if after.get("state", {}).get("phase") != "combat":
                return finish("PHASE_CHANGED", after)
            if transition is None or transition.boundary or step.action_id == "end_turn":
                return finish(transition.boundary if transition and transition.boundary else
                              "OBSERVATION_REQUIRED", after)
            try:
                actual = normalize(after)
                matched = projection(actual) == projection(transition.state) and not actual.warnings
            except (ValueError, KeyError, TypeError):
                matched = False
            if not matched:
                return finish("PREDICTION_MISMATCH", after)
            model, before = transition.state, after
        record.update(status="completed", reason="PLAN_COMPLETED", snapshot=before,
                      total_ms=(perf_counter() - started) * 1000)
        save(path, record)
        return record
