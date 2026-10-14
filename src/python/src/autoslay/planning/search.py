from dataclasses import dataclass, asdict, replace
from autoslay.decision.cache import digest
from time import perf_counter
from collections import deque
from autoslay.decision.contracts import CombatState, Evaluator, Simulator, Transition, UnsupportedEffect, Operation

VERSION = "0.5.0"


@dataclass(frozen=True)
class SearchBudget:
    max_nodes: int = 2000
    max_depth: int = 6
    time_ms: int = 200
    top_k: int = 5
    continuations_per_group: int = 32

    def __post_init__(self):
        if any(type(n) is not int for n in (self.max_nodes, self.max_depth, self.time_ms, self.top_k, self.continuations_per_group)):
            raise ValueError("Integer search budgets required")
        if not 1 <= self.max_nodes <= 100000 or not 1 <= self.max_depth <= 12 or not 1 <= self.time_ms <= 5000 or not 1 <= self.top_k <= 20:
            raise ValueError("Search budget outside supported limits")
        if not 1 <= self.continuations_per_group <= 128:
            raise ValueError("Invalid decision candidate budget")


def dominates(a, b):
    """Only used within identical observation/coverage/resource classes."""
    if a.get('continuation_context')!=b.get('continuation_context'):return False
    keys = ('projected_hp_loss', 'remaining_primary_enemy_hp')
    benefits = ('heuristic_score', 'remaining_energy', 'remaining_stars',
                'permanent_strength_gain', 'effective_block')
    av = tuple(-a['metrics'].get(k, 0) for k in keys) + tuple(a['metrics'].get(k, 0) for k in benefits)
    bv = tuple(-b['metrics'].get(k, 0) for k in keys) + tuple(b['metrics'].get(k, 0) for k in benefits)
    return all(x >= y for x, y in zip(av, bv)) and any(x > y for x, y in zip(av, bv))


def search(initial: CombatState, simulator: Simulator, evaluator: Evaluator, budget: SearchBudget) -> dict:
    started = perf_counter()
    stack = [(Transition(initial), ())]
    roots = deque()
    queues = {}
    edge_cache = {}
    cache_hits = 0
    visited = set()
    candidates, boundaries, waits, problems = [], [], [], set()
    nodes = 0
    truncated = False
    limits = set()
    failed = {}
    while stack or roots:
        if nodes >= budget.max_nodes or (perf_counter() - started) * 1000 >= budget.time_ms:
            truncated = True
            limits.add('node_limit' if nodes >= budget.max_nodes else 'time_limit')
            break
        if stack:
            result, steps = stack.pop()
        else:
            root = roots.popleft()
            result, steps = queues[root].pop()
            if queues[root]: roots.append(root)
        nodes += 1
        first = steps[0] if steps else None
        # Pending draw effects and card variants are part of the continuation key.
        key = (first, result)
        if key in visited:
            continue
        visited.add(key)
        metrics = evaluator.evaluate(initial, result, steps)
        record = dict(steps=[dict(action_id=a.action_id, target_id=a.target_id) for a in steps], metrics=metrics,
                      continuation_context=digest(asdict(replace(result.state,hp=0,block=0,energy=0,
                                                               enemy_hp_loss=0,self_hp_loss=0,healing=0))),
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
                limits.add('depth_limit')
            continue
        if result.state.hp <= 0 or result.state.combat_won or all(e.hp <= 0 for e in result.state.enemies):
            continue
        # Preserve competing first steps even when they converge to one state.
        for action in reversed(simulator.actions(result.state)):
            try:
                edge = (result.state, action)
                if edge in edge_cache:
                    transition = edge_cache[edge]
                    cache_hits += 1
                else:
                    transition = simulator.apply(result.state, action)
                    edge_cache[edge] = transition
                root = first or action
                queue = queues.setdefault(root, [])
                if not queue and root not in roots: roots.append(root)
                queue.append((transition, steps + (action,)))
            except UnsupportedEffect as exc:
                problems.add(str(exc))
                limits.add('mechanics_boundary')
                failed.setdefault(first or action, set()).add(str(exc))
    candidates.sort(key=lambda c: evaluator.sort_key(c["metrics"]), reverse=True)
    # Draw prefixes are intentionally separate from finished deterministic turn candidates.
    boundaries.sort(key=lambda c: c["metrics"]["heuristic_score"], reverse=True)
    waits.sort(key=lambda c: evaluator.sort_key(c["metrics"]), reverse=True)
    # UI top_k must not determine which actions the local policy can compare.
    # Keep separate resource/boundary classes within each first-step group.
    # Storage is bounded by the visited node budget; unknown branches still fail closed.
    groups = {}
    pool_incomplete = False
    for record in candidates + boundaries + waits:
        first = record['steps'][0] if record['steps'] else dict(action_id='end_turn', target_id=None)
        potion_ids = tuple(sorted(s['action_id'] for s in record['steps'] if s['action_id'].startswith('use_potion_')))
        key = (first['action_id'], first['target_id'], record['boundary'], potion_ids,
               bool(record['metrics'].get('permanent_strength_gain')),
               tuple(record['metrics'].get('uncertainty') or []))
        group = groups.setdefault(key, [])
        if any(dominates(other, record) for other in group): continue
        group[:] = [other for other in group if not dominates(record, other)]
        group.append(record)
        group.sort(key=lambda c: evaluator.sort_key(c['metrics']), reverse=True)
        if len(group) > budget.continuations_per_group:
            pool_incomplete = True
            del group[budget.continuations_per_group:]
    decision_candidates = [record for group in groups.values() for record in group]
    first_steps = sorted({(key[0], key[1]) for key in groups}, key=lambda x: (x[0], x[1] or ''))
    expected = set(simulator.actions(initial)) | {Operation('end_turn')}
    summaries = []
    for action in sorted(expected, key=lambda a: (a.action_id, a.target_id or '')):
        records = [r for r in decision_candidates if (r['steps'][0] if r['steps'] else
                   dict(action_id='end_turn', target_id=None)) == dict(action_id=action.action_id, target_id=action.target_id)]
        pending = len(queues.get(action, []))
        reasons = sorted(failed.get(action, set()))
        if pending: reasons.append('unsearched_alternatives')
        if not records and not reasons: reasons.append('first_step_not_evaluated')
        summaries.append(dict(action_id=action.action_id, target_id=action.target_id,
                              continuation_count=len(records), unsearched_alternatives=pending,
                              coverage='complete' if records and not reasons and not truncated and not pool_incomplete else 'incomplete',
                              reasons=reasons, lower_bound=None, upper_bound=None, bound_kind='unknown'))
    return dict(candidates=candidates[:budget.top_k], observation_boundaries=boundaries[:budget.top_k],
                wait_candidates=waits[:budget.top_k],
                decision_candidates=decision_candidates,
                first_step_coverage=[dict(action_id=a, target_id=t) for a,t in first_steps],
                first_action_summaries=summaries, candidate_pool_incomplete=pool_incomplete,
                continuation_cache_hits=cache_hits, truncation_reasons=sorted(limits),
                unsearched_branches=sum(len(q) for q in queues.values()),
                decision_candidate_count=len(decision_candidates),
                nodes=nodes, elapsed_ms=(perf_counter() - started) * 1000, search_truncated=truncated,
                problems=sorted(problems), optimality_proven=False)
