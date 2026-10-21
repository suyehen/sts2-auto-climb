"""Joint cross-combat timing value, independent of card simulation and action dispatch."""
from dataclasses import asdict, dataclass
from math import isfinite
from autoslay.decision.contracts import CombatState
from autoslay.rules.periodic_relics import advance

VERSION = "0.1.0"


@dataclass(frozen=True)
class TimingPolicy:
    version: str = "1.0.0"
    draw_value: float = 1.0
    energy_value: float = 2.0
    hp_cost: float = 1.0
    turn_discount: float = 0.7
    reserve_hp: int = 1
    max_delay: int = 3
    horizon: int = 3

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("Policy version required")
        for v in (self.draw_value, self.energy_value, self.hp_cost, self.turn_discount):
            if type(v) not in (int, float) or not isfinite(v) or v < 0:
                raise ValueError("Finite nonnegative timing weights required")
        if self.turn_discount > 1 or self.hp_cost <= 0:
            raise ValueError("Discount must be <=1 and HP cost positive")
        if any(type(v) is not int for v in (self.reserve_hp, self.max_delay, self.horizon)) or not (
            self.reserve_hp >= 1 and 0 <= self.max_delay <= 6 and 1 <= self.horizon <= 6
        ):
            raise ValueError("Invalid timing budgets")


@dataclass(frozen=True)
class OpeningContext:
    opening_draw: int = 5
    later_draw: int = 5
    opening_hand: int = 0
    later_hand: int = 0
    draw_blocked_at_hand_draw: bool = False
    supply_per_turn: tuple[int, ...] | None = None
    basis: str = "assumed_standard_draw_and_empty_hand; verify innate/retain/relic modifiers"

    def __post_init__(self):
        if any(type(v) is not int or not 0 <= v <= 100 for v in (self.opening_draw, self.later_draw)):
            raise ValueError("Invalid draw counts")
        if any(type(v) is not int or not 0 <= v <= 10 for v in (self.opening_hand, self.later_hand)):
            raise ValueError("Invalid future hand size")
        if self.supply_per_turn is not None and any(type(v) is not int or v < 0 for v in self.supply_per_turn):
            raise ValueError("Invalid future card supply")
        if type(self.draw_blocked_at_hand_draw) is not bool or not isinstance(self.basis, str) or not self.basis:
            raise ValueError("Explicit draw permission and context basis required")


@dataclass(frozen=True)
class DelayCost:
    turns: int
    hp_low: float
    hp_high: float | None
    finish_supported: bool = False
    basis: str = "unknown_future_turns"
    resource_penalty: float = 0.0

    def __post_init__(self):
        if type(self.turns) is not int or self.turns < 0 or type(self.hp_low) not in (int, float) or not isfinite(self.hp_low) or self.hp_low < 0:
            raise ValueError("Invalid delay cost")
        if self.hp_high is not None and (type(self.hp_high) not in (int, float) or not isfinite(self.hp_high) or self.hp_high < self.hp_low):
            raise ValueError("Invalid HP cost range")
        if type(self.resource_penalty) not in (int, float) or not isfinite(self.resource_penalty) or self.resource_penalty < 0:
            raise ValueError("Invalid resource penalty")
        if type(self.finish_supported) is not bool or not isinstance(self.basis, str) or not self.basis:
            raise ValueError("Explicit finish evidence required")


def _profile(relics, context: OpeningContext, policy: TimingPolicy, hand_limit: int) -> dict:
    current = relics
    rows, value = [], 0.0
    for turn in range(1, policy.horizon + 1):
        after, extra_draw, energy = advance(current)
        triggers = [r.entity_id for before, r in zip(current, after) if before.counter == before.period - 1]
        hand = context.opening_hand if turn == 1 else context.later_hand
        base = context.opening_draw if turn == 1 else context.later_draw
        capacity = max(0, hand_limit - hand)
        supply = context.supply_per_turn[turn - 1] if context.supply_per_turn and turn <= len(context.supply_per_turn) else 1000000
        base_actual = min(base, capacity, supply)
        total_actual = min(base + extra_draw, capacity, supply)
        if context.draw_blocked_at_hand_draw:
            base_actual = total_actual = 0
        effective = total_actual - base_actual
        discount = policy.turn_discount ** (turn - 1)
        value += discount * (effective * policy.draw_value + energy * policy.energy_value)
        rows.append(dict(turn=turn, triggering_relics=triggers, counters={r.entity_id: r.counter for r in after},
                         nominal_bonus_draw=extra_draw, actual_bonus_draw=effective,
                         actual_total_draw=total_actual, extra_energy=energy, discount=discount))
        current = after
    return dict(turns=rows, heuristic_value=value)


