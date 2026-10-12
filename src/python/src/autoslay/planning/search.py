from dataclasses import dataclass
from time import perf_counter
from autoslay.decision.contracts import CombatState, Evaluator, Simulator, Transition, UnsupportedEffect

VERSION = "0.3.0"


@dataclass(frozen=True)
class SearchBudget:
    max_nodes: int = 2000
    max_depth: int = 6
    time_ms: int = 200
    top_k: int = 5

    def __post_init__(self):
        if any(type(n) is not int for n in (self.max_nodes, self.max_depth, self.time_ms, self.top_k)):
            raise ValueError("Integer search budgets required")
        if not 1 <= self.max_nodes <= 100000 or not 1 <= self.max_depth <= 12 or not 1 <= self.time_ms <= 5000 or not 1 <= self.top_k <= 20:
            raise ValueError("Search budget outside supported limits")


def search(initial: CombatState, simulator: Simulator, evaluator: Evaluator, budget: SearchBudget) -> dict:
    started = perf_counter()
    stack = [(Transition(initial), ())]
    visited = set()
    candidates, boundaries, waits, problems = [], [], [], set()
    nodes = 0
    truncated = False
    while stack:
        if nodes >= budget.max_nodes or (perf_counter() - started) * 1000 >= budget.time_ms:
            truncated = True
            break
        result, steps = stack.pop()
        nodes += 1
        key = (result.state, result.boundary, result.draw_count, result.requested_draw, result.draw_reason)
        if key in visited:
            continue
        visited.add(key)
        metrics = evaluator.evaluate(initial, result, steps)
        record = dict(steps=[dict(action_id=a.action_id, target_id=a.target_id) for a in steps], metrics=metrics,
                      boundary=result.boundary, draw_count=result.draw_count,
                      requested_draw=result.requested_draw, draw_reason=result.draw_reason,
                      draw_composition=dict(result.state.draw_composition),
                      discard_composition=dict(result.state.discard_composition),
                      discard_known=result.state.discard_known,
                      remaining_hand_size=len(result.state.hand))
        if result.boundary:
            (waits if result.boundary == "next_turn_requires_observation" else boundaries).append(record)
            continue
        candidates.append(record)
        if len(steps) >= budget.max_depth:
            if simulator.actions(result.state):
                truncated = True
            continue
        if result.state.hp <= 0 or result.state.combat_won or all(e.hp <= 0 for e in result.state.enemies):
            continue
        # Identical intermediate states need expanding only once; no hidden RNG is used.
        for action in reversed(simulator.actions(result.state)):
            try:
                stack.append((simulator.apply(result.state, action), steps + (action,)))
            except UnsupportedEffect as exc:
                problems.add(str(exc))
    candidates.sort(key=lambda c: evaluator.sort_key(c["metrics"]), reverse=True)
    # Draw prefixes are intentionally separate from finished deterministic turn candidates.
    boundaries.sort(key=lambda c: c["metrics"]["heuristic_score"], reverse=True)
    waits.sort(key=lambda c: evaluator.sort_key(c["metrics"]), reverse=True)
    return dict(candidates=candidates[:budget.top_k], observation_boundaries=boundaries[:budget.top_k],
                wait_candidates=waits[:budget.top_k],
                nodes=nodes, elapsed_ms=(perf_counter() - started) * 1000, search_truncated=truncated,
                problems=sorted(problems), optimality_proven=False)
