"""Versioned immutable types shared by independently replaceable modules."""
from dataclasses import dataclass
from typing import Protocol

CONTRACT_VERSION = "1.5.0"


@dataclass(frozen=True)
class Card:
    instance_id: str
    entity_id: str
    cost: int
    target_type: str
    description: str
    upgrade_level: int = 0
    card_type: str | None = None
    canonical: bool = False
    star_cost: int = -1


@dataclass(frozen=True)
class Enemy:
    instance_id: str
    entity_id: str
    hp: int
    block: int
    vulnerable: int
    damage_cap: int | None
    attacks: tuple[int, ...]
    powers: tuple[str, ...] = ()
    uncertain_intents: tuple[str, ...] = ()
    minion: bool = False
    strength: int = 0
    weak: bool = False


@dataclass(frozen=True)
class Potion:
    instance_id: str
    entity_id: str
    description: str
    action_id: str
    targets: tuple[str, ...]


@dataclass(frozen=True)
class PeriodicRelic:
    entity_id: str
    counter: int | None
    period: int
    draw_bonus: int = 0
    energy_bonus: int = 0

    def __post_init__(self):
        if type(self.period) is not int or self.period < 1:
            raise ValueError("Positive integer relic period required")
        if self.counter is not None and (type(self.counter) is not int or not 0 <= self.counter < self.period):
            raise ValueError("Invalid stable relic counter")
        if any(type(v) is not int or v < 0 for v in (self.draw_bonus, self.energy_bonus)):
            raise ValueError("Invalid relic bonus")


@dataclass(frozen=True)
class CombatState:
    hp: int
    max_hp: int
    block: int
    energy: int
    hand: tuple[Card, ...]
    enemies: tuple[Enemy, ...]
    draw_composition: tuple[tuple[str, int], ...]
    potions: tuple[Potion, ...] = ()
    strength_delta: int = 0
    temporary_strength: int = 0
    legion_ready: bool = False
    colossus: bool = False
    warnings: tuple[str, ...] = ()
    hand_limit: int = 10
    effect_draw_blocked: bool = False
    discard_composition: tuple[tuple[str, int], ...] = ()
    discard_known: bool = False
    exhausted_composition: tuple[tuple[str, int], ...] = ()
    periodic_relics: tuple[PeriodicRelic, ...] = ()
    turn: int | None = None
    hp_loss_reduction: int = 0
    turn_hp_loss_cap: int | None = None
    player_weak: bool = False
    permafrost: bool = False
    base_strength: int = 0
    player_frail: bool = False
    combat_won: bool = False
    weak_turns: int = 0
    frail_turns: int = 0
    stars: int = 0


@dataclass(frozen=True)
class Operation:
    action_id: str
    target_id: str | None = None


@dataclass(frozen=True)
class Transition:
    state: CombatState
    boundary: str | None = None
    draw_count: int = 0
    requested_draw: int = 0
    draw_reason: str | None = None
    resume_effects: tuple[tuple[str, int, int], ...] = ()
    pending_card: Card | None = None
    pending_exhaust: bool = False
    effect_target_ids: tuple[str, ...] = ()


class UnsupportedEffect(ValueError):
    """Never silently substitute zero for an unknown effect."""


class Simulator(Protocol):
    version: str
    def actions(self, state: CombatState) -> tuple[Operation, ...]: ...
    def apply(self, state: CombatState, action: Operation) -> Transition: ...


class Evaluator(Protocol):
    version: str
    def evaluate(self, initial: CombatState, result: Transition, steps: tuple[Operation, ...]) -> dict: ...
    def sort_key(self, metrics: dict) -> tuple: ...


class KnowledgeProvider(Protocol):
    version: str
    def references(self, entity_ids: tuple[str, ...]) -> tuple[dict, ...]: ...
