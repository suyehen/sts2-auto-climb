"""Shared hand/pile rules; no hidden order, draw hooks or generated identities inferred."""
from collections import Counter
from dataclasses import dataclass, replace
from autoslay.decision.contracts import Card, CombatState, UnsupportedEffect

VERSION = "1.2.0"
GAME_VERSION = "0.111.0"
HAND_LIMIT = 10


def validate_count(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(name + " must be a nonnegative integer")


@dataclass(frozen=True)
class DrawPlan:
    requested: int
    actual: int
    from_draw: int
    from_discard: int
    reason: str
    supported: bool = True


def plan_draw(requested: int, hand_size: int, draw_size: int, discard_size: int,
              *, hand_limit: int = HAND_LIMIT, blocked: bool = False,
              from_hand_draw: bool = False, discard_known: bool = True) -> DrawPlan:
    for name, value in (("requested", requested), ("hand_size", hand_size), ("draw_size", draw_size),
                        ("discard_size", discard_size), ("hand_limit", hand_limit)):
        validate_count(value, name)
    if hand_limit != HAND_LIMIT or hand_size > hand_limit:
        raise ValueError("Unsupported hand capacity/state")
    if blocked and not from_hand_draw:
        return DrawPlan(requested, 0, 0, 0, "effect_draw_prevented")
    capacity = hand_limit - hand_size
    desired = min(requested, capacity)
    if desired > draw_size and not discard_known:
        return DrawPlan(requested, desired, min(desired, draw_size), 0, "discard_unknown_reshuffle", False)
    actual = min(desired, draw_size + discard_size)
    from_draw = min(actual, draw_size)
    reason = ("hand_full" if capacity == 0 and requested else
              "no_cards" if actual == 0 and desired else
              "hand_capacity_limited" if requested > capacity else
              "piles_exhausted" if actual < desired else "draw_available")
    return DrawPlan(requested, actual, from_draw, actual - from_draw, reason)


def add_to_composition(composition: tuple[tuple[str, int], ...], entities) -> tuple[tuple[str, int], ...]:
    counts = Counter(dict(composition))
    counts.update(entities)
    return tuple(sorted((entity, count) for entity, count in counts.items() if count))


def exhaust_card(state,card,rules,*,from_hand=True):
    """The same exhaust event for played cards, forced exhaust and Ethereal."""
    if state.hp<=0 or state.combat_won:return state
    from autoslay.rules.statuses import stack
    from autoslay.simulation.atomic_effects import apply_atomic
    state=replace(state,hand=tuple(c for c in state.hand if c.instance_id!=card.instance_id) if from_hand else state.hand,
        exhausted_composition=add_to_composition(state.exhausted_composition,[card.variant_key or card.entity_id]),
        cards_exhausted=state.cards_exhausted+1,turn_cards_exhausted=state.turn_cards_exhausted+1)
    state=apply_atomic(state,'unpowered_block',stack(state,'feel_no_pain'))
    if state.exhaust_damage:
        state=apply_atomic(state,'unpowered_damage',state.exhaust_damage,{e.instance_id for e in state.enemies if e.hp>0})
    if hasattr(rules,'lifecycle'):
        for effect in rules.lifecycle(card,state,'on_exhaust'):
            state=apply_atomic(state,effect.kind,effect.amount,repeats=effect.repeats)
    return state


def flush_hand(state, rules):
    """Ethereal precedes retain/flush, after modeled side-end triggers.

    Unknown held-card hooks stop prediction; they cannot disappear by discarding.
    Global overrides are rejected by the caller's mechanism scope as usual.
    """
    marks=dict(state.status_phase_marks)
    if state.turn is not None and marks.get('hand_flush')==state.turn:return state
    retained=[];discarded=[];exhausted=[]
    for card in state.hand:
        rules.card(card,state)
        from autoslay.rules.statuses import stack
        if hasattr(rules,'lifecycle'):
            for effect in rules.lifecycle(card,state,'turn_end_in_hand'):
                if effect.kind in {'lose_hp','player_damage'}:
                    from autoslay.simulation.atomic_effects import lose_player_hp,damage_player
                    value=effect.amount
                    if effect.kind=='player_damage':
                        state=damage_player(state,value,self_inflicted=True)
                    else:state=lose_player_hp(state,value,self_inflicted=True)
                elif effect.kind in {'player_weak','player_frail'}:
                    field='weak_turns' if effect.kind=='player_weak' else 'frail_turns'
                    from autoslay.rules.statuses import prevent_debuff
                    state,blocked=prevent_debuff(state)
                    if not blocked:
                        skip='weak_skip_next_tick' if field=='weak_turns' else 'frail_skip_next_tick'
                        state=replace(state,**{field:getattr(state,field)+effect.amount,skip:getattr(state,skip) or getattr(state,field)==0})
                else:raise UnsupportedEffect('Held-card phase operation not implemented: '+effect.kind)
        keywords=card.keywords
        if keywords is None:
            entry=rules.references[card.entity_id]
            variants=[v for v in entry['variants'] if v['upgrade_level']==card.upgrade_level]
            if len(variants)!=1:raise UnsupportedEffect('Held card variant unknown: '+card.entity_id)
            keywords=variants[0].get('keywords',[])
        key=card.variant_key or card.entity_id
        if 'Ethereal' in keywords:exhausted.append(card)
        elif 'Retain' in keywords or stack(state,'well_laid_plans') or stack(state,'retain_hand') or state.retain_hand_relic:retained.append(card)
        else:discarded.append(key)
    for card in exhausted:state=exhaust_card(state,card,rules)
    if state.turn is not None:marks['hand_flush']=state.turn
    return replace(state,hand=tuple(retained),status_phase_marks=tuple(sorted(marks.items())),
                   discard_composition=add_to_composition(state.discard_composition,discarded))


def refresh_retained(state, templates):
    templates=dict(templates)|{c.variant_key:c for c in state.generated_templates}
    cards=[]
    for card in state.hand:
        template=templates.get(card.variant_key or card.entity_id)
        if template is None:raise UnsupportedEffect('Retained card canonical state unavailable: '+card.entity_id)
        cards.append(replace(template,instance_id=card.instance_id,canonical=True))
    return replace(state,hand=tuple(cards))


def generate_known(state,identity,destination,upgrade_level,count,entries):
    """Fresh deterministic cards. No random pools, cost mutations or clone inference."""
    from autoslay.knowledge_base.templates import canonical
    if type(count) is not int or not 0<=count<=100:raise UnsupportedEffect('Unsupported generation count')
    if destination not in {'hand','discard','draw'}:raise UnsupportedEffect('Unsupported generation destination')
    if not count:return state,()
    prototype=replace(canonical(dict(entity_id=identity,upgrade_level=upgrade_level),entries)[0],canonical=True)
    # Same public multiset identity exported by Bridge.CardVariantKey for a fresh
    # card with no local modifiers. Actual hand identities still require observation.
    key=f'{identity}|{upgrade_level}|||{prototype.cost}|{prototype.star_cost}|{prototype.cost}|False|'
    prototype=replace(prototype,variant_key=key,instance_id='template:'+key)
    known={c.variant_key:c for c in state.generated_templates};known[key]=prototype
    used={c.instance_id for c in state.hand};cards=[]
    for i in range(count):
        instance=f'generated:{state.generation_counter+i}:{key}'
        while instance in used:instance+=':next'
        used.add(instance);cards.append(replace(prototype,instance_id=instance))
    placed=add_generated(state,tuple(cards),destination)
    return replace(placed.state,generation_counter=state.generation_counter+count,
                   generated_templates=tuple(known[k] for k in sorted(known))),tuple(cards)


def apply_drawn_hooks(state,cards,rules,*,from_hand_draw=False):
    if not hasattr(rules,'lifecycle'):return state
    from autoslay.simulation.atomic_effects import apply_atomic
    for card in cards:
        if not from_hand_draw:state=replace(state,turn_cards_drawn=state.turn_cards_drawn+1)
        for effect in rules.lifecycle(card,state,'on_draw'):
            state=apply_atomic(state,effect.kind,effect.amount,repeats=effect.repeats)
    return state


@dataclass(frozen=True)
class Placement:
    state: CombatState
    entered_hand: tuple[str, ...]
    overflow_to_discard: tuple[str, ...]


def add_generated(state: CombatState, cards: tuple[Card, ...], destination: str = "hand") -> Placement:
    """Known generated cards only. Unknown/random choices must be observed first.
    Handles destination and overflow, not arbitrary creation hooks or random pool selection.
    Draw prevention does not stop adding a generated card.
    """
    if destination not in {"hand", "discard", "draw", "exhaust"}:
        raise ValueError("Unsupported generated-card destination")
    if state.hand_limit != HAND_LIMIT or len(state.hand) > HAND_LIMIT:
        raise ValueError("Unsupported hand state")
    identities = [c.instance_id for c in cards]
    if len(set(identities)) != len(identities) or set(identities) & {c.instance_id for c in state.hand}:
        raise ValueError("Generated cards require distinct new instance IDs")
    hand_cards = cards[:max(0, state.hand_limit - len(state.hand))] if destination == "hand" else ()
    discarded = cards[len(hand_cards):] if destination == "hand" else cards if destination == "discard" else ()
    s = replace(state, hand=state.hand + hand_cards,
                discard_composition=add_to_composition(state.discard_composition, (c.variant_key or c.entity_id for c in discarded)))
    if destination == "draw":
        s = replace(s, draw_composition=add_to_composition(s.draw_composition, (c.variant_key or c.entity_id for c in cards)))
    elif destination == "exhaust":
        s = replace(s, exhausted_composition=add_to_composition(s.exhausted_composition, (c.variant_key or c.entity_id for c in cards)))
    return Placement(s, tuple(c.instance_id for c in hand_cards),
                     tuple(c.instance_id for c in discarded) if destination == "hand" else ())
