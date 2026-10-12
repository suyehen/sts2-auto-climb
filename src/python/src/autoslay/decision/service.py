"""Compose replaceable modules into a read-only report for Codex."""
from autoslay.configuration import decision_config
import hashlib
import json
import uuid
from dataclasses import asdict
from pathlib import Path
from autoslay.decision import contracts, normalize as adapter
from autoslay.evaluation import combat as combat_eval, deck as deck_eval
from autoslay.planning import search as planner
from autoslay.probability import draw
from autoslay.rules import registry
from autoslay.simulation import engine
from autoslay.telemetry import timing, journal
from autoslay.probability.card_flow import draw_forecast
from autoslay.simulation import card_flow
from autoslay.rules import periodic_relics
from autoslay.planning import relic_timing
from autoslay.evaluation.relic_timing import OpeningContext, TimingPolicy
from autoslay.paths import ROOT

VERSION = "0.5.0"


def load_policy(path: Path) -> combat_eval.CombatPolicy:
    return combat_eval.CombatPolicy(**decision_config(path))


def analyze(snapshot: dict, *, budget: planner.SearchBudget | None = None,
            policy: combat_eval.CombatPolicy | None = None, journal_path: Path | None = None,
            timing_context: OpeningContext | None = None, timing_policy: TimingPolicy | None = None,
            lookahead_settings: dict | None = None, scenarios: list | None = None) -> dict:
    clock = timing.Timing()
    with clock.stage("normalize"):
        initial = adapter.normalize(snapshot)
    with clock.stage("compile_rules"):
        simulator = engine.CombatSimulator(initial)
        evaluator = combat_eval.CombatEvaluator(policy)
    with clock.stage("search_and_evaluate"):
        report = planner.search(initial, simulator, evaluator, budget or planner.SearchBudget())
    with clock.stage("draw_probability"):
        for candidate in report["observation_boundaries"]:
            if candidate["boundary"] == "draw_requires_observation":
                candidate["draw_analysis"] = draw_forecast(candidate)
    with clock.stage("relic_timing"):
        if initial.periodic_relics and timing_policy is None:
            timing_policy = TimingPolicy(**decision_config(ROOT / "config/decision/relic-timing.default.json"))
        report["relic_timing"] = relic_timing.analyze_timing(initial, report, context=timing_context, policy=timing_policy)
    if lookahead_settings is not None:
        from autoslay.planning.lookahead import evaluate_lookahead,VERSION as LOOKAHEAD_VERSION
        from autoslay.knowledge.templates import index
        with clock.stage('lookahead'):
            entries, knowledge_hash = index(expected_version=snapshot['game_version'])
            report['lookahead'] = evaluate_lookahead(snapshot, initial, report['candidates'], report['observation_boundaries'],
                                                   entries, lookahead_settings, scenarios)
            versions_extra = dict(lookahead=LOOKAHEAD_VERSION, knowledge_hash=knowledge_hash)
    else:
        versions_extra = {}
    legal = {a["action_id"]: a for a in snapshot["state"].get("legal_actions", [])}
    for candidate in report["candidates"] + report["observation_boundaries"] + report["wait_candidates"]:
        current=initial; trace=[]
        for step in candidate['steps']:
            try:
                action=contracts.Operation(**step); result=simulator.apply(current,action)
                trace.append(dict(action=step,before=asdict(current),after=asdict(result.state),boundary=result.boundary))
                current=result.state
                if result.boundary: break
            except contracts.UnsupportedEffect as exc:
                trace.append(dict(action=step,error=str(exc))); break
        candidate['state_trace']=trace
        first = candidate["steps"][0] if candidate["steps"] else None
        catalog = legal.get(first["action_id"]) if first else None
        candidate["first_step_currently_legal"] = bool(catalog and (
            first["target_id"] in catalog.get("allowed_target_ids", []) if first["target_id"] is not None
            else catalog.get("allows_no_target", False)))
        candidate["requires_fresh_validation"] = True
    versions = dict(contract=contracts.CONTRACT_VERSION, decision=VERSION, adapter=adapter.VERSION,
                    rules=simulator.rules.version, simulation=simulator.version, probability=draw.VERSION,
                    search=planner.VERSION, combat_evaluation=evaluator.version, deck_evaluation=deck_eval.VERSION,
                    telemetry=timing.VERSION, journal=journal.VERSION, policy=evaluator.policy.version)
    versions.update(card_flow=card_flow.VERSION, periodic_relic_rules=periodic_relics.VERSION,
                    relic_timing_planner=relic_timing.VERSION, relic_timing_policy=timing_policy.version if timing_policy else None)
    versions.update(versions_extra)
    report.update(schema_version="decision/1.0", analysis_id=uuid.uuid4().hex,
                  source=dict(session_epoch=snapshot.get("session_epoch"), state_version=snapshot.get("state_version"),
                              run_id=snapshot["state"].get("command_context_id") or snapshot["state"].get("run_id"),
                              game_version=snapshot.get("game_version"), bridge_version=snapshot.get("bridge_version"),
                              state_sha256=hashlib.sha256(json.dumps(snapshot["state"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()),
                  module_versions=versions, policy=asdict(evaluator.policy), budget=asdict(budget or planner.SearchBudget()),
                  timings_ms=clock.stages_ms, coverage=dict(unsupported_cards=simulator.unsupported,
                    unsupported_potions=list(simulator.unsupported_potions), interaction_warnings=list(initial.warnings)),
                  normalized_input=asdict(initial),execution_authorized=False, live_state_ready=bool(snapshot.get("ready") and not snapshot.get("busy")),
                  limitations=["read-only candidates; Codex chooses actions from a fresh legal catalog",
                               "search is bounded and does not prove optimal play",
                               "draw continuations are scenario estimates with explicit coverage" if lookahead_settings is not None else "draw prefixes have no estimated continuation value yet",
                               "forecast covers current visible attacks, not complete enemy-turn scripts",
                               "random exhaustion, upgrades, nonattack exhaustion and newly applied Weak stop at observation boundaries",
                               "remaining Beating Remnant allowance is hidden; self-HP loss stops at observation, attack forecast uses a conservative upper bound",
                               "Permafrost power-card trigger is unsupported; known non-power cards have no trigger",
                               "potion/random generated/card-draw hooks not implemented; known generated placement has a separate tested primitive",
                               "relic timing is a bounded heuristic scenario; future kill and full waiting cost require observation"])
    if journal_path is not None:
        journal.append_record(journal_path, report)
    return report
