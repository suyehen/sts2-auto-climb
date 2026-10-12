from dataclasses import replace
from autoslay.decision.contracts import CombatState, Operation, Transition, UnsupportedEffect
from autoslay.rules.registry import IroncladRules, Effect
from autoslay.simulation.card_flow import plan_draw, add_to_composition

VERSION = "0.6.0"


class CombatSimulator:
    version = VERSION

    def __init__(self, initial: CombatState, rules=None):
        self.rules = rules or IroncladRules()
        self.initial = initial
        self.compiled = {}
        self.unsupported = {}
        for card in initial.hand:
            try:
                self.compiled[card.instance_id] = self.rules.card(card, initial)
            except UnsupportedEffect as exc:
                self.unsupported[card.instance_id] = str(exc)
        self.compiled_potions = {}
        for potion in initial.potions:
            try:
                self.compiled_potions[potion.action_id] = self.rules.potion(potion)
            except (UnsupportedEffect, AttributeError):
                pass
        self.unsupported_potions = tuple(p.entity_id for p in initial.potions if p.action_id not in self.compiled_potions)

    def actions(self, state: CombatState) -> tuple[Operation, ...]:
        if state.combat_won or state.hp <= 0:
            return ()
        actions = []
        for card in state.hand:
            if card.instance_id not in self.compiled or not 0 <= card.cost <= state.energy or max(0,card.star_cost)>state.stars:
                continue
            aid = "play_" + card.instance_id
            if card.target_type == "AnyEnemy":
                actions.extend(Operation(aid, e.instance_id) for e in state.enemies if e.hp > 0)
            elif card.target_type in {"Self", "AllEnemies", "None"}:
                actions.append(Operation(aid))
        if state.periodic_relics and all(r.counter is not None for r in state.periodic_relics) and any(e.hp > 0 for e in state.enemies):
            actions.append(Operation("end_turn"))
        for potion in state.potions:
            if potion.action_id in self.compiled_potions:
                rule=self.compiled_potions[potion.action_id]
                alive_ids={e.instance_id for e in state.enemies if e.hp>0}
                actions.extend(Operation(potion.action_id, target) for target in potion.targets
                               if rule.target_type!='AnyEnemy' or target in alive_ids)
                if not potion.targets:
                    actions.append(Operation(potion.action_id))
        return tuple(actions)

    def apply(self, state: CombatState, action: Operation) -> Transition:
        if action not in self.actions(state):
            raise UnsupportedEffect("Operation is not modeled or not legal")
        if state.warnings:
            raise UnsupportedEffect("Unmodeled combat interaction: " + "; ".join(state.warnings))
        if action.action_id == "end_turn":
            # Forecast current visible attacks separately; never invent next turn's hand/intents.
            return Transition(state, "next_turn_requires_observation")
        potion_rule = self.compiled_potions.get(action.action_id)
        card = None if potion_rule else next(c for c in state.hand if "play_" + c.instance_id == action.action_id)
        rule = potion_rule or self.compiled[card.instance_id]
        target_type = potion_rule.target_type if potion_rule else card.target_type
        if target_type == "AnyEnemy":
            targets = {action.target_id}
        elif target_type == 'AllEnemies' or not potion_rule:
            targets = {e.instance_id for e in state.enemies if e.hp > 0}
        else:
            targets = set()
        if potion_rule:
            s = replace(state, potions=tuple(p for p in state.potions if p.action_id != action.action_id))
        else:
            s = replace(state, energy=state.energy - card.cost,
                    stars=state.stars-max(0,card.star_cost),
                    hand=tuple(c for c in state.hand if c.instance_id != card.instance_id))
        return self._resolve(s,rule.effects,targets,card,False if potion_rule else rule.exhaust)

    def resume_draw(self, transition: Transition, drawn_state: CombatState) -> Transition:
        """Resume a modeled public-composition branch, never issue a game action."""
        if transition.boundary!='draw_requires_observation':
            raise UnsupportedEffect('Only a draw branch may be resumed')
        return self._resolve(drawn_state,tuple(Effect(*effect) for effect in transition.resume_effects),
                             set(transition.effect_target_ids),transition.pending_card,transition.pending_exhaust)

    def _resolve(self,s,effects,targets,card,exhaust):
        draw_plan = None
        for index,effect in enumerate(effects):
            if effect.kind in {"damage", "dismantle", "unpowered_damage"}:
                enemies = []
                for enemy in s.enemies:
                    e = enemy
                    if e.instance_id in targets and e.hp > 0:
                        hits = 2 if effect.kind == "dismantle" and e.vulnerable > 0 else effect.repeats
                        for _ in range(hits):
                            if e.hp <= 0:
                                break
                            damage = max(0, effect.amount + (0 if effect.kind == 'unpowered_damage' else s.strength_delta))
                            if e.vulnerable > 0 and effect.kind != 'unpowered_damage':
                                damage = damage * 3 // 2
                            if e.damage_cap is not None:
                                damage = min(damage, e.damage_cap)
                            absorbed = min(e.block, damage)
                            e = replace(e, block=e.block - absorbed, hp=max(0, e.hp - (damage - absorbed)))
                    enemies.append(e)
                s = replace(s, enemies=tuple(enemies))
            elif effect.kind == "block":
                value = effect.amount * (2 if s.legion_ready else 1)
                s = replace(s, block=s.block + value, legion_ready=False)
            elif effect.kind == 'unpowered_block':
                s = replace(s, block=s.block + effect.amount)
            elif effect.kind == 'multiply_block':
                s = replace(s, block=s.block * effect.amount)
            elif effect.kind in {"vulnerable", "double_vulnerable", "enemy_strength"}:
                enemies = []
                for e in s.enemies:
                    if e.instance_id in targets and e.hp > 0:
                        if effect.kind == "vulnerable":
                            e = replace(e, vulnerable=e.vulnerable + effect.amount)
                        elif effect.kind == "double_vulnerable":
                            e = replace(e, vulnerable=e.vulnerable * 2)
                        else:
                            if e.weak:
                                return Transition(s, "enemy_weak_strength_rounding_requires_observation")
                            e = replace(e, attacks=tuple(d + effect.amount for d in e.attacks), strength=e.strength + effect.amount)
                    enemies.append(e)
                s = replace(s, enemies=tuple(enemies))
            elif effect.kind in {"strength", "temporary_strength"}:
                s = replace(s, strength_delta=s.strength_delta + effect.amount,
                            temporary_strength=s.temporary_strength + (effect.amount if effect.kind == "temporary_strength" else 0))
                if s.player_weak:
                    return Transition(s, "weak_strength_rounding_requires_observation")
            elif effect.kind == "lose_hp":
                if s.turn_hp_loss_cap is not None:
                    return Transition(s, "remaining_turn_hp_cap_requires_observation")
                s = replace(s, hp=max(0, s.hp - max(0, effect.amount - s.hp_loss_reduction)))
            elif effect.kind == "energy":
                s = replace(s, energy=s.energy + effect.amount)
            elif effect.kind == "exhaust_nonattacks":
                if s.legion_ready or any(c.card_type is None for c in s.hand):
                    return Transition(s, "exhaust_nonattacks_requires_observation")
                exhausted = tuple(c for c in s.hand if c.card_type != "Attack")
                if any(c.instance_id not in self.compiled and c.entity_id != "CARD.ASCENDERS_BANE" for c in exhausted):
                    return Transition(s, "unknown_exhaust_hooks_requires_observation")
                s = replace(s, hand=tuple(c for c in s.hand if c.card_type == "Attack"),
                    exhausted_composition=add_to_composition(s.exhausted_composition, [c.entity_id for c in exhausted]),
                    block=s.block + effect.amount * len(exhausted))
            elif effect.kind.endswith("_boundary"):
                return Transition(s, effect.kind + "_requires_observation")
            elif effect.kind == "colossus":
                s = replace(s, colossus=True)
            elif effect.kind == "draw":
                draw_plan = plan_draw(effect.amount, len(s.hand), sum(n for _, n in s.draw_composition),
                                      sum(n for _, n in s.discard_composition), hand_limit=s.hand_limit,
                                      blocked=s.effect_draw_blocked, discard_known=s.discard_known)
                if draw_plan.actual or not draw_plan.supported:
                    # Played card remains in the play pile during its draw; do not reshuffle it.
                    return Transition(s, "draw_requires_observation", draw_plan.actual,
                                      effect.amount, draw_plan.reason,
                                      tuple((e.kind,e.amount,e.repeats) for e in effects[index+1:]),
                                      card,exhaust,tuple(sorted(targets)))
            else:
                raise UnsupportedEffect("Unknown effect: " + effect.kind)
            if any(not e.minion for e in s.enemies) and all(e.hp <= 0 for e in s.enemies if not e.minion):
                # Secondary enemies stop combat; their remaining HP is not damage dealt.
                s = replace(s, combat_won=True)
            if s.hp <= 0 or s.combat_won or all(e.hp <= 0 for e in s.enemies):
                break
        if card is not None:
            destination = "exhausted_composition" if exhaust else "discard_composition"
            s = replace(s, **{destination: add_to_composition(getattr(s, destination), [card.entity_id])})
        return Transition(s, requested_draw=draw_plan.requested if draw_plan else 0,
                          draw_reason=draw_plan.reason if draw_plan else None)
