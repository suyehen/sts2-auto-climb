"""Shared hand/pile rules; no hidden order, draw hooks or generated identities inferred."""
from collections import Counter
from dataclasses import dataclass, replace
from autoslay.decision.contracts import Card, CombatState

VERSION = "1.0.0"
GAME_VERSION = "0.111.0"
HAND_LIMIT = 10


def validate_count(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(name + " must be a nonnegative integer")


@dataclass(frozen=True)
class DrawPlan:
    requested: int
    actual: int
    from_draw: int
    from_discard: int
    reason: str
    supported: bool = True


def plan_draw(requested: int, hand_size: int, draw_size: int, discard_size: int,
              *, hand_limit: int = HAND_LIMIT, blocked: bool = False,
              from_hand_draw: bool = False, discard_known: bool = True) -> DrawPlan:
    for name, value in (("requested", requested), ("hand_size", hand_size), ("draw_size", draw_size),
                        ("discard_size", discard_size), ("hand_limit", hand_limit)):
        validate_count(value, name)
    if hand_limit != HAND_LIMIT or hand_size > hand_limit:
        raise ValueError("Unsupported hand capacity/state")
    if blocked and not from_hand_draw:
        return DrawPlan(requested, 0, 0, 0, "effect_draw_prevented")
    capacity = hand_limit - hand_size
    desired = min(requested, capacity)
    if desired > draw_size and not discard_known:
        return DrawPlan(requested, desired, min(desired, draw_size), 0, "discard_unknown_reshuffle", False)
    actual = min(desired, draw_size + discard_size)
    from_draw = min(actual, draw_size)
    reason = ("hand_full" if capacity == 0 and requested else
              "no_cards" if actual == 0 and desired else
              "hand_capacity_limited" if requested > capacity else
              "piles_exhausted" if actual < desired else "draw_available")
    return DrawPlan(requested, actual, from_draw, actual - from_draw, reason)


def add_to_composition(composition: tuple[tuple[str, int], ...], entities) -> tuple[tuple[str, int], ...]:
    counts = Counter(dict(composition))
    counts.update(entities)
    return tuple(sorted((entity, count) for entity, count in counts.items() if count))


@dataclass(frozen=True)
class Placement:
    state: CombatState
    entered_hand: tuple[str, ...]
    overflow_to_discard: tuple[str, ...]


def add_generated(state: CombatState, cards: tuple[Card, ...], destination: str = "hand") -> Placement:
    """Known generated cards only. Unknown/random choices must be observed first.
    Handles destination and overflow, not arbitrary creation hooks or random pool selection.
    Draw prevention does not stop adding a generated card.
    """
    if destination not in {"hand", "discard", "draw", "exhaust"}:
        raise ValueError("Unsupported generated-card destination")
    if state.hand_limit != HAND_LIMIT or len(state.hand) > HAND_LIMIT:
        raise ValueError("Unsupported hand state")
    identities = [c.instance_id for c in cards]
    if len(set(identities)) != len(identities) or set(identities) & {c.instance_id for c in state.hand}:
        raise ValueError("Generated cards require distinct new instance IDs")
    hand_cards = cards[:max(0, state.hand_limit - len(state.hand))] if destination == "hand" else ()
    discarded = cards[len(hand_cards):] if destination == "hand" else cards if destination == "discard" else ()
    s = replace(state, hand=state.hand + hand_cards,
                discard_composition=add_to_composition(state.discard_composition, (c.entity_id for c in discarded)))
    if destination == "draw":
        s = replace(s, draw_composition=add_to_composition(s.draw_composition, (c.entity_id for c in cards)))
    elif destination == "exhaust":
        s = replace(s, exhausted_composition=add_to_composition(s.exhausted_composition, (c.entity_id for c in cards)))
    return Placement(s, tuple(c.instance_id for c in hand_cards),
                     tuple(c.instance_id for c in discarded) if destination == "hand" else ())
