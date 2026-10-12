"""Adapter from bounded combat candidates to conservative cross-combat timing scenarios."""
from autoslay.decision.contracts import CombatState
from autoslay.evaluation.relic_timing import DelayCost, OpeningContext, TimingPolicy, evaluate_timing

VERSION = "0.1.0"


def analyze_timing(initial: CombatState, combat_report: dict, *, context: OpeningContext | None = None,
                   policy: TimingPolicy | None = None) -> dict:
    if not initial.periodic_relics:
        return evaluate_timing(initial)
    lethal = [c for c in combat_report["candidates"] if c["metrics"]["all_enemies_killed"]
              and not c["metrics"]["projected_death"] and not c["metrics"]["uncertainty"]]
    waits = combat_report.get("wait_candidates", [])
    costs = []
    if lethal:
        immediate = min(c["metrics"]["projected_hp_loss"] for c in lethal)
        costs.append(DelayCost(0, immediate, immediate, True, "modeled_immediate_kill_total_hp_cost"))
    else:
        immediate = 0
    if waits:
        # Current intents form only a partial damage forecast, never a complete future-turn cost bound.
        first_loss = min(c["metrics"]["projected_hp_loss"] for c in waits)
        costs.append(DelayCost(1, 0, None, False,
                              f"visible first-turn extra HP forecast {first_loss}; full delay cost and future lethal unknown"))
    report = evaluate_timing(initial, context=context, policy=policy, costs=tuple(costs))
    report["immediate_kill_found"] = bool(lethal)
    report["wait_candidates_found"] = len(waits)
    report["search_truncated"] = combat_report["search_truncated"]
    report["first_wait_visible_hp_forecast"] = min((c["metrics"]["projected_hp_loss"] for c in waits), default=None)
    return report