def evaluate_timing(state: CombatState, *, context: OpeningContext | None = None,
                    policy: TimingPolicy | None = None, costs: tuple[DelayCost, ...] = ()) -> dict:
    if not state.periodic_relics:
        return dict(enabled=False, reason="no_supported_periodic_relics", options=[], recommendation="ordinary_combat")
    policy, context = policy or TimingPolicy(), context or OpeningContext()
    if any(r.counter is None for r in state.periodic_relics):
        return dict(enabled=True, supported=False, reason="unknown_or_transient_counter; never assumed zero",
                    options=[], recommendation="observe_counter_again")
    if len({r.entity_id for r in state.periodic_relics}) != len(state.periodic_relics):
        return dict(enabled=True, supported=False, reason="duplicate relic identities need observed instance mechanics", options=[])
    if state.hand_limit != 10:
        return dict(enabled=True, supported=False, reason="unsupported_hand_capacity", options=[])
    cost_by_turn = {c.turns: c for c in costs}
    if len(cost_by_turn) != len(costs):
        raise ValueError("Duplicate delay cost estimates")
    baseline = _profile(state.periodic_relics, context, policy, state.hand_limit)
    baseline_cost = cost_by_turn.get(0)
    baseline_low = baseline_cost.hp_low if baseline_cost else 0
    baseline_high = baseline_cost.hp_high if baseline_cost and baseline_cost.hp_high is not None else 0
    baseline_resource = baseline_cost.resource_penalty if baseline_cost else 0
    rows = []
    for delay in range(policy.max_delay + 1):
        rotated, current_draw, current_energy = advance(state.periodic_relics, delay)
        future = _profile(rotated, context, policy, state.hand_limit)
        delta = future["heuristic_value"] - baseline["heuristic_value"]
        cost = cost_by_turn.get(delay, DelayCost(delay, 0, None))
        # Costs cover the entire finish, including immediate self-damage. Compare incremental costs,
        # but check survival using total loss so baseline damage cannot disappear from the reserve.
        gain_low = None if cost.hp_high is None else delta - (cost.hp_high - baseline_low) * policy.hp_cost - (cost.resource_penalty - baseline_resource)
        gain_high = delta - (cost.hp_low - baseline_high) * policy.hp_cost - (cost.resource_penalty - baseline_resource)
        survives = cost.hp_high is not None and state.hp - cost.hp_high >= policy.reserve_hp
        certified = cost.finish_supported and survives and not state.warnings
        rows.append(dict(delay_turns=delay, ending_counters={r.entity_id: r.counter for r in rotated},
                         next_combat=future, future_value_delta=delta,
                         current_combat_trigger_bonus=dict(draw=current_draw, energy=current_energy, value=None),
                         hp_cost=asdict(cost), net_value_low=gain_low, net_value_high=gain_high,
                         certified_finish_and_survival=certified,
                         decision="reject_death_or_reserve" if cost.hp_high is not None and not survives else
                                  "requires_future_observation" if not certified else
                                  "beneficial_under_supplied_assumptions" if gain_low is not None and gain_low > 0 else
                                  "no_positive_guaranteed_gain"))
    baseline_finish = bool(baseline_cost and baseline_cost.finish_supported and baseline_cost.hp_high is not None
                           and state.hp - baseline_cost.hp_high >= policy.reserve_hp and not state.warnings)
    # Direct battle actions remain Codex's decision; do not turn hypothetical finish into legal lethal.
    positive = [r for r in rows if r["delay_turns"] > 0 and r["certified_finish_and_survival"]
                and r["net_value_low"] is not None and r["net_value_low"] > 0]
    positive.sort(key=lambda r: (r["net_value_low"], -r["delay_turns"]), reverse=True)
    rows.sort(key=lambda r: (r["net_value_high"], -r["delay_turns"]), reverse=True)
    return dict(enabled=True, supported=True, policy=asdict(policy), context=asdict(context), options=rows,
                recommendation="compare_verified_delay" if positive and baseline_finish else
                               "finish_now" if baseline_finish else "ordinary_combat_with_timing_reference",
                proposed_delay=positive[0]["delay_turns"] if positive and baseline_finish else None,
                reference_only=True, execution_authorized=False,
                limitations=["heuristic weights are not calibrated win probability or exact HP exchange rates",
                             "future hand/draw context is supplied or explicitly assumed, not hidden future state",
                             "current-combat trigger bonuses shown but not valued without continuation simulation",
                             "waiting changes enemy behavior, deck and buffs; reobserve before predicting a kill",
                             "HP/resource costs are total until combat completion, compared against delay=0; visible attacks alone cannot certify it",
                             "combined extra draws share hand capacity; no fixed target counter is optimized"])
