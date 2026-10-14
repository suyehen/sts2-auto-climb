from dataclasses import asdict, dataclass, field
from math import isfinite
from autoslay.decision.contracts import CombatState, Operation, Transition

VERSION = "0.4.0"


@dataclass(frozen=True)
class CombatPolicy:
    version: str = "1.0.0"
    damage_weight: float = 1.0
    block_weight: float = 1.5
    permanent_strength_weight: float = 3.0
    draw_option_weight: float = 0.0
    short_term: dict = field(default_factory=dict)
    potion_reserve: dict = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("Policy version required")
        for key, value in asdict(self).items():
            if key not in {'version','short_term','potion_reserve'} and (type(value) not in {int, float} or not isfinite(value) or value < 0):
                raise ValueError("Weights must be finite nonnegative numbers")
        from .combat_value import ValuePolicy
        ValuePolicy(**self.short_term)
        for cost in self.potion_reserve.get('costs',{}).values():
            if not isinstance(cost,dict) or set(cost)!={'lower','upper'} or any(
                type(v) not in (int,float) or not isfinite(v) or v<0 for v in cost.values()) or cost['lower']>cost['upper']:
                raise ValueError('Invalid potion reserve interval')
        discount=self.potion_reserve.get('substitute_discount',.75)
        if type(discount) not in (int,float) or not 0<discount<=1:raise ValueError('Invalid potion substitute discount')


class CombatEvaluator:
    version = VERSION

    def __init__(self, policy: CombatPolicy | None = None):
        self.policy = policy or CombatPolicy()

    def evaluate(self, initial: CombatState, result: Transition, steps: tuple[Operation, ...]) -> dict:
        s = result.state
        alive = [] if s.combat_won else [e for e in s.enemies if e.hp > 0]
        incoming = sum((d // 2 if s.colossus and e.vulnerable else d) for e in alive for d in e.attacks)
        initial_incoming = sum(d for e in initial.enemies for d in e.attacks if e.hp > 0)
        remaining_block, attack_hp_loss = s.block, 0
        for enemy in alive:
            for hit in enemy.attacks:
                hit = hit // 2 if s.colossus and enemy.vulnerable else hit
                absorbed = min(remaining_block, hit)
                remaining_block -= absorbed
                attack_hp_loss += max(0, hit - absorbed - s.hp_loss_reduction)
        if s.turn_hp_loss_cap is not None:
            attack_hp_loss = min(attack_hp_loss, s.turn_hp_loss_cap)
        hp_loss = initial.hp - s.hp + attack_hp_loss
        blocked = min(s.block, incoming)
        damage = sum(e.hp for e in initial.enemies) - sum(e.hp for e in s.enemies)
        future_strength = s.strength_delta - s.temporary_strength
        score = (damage * self.policy.damage_weight + blocked * self.policy.block_weight
                 + future_strength * self.policy.permanent_strength_weight
                 + result.draw_count * self.policy.draw_option_weight)
        uncertainty = list(s.warnings)
        uncertainty.extend("ENEMY_INTENT:" + e.instance_id + ":" + kind for e in alive for kind in e.uncertain_intents)
        if result.boundary:
            uncertainty.append(result.boundary)
        if s.turn_hp_loss_cap is not None and alive:
            uncertainty.append("TURN_HP_CAP_REMAINING_UNKNOWN; projected loss is conservative upper bound")
        return dict(all_enemies_killed=not alive, projected_death=s.hp <= 0 or hp_loss >= initial.hp,
                    projected_hp_loss=hp_loss, forecast_kind="visible_current_intents_only",
                    self_hp_loss=initial.hp-s.hp,projected_attack_hp_loss=attack_hp_loss,
                    block_after_plays=s.block,incoming_after_kills=incoming,initial_visible_attack=initial_incoming,
                    remaining_primary_enemy_hp=sum(e.hp for e in alive if not e.minion),remaining_stars=s.stars,
                    projected_hp_loss_is_upper_bound=s.turn_hp_loss_cap is not None,
                    effective_hp_damage=damage, effective_block=blocked,
                    prevented_attack_damage=max(0, initial_incoming - incoming),
                    permanent_strength_gain=future_strength, remaining_energy=s.energy,
                    draw_count=result.draw_count, heuristic_score=score,
                    uncertainty=sorted(set(uncertainty)), confidence="partial" if uncertainty else "modeled_current_turn")

    @staticmethod
    def sort_key(metrics: dict):
        # Unobserved draw outcomes cannot prove safety or lethal and are not compared as finished turns.
        return (not metrics["projected_death"], metrics["all_enemies_killed"],
                -metrics["projected_hp_loss"], metrics["heuristic_score"])
