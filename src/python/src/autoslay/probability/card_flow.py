"""Marginal draw odds with capacity, prevention and one ordinary discard reshuffle."""
from autoslay.probability.draw import at_least_one

VERSION = "1.0.0"


def draw_forecast(candidate: dict) -> dict:
    composition = candidate["draw_composition"]
    discard = candidate["discard_composition"]
    size, discarded = sum(composition.values()), sum(discard.values())
    count = candidate["draw_count"]
    if count > size and not candidate["discard_known"]:
        return dict(supported=False, reason="discard unknown; reshuffle cannot be calculated")
    if count > size + discarded:
        return dict(supported=False, reason="draw exceeds observed piles")
    probabilities = {}
    for entity in sorted(composition.keys() | discard.keys()):
        if count <= size:
            probability = at_least_one(size, composition.get(entity, 0), count)
        elif composition.get(entity, 0):
            probability = 1
        else:
            probability = at_least_one(discarded, discard.get(entity, 0), count - size)
        probabilities[entity] = float(probability)
    return dict(supported=True, requested_draw=candidate["requested_draw"], actual_draw=count,
                capacity_reason=candidate["draw_reason"], draw_pile_size=size, discard_size=discarded,
                reshuffle_required=count > size, at_least_one_by_entity=probabilities,
                assumption="uniform unknown order/shuffle; no card-draw hooks, forced draw positions or shuffle modifiers",
                continuation_value=None, reason="observe actual cards before continuing; no invented identities")
