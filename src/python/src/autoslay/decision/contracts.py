"""Versioned immutable types shared by independently replaceable modules."""
from dataclasses import dataclass
from typing import Protocol

CONTRACT_VERSION = "1.12.0"


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
    costs_x: bool = False
    costs_star_x: bool = False
    variant_key: str = ''
    enchantment_id: str | None = None
    enchantment_amount: int | None = None
    affliction_id: str | None = None
    dynamic_variables: tuple[tuple[str, int | float], ...] = ()
    keywords: tuple[str, ...] | None = None


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
    max_hp: int = 0
    weak_turns: int = 0
    move_state: str | None = None
    move_history: tuple[str, ...] = ()
    history_complete: bool = False
    runtime_fields: tuple[tuple[str, int | bool | str], ...] = ()
    poison: int = 0
    power_stacks: tuple[tuple[str, int], ...] = ()
    temporary_strength_loss: int = 0


@dataclass(frozen=True)
class Potion:
    instance_id: str
    entity_id: str
    description: str
    action_id: str
    targets: tuple[str, ...]
    allows_no_target: bool = False
    dynamic_variables: tuple[tuple[str, int | float], ...] = ()


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
class MechanismImpact:
    entity_id: str
    hook: str
    treatment: str
    reads: tuple[str, ...] = ('*',)
    writes: tuple[str, ...] = ('*',)
    phase: str = 'combat'
    trigger: str = 'unknown'
    source_fingerprint: str | None = None
    warning: str | None = None


@dataclass(frozen=True)
class ScheduledRelic:
    entity_id: str
    phase: str
    turn: int | None
    operation: str
    amount: int | None
    last_turn: int = 0
    repeats: int = 1
    binding: tuple[int,int] | None = None
    variables: tuple[tuple[str,int], ...] = ()

    def __post_init__(self):
        if self.phase not in {'after_block_cleared','side_turn_start','after_hand_draw','side_turn_end','after_energy_reset_late'}:
            raise ValueError('Unsupported scheduled phase')
        from autoslay.rules.registry import PHASE_OPERATIONS
        if self.operation not in PHASE_OPERATIONS:
            raise ValueError('Unsupported scheduled effect')
        if type(self.repeats) is not int or not 1<=self.repeats<=100 or self.operation not in {'unpowered_damage','summon'} and self.repeats!=1:
            raise ValueError('Invalid scheduled repeat count')
        if self.turn is not None and (type(self.turn) is not int or self.turn<1):raise ValueError('Invalid scheduled turn')
        if (self.amount is None and self.binding is None or self.amount is not None and (type(self.amount) is not int or self.amount<0)
                or type(self.last_turn) is not int or self.last_turn<0):
            raise ValueError('Invalid scheduled resource')


@dataclass(frozen=True)
class TriggeredRelic:
    entity_id: str
    card_type: str
    period: int
    counter: int
    reset_each_turn: bool
    last_reset_turn: int
    effects: tuple[tuple[str,int,int], ...]
    remaining_triggers: int | None = None

    def __post_init__(self):
        if self.card_type not in {'Attack','Skill','Power'}:raise ValueError('Invalid trigger card type')
        if type(self.period) is not int or not 1<=self.period<=1000:raise ValueError('Invalid trigger period')
        if type(self.counter) is not int or not 0<=self.counter<self.period:raise ValueError('Stable observed relic counter required')
        if type(self.reset_each_turn) is not bool or type(self.last_reset_turn) is not int or self.last_reset_turn<0:
            raise ValueError('Invalid counter reset state')
        if self.remaining_triggers is not None and (type(self.remaining_triggers) is not int or self.remaining_triggers<0):
            raise ValueError('Invalid remaining trigger count')


@dataclass(frozen=True)
class OstyState:
    hp: int
    max_hp: int


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
    base_strength: int = 0
    player_frail: bool = False
    combat_won: bool = False
    weak_turns: int = 0
    frail_turns: int = 0
    stars: int = 0
    enemy_hp_loss: int = 0
    self_hp_loss: int = 0
    healing: int = 0
    mechanism_scope: tuple[MechanismImpact, ...] = ()
    base_dexterity: int = 0
    dexterity_delta: int = 0
    temporary_dexterity: int = 0
    temporary_draw_blocked: bool = False
    permanent_draw_blocked: bool = False
    scheduled_relics: tuple[ScheduledRelic, ...] = ()
    removed_composition: tuple[tuple[str, int], ...] = ()
    regeneration: int = 0
    plating: int = 0
    status_phase_marks: tuple[tuple[str, int], ...] = ()
    triggered_relics: tuple[TriggeredRelic, ...] = ()
    next_turn_block: int = 0
    next_turn_energy: int = 0
    next_turn_stars: int = 0
    next_turn_draw: int = 0
    ritual: int = 0
    generated_templates: tuple[Card, ...] = ()
    generation_counter: int = 0
    power_stacks: tuple[tuple[str, int], ...] = ()
    cards_played: int = 0
    attacks_played: int = 0
    skills_played: int = 0
    cards_exhausted: int = 0
    turn_cards_exhausted: int = 0
    turn_cards_drawn: int = 0
    x_value: int = 0
    star_x_value: int = 0
    last_discard_count: int = 0
    last_exhaust_count: int = 0
    weak_skip_next_tick: bool = False
    frail_skip_next_tick: bool = False
    combat_cards_played: int = 0
    history_known: bool = True
    retain_hand_relic: bool = False
    retain_energy: bool = False
    extra_hand_draw: int = 0
    discard_block: int = 0
    exhaust_damage: int = 0
    shuffle_block: int = 0
    osty: OstyState | None = None
    pets_known: bool = True
    osty_attack_block: int = 0


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
    resume_effects: tuple[tuple, ...] = ()
    pending_card: Card | None = None
    pending_exhaust: bool = False
    effect_target_ids: tuple[str, ...] = ()
    play_power_snapshot: tuple[tuple[str,int], ...] | None = None


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
