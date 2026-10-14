"""Independently versioned, source-checked persistent player-turn relic counters."""
from dataclasses import replace
from autoslay.knowledge_base.repository import read_json, dataset_path
from autoslay.decision.contracts import PeriodicRelic
from autoslay.knowledge_base.rulebook import Rulebook, RULEBOOK_PATH, validate_rulebook

VERSION = "1.0.0"
GAME_VERSION = "0.111.0"
# Stable DisplayAmount equals TurnsSeen. Animation-only threshold display is rejected.
def declared_rules():
    # Importing services must work before the user's first encyclopedia export.
    # This is only the declared ID/shape lookup; read_periodic still checks the
    # current catalog fingerprint before using any rule for calculation.
    data=read_json(dataset_path('rules'))
    validate_rulebook(data)
    return {identity: (rule['periodic']['period'],rule['periodic']['draw'],rule['periodic']['energy'])
            for identity,rule in data['entries'].items() if rule.get('periodic')}


RULES = declared_rules()


def read_periodic(relic: dict) -> PeriodicRelic | None:
    book=Rulebook(); identity=relic['entity_id']; entry=book.data['entries'].get(identity,{})
    if not entry.get('periodic') or book.status(identity)!='compatible':
        return None
    value=entry['periodic']; period,draw,energy=value['period'],value['draw'],value['energy']
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
