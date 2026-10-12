"""Unordered drawing without replacement; uniform-shuffle assumption is explicit."""
from dataclasses import dataclass
from fractions import Fraction
from math import comb
from random import Random
from collections import Counter

VERSION = "1.0.0"


def at_least_one(total: int, targets: int, draws: int) -> Fraction:
    if any(type(n) is not int for n in (total, targets, draws)):
        raise ValueError("Integer counts required")
    if not 0 <= targets <= total or not 0 <= draws <= total:
        raise ValueError("Require 0 <= targets, draws <= total")
    misses = comb(total - targets, draws) if draws <= total - targets else 0
    return 1 - Fraction(misses, comb(total, draws))


@dataclass(frozen=True)
class DrawOutcome:
    cards: tuple[tuple[str, int], ...]
    probability: Fraction


@dataclass(frozen=True)
class DrawDistribution:
    outcomes: tuple[DrawOutcome, ...]
    method: str
    samples: int | None
    seed: int | None
    assumption: str = "uniform unordered draw without replacement; no reshuffle"


def distribution(composition: dict[str, int], draws: int, *, exact_limit: int = 5000,
                 samples: int = 2000, seed: int = 0) -> DrawDistribution:
    if any(type(n) is not int or n < 0 for n in composition.values()):
        raise ValueError("Counts must be nonnegative integers")
    items = tuple(sorted((k, n) for k, n in composition.items() if n))
    total = sum(n for _, n in items)
    if type(draws) is not int or not 0 <= draws <= total:
        raise ValueError("Drawing beyond the pile needs a reshuffle model")
    if exact_limit < 1 or samples < 1:
        raise ValueError("Positive budgets required")
    outcomes = []
    denominator = comb(total, draws)

    def visit(index, remaining, chosen, weight):
        if len(outcomes) > exact_limit:
            return
        if index == len(items):
            if remaining == 0:
                outcomes.append(DrawOutcome(tuple(chosen), Fraction(weight, denominator)))
            return
        entity, count = items[index]
        rest = sum(n for _, n in items[index + 1:])
        for n in range(max(0, remaining - rest), min(count, remaining) + 1):
            visit(index + 1, remaining - n, chosen + ([(entity, n)] if n else []), weight * comb(count, n))

    # Avoid very deep recursion and huge exact distributions.
    if len(items) <= 80:
        visit(0, draws, [], 1)
        if len(outcomes) <= exact_limit:
            return DrawDistribution(tuple(outcomes), "exact", None, None)
    rng = Random(seed)
    pool = [entity for entity, n in items for _ in range(n)]
    counts = Counter(tuple(sorted(Counter(rng.sample(pool, draws)).items())) for _ in range(samples))
    return DrawDistribution(tuple(DrawOutcome(cards, Fraction(n, samples)) for cards, n in sorted(counts.items())),
                            "sampled", samples, seed)
