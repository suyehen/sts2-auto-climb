"""Persistent player-turn counters defined in the native mechanic registry."""
from dataclasses import replace
from autoslay.decision.contracts import PeriodicRelic
from autoslay.rules.registry import ENTITY_MECHANICS

VERSION = '2.0.0'
GAME_VERSION = "0.111.0"
# Definitions are read at the decision's revision; no import-time entity cache.


def read_periodic(relic: dict) -> PeriodicRelic | None:
    identity=relic['entity_id']; entry=ENTITY_MECHANICS.get(identity)
    if entry is None or entry.periodic is None:
        return None
    period,draw,energy=entry.periodic
    counter = relic.get("counter")
    if type(counter) is not int or not 0 <= counter < period:
        counter = None
    return PeriodicRelic(relic["entity_id"], counter, period, draw, energy)


def advance(relics: tuple[PeriodicRelic, ...], turns: int = 1) -> tuple[tuple[PeriodicRelic, ...], int, int]:
    if type(turns) is not int or turns < 0:
        raise ValueError("Nonnegative player-turn starts required")
    if any(r.counter is None for r in relics):
        raise ValueError("Unknown/transient relic counter; observe again")
    output, draws, energy = [], 0, 0
    for relic in relics:
        triggers, counter = divmod(relic.counter + turns, relic.period)
        output.append(replace(relic, counter=counter))
        draws += triggers * relic.draw_bonus
        energy += triggers * relic.energy_bonus
    return tuple(output), draws, energy
