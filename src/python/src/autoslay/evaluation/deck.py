"""Structural metrics only. No claim of a complete deck-strength model."""
from collections import Counter
from autoslay.probability.draw import at_least_one

VERSION = "0.1.0"


def compare_deck(deck: list[dict], offered: dict | None, *, target_entities: tuple[str, ...] = (),
                 opening_draw: int = 5, remove_instance: str | None = None) -> dict:
    before = list(deck)
    after = list(deck)
    if offered is not None and remove_instance is not None:
        raise ValueError("Compare adding or removing in separate alternatives")
    if remove_instance is not None:
        found = [c for c in after if c.get("instance_id") == remove_instance]
        if len(found) != 1:
            raise ValueError("Removal must match exactly one actual deck instance")
        after = [c for c in after if c.get("instance_id") != remove_instance]
    if offered is not None:
        after.append(offered)

    def metrics(cards):
        counts = Counter(c["type"] for c in cards)
        costs = [c["energy_cost"] for c in cards if c.get("energy_cost", -1) >= 0 and not c.get("costs_x")]
        n = len(cards)
        targets = sum(c["entity_id"] in target_entities for c in cards)
        probability = at_least_one(n, targets, min(opening_draw, n))
        return dict(size=n, types=dict(counts), mean_fixed_cost=sum(costs) / len(costs) if costs else None,
                    target_copies=targets, opening_target_probability=float(probability))
    if opening_draw < 0:
        raise ValueError("opening_draw must be nonnegative")
    return dict(before=metrics(before), after=metrics(after),
                assumptions=["uniform initial shuffle", "no innate/bottled/start-of-combat draw modifiers"],
                limitations=["structural comparison only; no damage curve, synergy, upgrade or route valuation yet"])
