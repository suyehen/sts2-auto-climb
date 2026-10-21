from dataclasses import replace
from autoslay.decision.contracts import CombatState, Operation, Transition, UnsupportedEffect
from autoslay.rules.registry import KnowledgeRules, Effect
from autoslay.simulation.card_flow import plan_draw, add_to_composition
from autoslay.rules.registry import ATOMIC_OPERATIONS
from autoslay.simulation.atomic_effects import apply_atomic

VERSION = "0.11.0"


class CombatSimulator:
    version = VERSION

    def __init__(self, initial: CombatState, rules=None):
        self.rules = rules or KnowledgeRules()
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
            if card.instance_id not in self.compiled or not self.compiled[card.instance_id].playable or (not card.costs_x and not 0 <= card.cost <= state.energy) or (not card.costs_star_x and max(0,card.star_cost)>state.stars):
                continue
            aid = "play_" + card.instance_id
            target_type=self.target_type(card,state)
            if target_type == "AnyEnemy":
                actions.extend(Operation(aid, e.instance_id) for e in state.enemies if e.hp > 0)
            elif target_type in {"Self", "AllEnemies", "None", "AllAllies"}:
                actions.append(Operation(aid))
        if (state.periodic_relics or state.scheduled_relics or state.triggered_relics or state.regeneration or state.plating or state.ritual or state.next_turn_block or state.next_turn_energy or state.next_turn_stars or state.next_turn_draw or any(e.poison for e in state.enemies)) and all(r.counter is not None for r in state.periodic_relics) and any(e.hp > 0 for e in state.enemies):
            actions.append(Operation("end_turn"))
        for potion in state.potions:
            if potion.action_id in self.compiled_potions:
                rule=self.compiled_potions[potion.action_id]
                alive_ids={e.instance_id for e in state.enemies if e.hp>0}
                actions.extend(Operation(potion.action_id, target) for target in potion.targets
                               if rule.target_type!='AnyEnemy' or target in alive_ids)
                if potion.allows_no_target or not potion.targets:
                    actions.append(Operation(potion.action_id))
        return tuple(actions)

    @staticmethod
    def target_type(card,state):
        from autoslay.rules.statuses import stack
        if card.entity_id=='CARD.SHIV' and stack(state,'fan_of_knives'):return 'AllEnemies'
        return card.target_type

    def apply(self, state: CombatState, action: Operation) -> Transition:
        if action not in self.actions(state):
            raise UnsupportedEffect("Operation is not modeled or not legal")
        from autoslay.rules.mechanism_scope import action_blockers
        blockers=action_blockers(state,action) if state.mechanism_scope else list(state.warnings)
        if blockers:
            raise UnsupportedEffect("Unmodeled combat interaction: " + "; ".join(blockers))
        if action.action_id == "end_turn":
            # Forecast current visible attacks separately; never invent next turn's hand/intents.
            from autoslay.rules.scheduled_relics import apply_phase
            state=apply_phase(state,'side_turn_end')
            return Transition(state, 'combat_victory' if state.combat_won else "next_turn_requires_observation")
        potion_rule = self.compiled_potions.get(action.action_id)
        card = None if potion_rule else next(c for c in state.hand if "play_" + c.instance_id == action.action_id)
        rule = potion_rule or self.compiled[card.instance_id]
        target_type = potion_rule.target_type if potion_rule else self.target_type(card,state)
        if target_type == "AnyEnemy":
            targets = {action.target_id}
        elif target_type == 'AllEnemies' or not potion_rule:
            targets = {e.instance_id for e in state.enemies if e.hp > 0}
        else:
            targets = set()
        if potion_rule:
            s = replace(state, potions=tuple(p for p in state.potions if p.action_id != action.action_id))
        else:
            from autoslay.rules.statuses import stack
            s = replace(state, energy=0 if card.costs_x else state.energy - card.cost,
                    stars=0 if card.costs_star_x else state.stars-max(0,card.star_cost),
                    x_value=state.energy if card.costs_x else 0,
                    star_x_value=state.stars if card.costs_star_x else 0,
                    hand=tuple(c for c in state.hand if c.instance_id != card.instance_id))
            s=replace(s,stars=s.stars+stack(state,'the_sealed_throne'))
            keywords=card.keywords
            if keywords is None and hasattr(self.rules,'references'):
                variants=self.rules.references[card.entity_id]['variants']
                variant=next(v for v in variants if v['upgrade_level']==card.upgrade_level)
                keywords=tuple(variant.get('keywords',[]))
            from autoslay.rules.statuses import before_card
            s=before_card(s,card,keywords)
            if card.canonical or rule.dynamic or rule.canonical_values:
                # Bind after paying and moving the played card out of the hand,
                # matching the state observed by OnPlay calculations.
                rule=self.rules.card(card,s)
            if rule.canonical_values and not card.canonical:card=replace(card,canonical=True)
        return self._resolve(s,rule.effects,targets,card,False if potion_rule else rule.exhaust)

    def resume_draw(self, transition: Transition, drawn_state: CombatState) -> Transition:
        """Resume a modeled public-composition branch, never issue a game action."""
        if transition.boundary!='draw_requires_observation':
            raise UnsupportedEffect('Only a draw branch may be resumed')
        from autoslay.simulation.card_flow import apply_drawn_hooks
        prior={c.instance_id for c in transition.state.hand}
        drawn_state=apply_drawn_hooks(drawn_state,[c for c in drawn_state.hand if c.instance_id not in prior],self.rules)
        return self._resolve(drawn_state,tuple(Effect(*effect) for effect in transition.resume_effects),
                             set(transition.effect_target_ids),transition.pending_card,transition.pending_exhaust,
                             transition.play_power_snapshot)

    def resume_generated(self,transition):
        if transition.boundary!='generated_cards_requires_observation':
            raise UnsupportedEffect('Only a generated-card boundary may be resumed')
        return self._resolve(transition.state,tuple(Effect(*e) for e in transition.resume_effects),
                             set(transition.effect_target_ids),transition.pending_card,transition.pending_exhaust,
                             transition.play_power_snapshot)

    @staticmethod
    def _resume_effects(effects):
        return tuple((e.kind,e.amount,e.repeats,e.generation) if e.generation is not None else (e.kind,e.amount,e.repeats) for e in effects)

    def _resolve(self,s,effects,targets,card,exhaust,prior_powers=None):
        from autoslay.rules.statuses import stack,set_stack,add_stack,prevent_debuff
        from autoslay.rules.registry import STACK_POWERS,TEMPORARY_ENEMY_STRENGTH,TEMPORARY_PLAYER_STATS
        from autoslay.simulation.atomic_effects import damage_enemy,lose_player_hp
        if prior_powers is None:prior_powers=s.power_stacks
        draw_plan = None
        for index,effect in enumerate(effects):
            if effect.kind in ATOMIC_OPERATIONS:
                s = apply_atomic(s,effect.kind,effect.amount,targets,effect.repeats)
            elif effect.kind in {"damage", "dismantle",'osty_damage'}:
                pet_attack=effect.kind=='osty_damage'
                if pet_attack and (s.osty is None or s.osty.hp<=0):continue
                if card and not card.canonical and (stack(s,'vigor') or stack(s,'accuracy') or stack(s,'cruelty') or stack(s,'gigantification')):
                    return Transition(s,'raw_attack_amount_requires_observation')
                if not pet_attack and s.player_weak and not (card and card.canonical) and any(e.hp>0 and e.instance_id in targets and e.vulnerable for e in s.enemies):
                    return Transition(s,'weak_vulnerable_rounding_requires_observation')
                vigor=0 if pet_attack else stack(s,'vigor')
                giant=stack(s,'gigantification') if card and card.card_type=='Attack' else 0
                hits=2 if effect.kind=='dismantle' and any(e.instance_id in targets and e.vulnerable>0 for e in s.enemies) else effect.repeats
                for _ in range(hits):
                    if s.hp<=0 or s.combat_won or pet_attack and s.osty.hp<=0:break
                    # AttackCommand iterates hits, then the current living target list.
                    for enemy in tuple(s.enemies):
                        e=enemy
                        if e.instance_id in targets and e.hp>0:
                            damage = effect.amount+(stack(s,'calcify') if pet_attack else s.strength_delta+vigor)
                            if card and card.entity_id=='CARD.SHIV':damage+=stack(s,'accuracy')
                            damage=max(0,damage)
                            numerator=damage;denominator=1
                            if not pet_attack and card and card.canonical and s.player_weak:numerator*=3;denominator*=4
                            if giant:numerator*=3
                            if e.vulnerable>0:
                                numerator*=150+stack(s,'cruelty');denominator*=100
                            damage=numerator//denominator
                            before_hp=e.hp
                            if stack(e,'thorns'):
                                from autoslay.simulation.atomic_effects import damage_player,damage_osty
                                s=damage_osty(s,stack(e,'thorns')) if pet_attack else damage_player(s,stack(e,'thorns'))
                            s=damage_enemy(s,e.instance_id,damage)
                            e=next(e for e in s.enemies if e.instance_id==enemy.instance_id)
                            if not pet_attack and e.hp>0 and e.hp<before_hp and stack(s,'envenom'):
                                e,blocked=prevent_debuff(e)
                                if not blocked:e=replace(e,poison=e.poison+stack(s,'envenom'))
                                s=replace(s,enemies=tuple(e if x.instance_id==e.instance_id else x for x in s.enemies))
                if vigor:s=set_stack(s,'vigor',max(0,stack(s,'vigor')-vigor))
                if giant:s=set_stack(s,'gigantification',giant-1)
                if pet_attack:s=apply_atomic(s,'unpowered_block',s.osty_attack_block)
            elif effect.kind in {'block','block_and_next_turn'}:
                if s.player_frail and s.dexterity_delta and not (card and card.canonical):
                    return Transition(s,'frail_dexterity_rounding_requires_observation')
                value=max(0,effect.amount+s.dexterity_delta)*(2 if s.legion_ready else 1)
                if card and card.canonical and s.player_frail:value=value*3//4
                s = replace(s, block=s.block + value, legion_ready=False)
                if effect.kind=='block_and_next_turn':s=replace(s,next_turn_block=s.next_turn_block+value)
            elif effect.kind == 'multiply_block':
                s = replace(s, block=s.block * effect.amount)
            elif effect.kind in {"vulnerable", "double_vulnerable", "enemy_strength"}:
                enemies = []
                for e in s.enemies:
                    if e.instance_id in targets and e.hp > 0:
                        if effect.kind == "vulnerable" and effect.amount>0:
                            e,blocked=prevent_debuff(e)
                            if not blocked:e = replace(e, vulnerable=e.vulnerable + effect.amount)
                        elif effect.kind == "double_vulnerable" and e.vulnerable>0:
                            e,blocked=prevent_debuff(e)
                            if not blocked:e = replace(e, vulnerable=e.vulnerable * 2)
                        elif effect.kind=='enemy_strength':
                            if e.weak:
                                return Transition(s, "enemy_weak_strength_rounding_requires_observation")
                            e = replace(e, attacks=tuple(d + effect.amount for d in e.attacks), strength=e.strength + effect.amount)
                    enemies.append(e)
                s = replace(s, enemies=tuple(enemies))
            elif effect.kind in {"strength", "temporary_strength"}:
                s = replace(s, strength_delta=s.strength_delta + effect.amount,
                            temporary_strength=s.temporary_strength + (effect.amount if effect.kind == "temporary_strength" else 0))
                if s.player_weak and not (card is not None and card.canonical and all(c.canonical for c in s.hand)
                    and all(e.kind not in {'damage','dismantle'} for e in effects[index+1:])):
                    return Transition(s, "weak_strength_rounding_requires_observation")
            elif effect.kind in {'dexterity','temporary_dexterity'}:
                s=replace(s,dexterity_delta=s.dexterity_delta+effect.amount,
                    temporary_dexterity=s.temporary_dexterity+(effect.amount if effect.kind=='temporary_dexterity' else 0))
            elif effect.kind=='weak':
                enemies=[]
                for e in s.enemies:
                    if e.instance_id in targets and e.hp>0 and effect.amount>0:
                        e,blocked=prevent_debuff(e)
                        if not blocked:e=replace(e,weak=True,weak_turns=e.weak_turns+effect.amount,
                            attacks=tuple(d*3//4 for d in e.attacks) if not e.weak else e.attacks)
                    enemies.append(e)
                s=replace(s,enemies=tuple(enemies))
            elif effect.kind=='poison':
                enemies=[]
                for e in s.enemies:
                    if e.instance_id in targets and e.hp>0 and effect.amount>0:
                        e,blocked=prevent_debuff(e)
                        if not blocked:e=replace(e,poison=e.poison+effect.amount)
                    enemies.append(e)
                s=replace(s,enemies=tuple(enemies))
            elif effect.kind in TEMPORARY_ENEMY_STRENGTH:
                enemies=[]
                for e in s.enemies:
                    if e.instance_id in targets and e.hp>0 and effect.amount>0:
                        e,blocked=prevent_debuff(e)
                        if not blocked:
                            if e.weak:return Transition(s,'enemy_weak_strength_rounding_requires_observation')
                            e=add_stack(e,effect.kind,effect.amount)
                            e=replace(e,strength=e.strength-effect.amount,
                                attacks=tuple(max(0,d-effect.amount) for d in e.attacks),
                                temporary_strength_loss=e.temporary_strength_loss+effect.amount)
                    enemies.append(e)
                s=replace(s,enemies=tuple(enemies))
            elif effect.kind in STACK_POWERS:
                if effect.kind in {'doom','demise'}:
                    enemies=[]
                    for e in s.enemies:
                        if e.instance_id in targets and e.hp>0 and effect.amount>0:
                            e,blocked=prevent_debuff(e)
                            if not blocked:e=add_stack(e,effect.kind,effect.amount)
                        enemies.append(e)
                    s=replace(s,enemies=tuple(enemies))
                else:
                    s=add_stack(s,effect.kind,effect.amount)
                    if effect.kind in TEMPORARY_PLAYER_STATS:
                        field=TEMPORARY_PLAYER_STATS[effect.kind]
                        s=replace(s,**{field+'_delta':getattr(s,field+'_delta')+effect.amount,
                            'temporary_'+field:getattr(s,'temporary_'+field)+effect.amount})
                        if field=='strength' and s.player_weak and not (
                            card is not None and card.canonical and all(c.canonical for c in s.hand)
                            and all(e.kind not in {'damage','dismantle'} for e in effects[index+1:])):
                            return Transition(s,'weak_strength_rounding_requires_observation')
            elif effect.kind=='enemy_hp_loss':
                for e in tuple(s.enemies):
                    if e.instance_id in targets:s=damage_enemy(s,e.instance_id,effect.amount,hp_loss=True)
            elif effect.kind=='resolve_doom':
                from autoslay.rules.statuses import resolve_doom
                s=resolve_doom(s)
            elif effect.kind=='shuffle_hand_and_discard':
                if not s.discard_known:return Transition(s,'discard_unknown_reshuffle')
                from collections import Counter
                counts=Counter(dict(s.draw_composition));counts.update(dict(s.discard_composition))
                counts.update(c.variant_key or c.entity_id for c in s.hand)
                s=replace(s,hand=(),draw_composition=tuple(sorted(counts.items())),discard_composition=())
                s=apply_atomic(s,'unpowered_block',s.shuffle_block)
            elif effect.kind in {'remove_enemy_block','remove_enemy_artifact'}:
                s=replace(s,enemies=tuple((replace(e,block=0) if effect.kind=='remove_enemy_block' else set_stack(e,'artifact',0))
                    if e.instance_id in targets and e.hp>0 else e for e in s.enemies))
            elif effect.kind in {'enemy_strength_loss','temporary_enemy_strength_loss'}:
                enemies=[]
                for e in s.enemies:
                    if e.instance_id in targets and e.hp>0:
                        e,blocked=prevent_debuff(e)
                        if not blocked:
                            # Recompute from the declared move base; rounding an observed
                            # Weak hit backwards is ambiguous and must be observed.
                            if e.weak:return Transition(s,'enemy_weak_strength_rounding_requires_observation')
                            e=replace(e,strength=e.strength-effect.amount,
                                attacks=tuple(max(0,d-effect.amount) for d in e.attacks),
                                temporary_strength_loss=e.temporary_strength_loss+(effect.amount if effect.kind=='temporary_enemy_strength_loss' else 0))
                    enemies.append(e)
                s=replace(s,enemies=tuple(enemies))
            elif effect.kind in {'regeneration','plating','next_turn_block','next_turn_energy','next_turn_stars','next_turn_draw','ritual'}:
                s=replace(s,**{effect.kind:getattr(s,effect.kind)+effect.amount})
            elif effect.kind=='generate_card':
                if effect.generation is None:raise UnsupportedEffect('Generation descriptor required')
                from autoslay.simulation.card_flow import generate_known
                identity,destination,level=effect.generation
                if level=='source':
                    if card is None:raise UnsupportedEffect('Generated upgrade requires its source card')
                    level=card.upgrade_level
                s,generated=generate_known(s,identity,destination,level,effect.amount,self.rules.references)
                for created in generated:
                    if created not in s.hand:continue
                    try:self.compiled[created.instance_id]=self.rules.card(created,s)
                    except UnsupportedEffect as exc:self.unsupported[created.instance_id]=str(exc)
                if any(c in s.hand for c in generated):
                    return Transition(s,'generated_cards_requires_observation',
                                      resume_effects=self._resume_effects(effects[index+1:]),pending_card=card,
                                      pending_exhaust=exhaust,effect_target_ids=tuple(sorted(targets)),play_power_snapshot=prior_powers)
            elif effect.kind == "lose_hp":
                if s.turn_hp_loss_cap is not None:
                    return Transition(s, "remaining_turn_hp_cap_requires_observation")
                s=lose_player_hp(s,effect.amount,self_inflicted=True)
            elif effect.kind=='player_damage':
                from autoslay.simulation.atomic_effects import damage_player
                s=damage_player(s,effect.amount,self_inflicted=True)
            elif effect.kind=='lose_max_hp':
                from autoslay.rules.resources import lose_max_hp
                s=lose_max_hp(s,effect.amount,lambda state,amount:lose_player_hp(state,amount,self_inflicted=True))
            elif effect.kind in {'exhaust_hand','discard_hand'}:
                removed=s.hand;keys=[c.variant_key or c.entity_id for c in removed]
                hooks=[]
                if effect.kind=='exhaust_hand':
                    from autoslay.simulation.card_flow import exhaust_card
                    s=replace(s,last_exhaust_count=len(removed))
                    for removed_card in removed:
                        s=exhaust_card(s,removed_card,self.rules)
                else:
                    s=replace(s,hand=(),last_discard_count=len(removed),
                        discard_composition=add_to_composition(s.discard_composition,keys))
                    s=apply_atomic(s,'unpowered_block',s.discard_block*len(removed))
                    for removed_card in removed:
                        if hasattr(self.rules,'lifecycle'):hooks.extend(self.rules.lifecycle(removed_card,s,'on_discard'))
                if hooks:return self._resolve(s,tuple(hooks)+effects[index+1:],targets,card,exhaust,prior_powers)
            elif effect.kind=='no_draw':
                s=replace(s,effect_draw_blocked=True,temporary_draw_blocked=True)
            elif effect.kind == "exhaust_nonattacks":
                if s.legion_ready or any(c.card_type is None for c in s.hand):
                    return Transition(s, "exhaust_nonattacks_requires_observation")
                exhausted = tuple(c for c in s.hand if c.card_type != "Attack")
                if any(c.instance_id not in self.compiled for c in exhausted):
                    return Transition(s, "unknown_exhaust_hooks_requires_observation")
                value=max(0,effect.amount+s.dexterity_delta)
                if card and card.canonical and s.player_frail:value=value*3//4
                elif s.player_frail and s.dexterity_delta:return Transition(s,'frail_dexterity_rounding_requires_observation')
                from autoslay.simulation.card_flow import exhaust_card
                for removed_card in exhausted:
                    s=exhaust_card(s,removed_card,self.rules)
                    s=replace(s,block=s.block+value)
            elif effect.kind.endswith("_boundary"):
                return Transition(s, effect.kind + "_requires_observation")
            elif effect.kind == "colossus":
                s = replace(s, colossus=True)
            elif effect.kind in {"draw","draw_discarded"}:
                requested=s.last_discard_count if effect.kind=='draw_discarded' else effect.amount
                draw_plan = plan_draw(requested, len(s.hand), sum(n for _, n in s.draw_composition),
                                      sum(n for _, n in s.discard_composition), hand_limit=s.hand_limit,
                                      blocked=s.effect_draw_blocked, discard_known=s.discard_known)
                if draw_plan.actual or not draw_plan.supported:
                    # Played card remains in the play pile during its draw; do not reshuffle it.
                    return Transition(s, "draw_requires_observation", draw_plan.actual,
                                      requested, draw_plan.reason,
                                      self._resume_effects(effects[index+1:]),
                                      card,exhaust,tuple(sorted(targets)),prior_powers)
            else:
                raise UnsupportedEffect("Unknown effect: " + effect.kind)
            if any(not e.minion for e in s.enemies) and all(e.hp <= 0 for e in s.enemies if not e.minion):
                # Secondary enemies stop combat; their remaining HP is not damage dealt.
                s = replace(s, combat_won=True)
            if s.hp <= 0 or s.combat_won or all(e.hp <= 0 for e in s.enemies):
                break
        if card is not None:
            from autoslay.rules.statuses import after_card
            s=after_card(s,card,prior_powers)
            s=replace(s,cards_played=s.cards_played+1,
                      combat_cards_played=s.combat_cards_played+1,
                      attacks_played=s.attacks_played+(card.card_type=='Attack'),
                      skills_played=s.skills_played+(card.card_type=='Skill'))
            from autoslay.rules.triggered_relics import advance_card_relic
            for relic_index in range(len(s.triggered_relics)):
                if s.hp<=0:break
                s,triggered=advance_card_relic(s,relic_index,card.card_type)
                if triggered and not s.combat_won:
                    transition=self._resolve(s,tuple(Effect(*e) for e in triggered),
                                             {e.instance_id for e in s.enemies if e.hp>0},None,False)
                    if transition.boundary:return replace(transition,pending_card=card,pending_exhaust=exhaust)
                    s=transition.state
            destination = ('removed_composition' if card.card_type=='Power' else
                           'exhausted_composition' if exhaust else 'discard_composition')
            if destination=='exhausted_composition':
                from autoslay.simulation.card_flow import exhaust_card
                s=exhaust_card(s,card,self.rules,from_hand=False)
            else:
                s=replace(s,**{destination:add_to_composition(getattr(s,destination),[card.variant_key or card.entity_id])})
        return Transition(s, requested_draw=draw_plan.requested if draw_plan else 0,
                          draw_reason=draw_plan.reason if draw_plan else None)
