from dataclasses import replace
import pytest
from autoslay.decision.contracts import Card, CombatState, Enemy, Operation, PeriodicRelic
from autoslay.decision.normalize import normalize
from autoslay.decision.service import analyze
from autoslay.simulation.card_flow import add_generated, plan_draw
from autoslay.simulation.engine import CombatSimulator
from autoslay.rules.periodic_relics import advance, read_periodic
from autoslay.evaluation.relic_timing import DelayCost, OpeningContext, TimingPolicy, evaluate_timing
from autoslay.knowledge_base.mechanics import references


def card(n, entity="STRIKE_IRONCLAD", text="Deal 6 damage.", target="AnyEnemy"):
    return Card(str(n), "CARD." + entity, 1, target, text)


def state(hand=(), **overrides):
    initial = CombatState(30, 80, 0, 3, tuple(hand),
                          (Enemy("e", "MONSTER.TEST", 40, 0, 0, None, (8,)),),
                          (("CARD.A", 4), ("CARD.B", 3)), discard_known=True)
    return replace(initial, **overrides)


def rows(report):
    return {row["delay_turns"]: row for row in report["options"]}


def test_draw_capacity_leaves_excess_in_pile():
    plan = plan_draw(3, 9, 7, 0)
    assert plan.actual == plan.from_draw == 1
    assert plan.from_discard == 0 and plan.reason == "hand_capacity_limited"
    assert plan_draw(3, 10, 7, 5).actual == 0
    assert plan_draw(3, 10, 7, 5).from_discard == 0  # no shuffle just because a draw was requested


def test_draw_prevention_is_not_start_hand_draw_or_generation_prevention():
    assert plan_draw(3, 0, 7, 0, blocked=True).actual == 0
    assert plan_draw(3, 0, 7, 0, blocked=True, from_hand_draw=True).actual == 3
    result = add_generated(state(effect_draw_blocked=True), (card("new"),))
    assert result.entered_hand == ("new",)


@pytest.mark.parametrize("size,hand_count,discard_count", [(9, 1, 2), (10, 0, 3), (8, 2, 1)])
def test_generated_overflow_goes_to_discard_in_order(size, hand_count, discard_count):
    s = state(tuple(card(i) for i in range(size)))
    result = add_generated(s, tuple(card(f"new{i}") for i in range(3)))
    assert len(result.entered_hand) == hand_count
    assert len(result.overflow_to_discard) == discard_count
    assert len(result.state.hand) == 10
    assert dict(result.state.discard_composition)["CARD.STRIKE_IRONCLAD"] == discard_count
    assert result.entered_hand == tuple(f"new{i}" for i in range(hand_count))
    assert result.state.draw_composition == s.draw_composition and len(s.hand) == size


@pytest.mark.parametrize("destination,field", [("discard", "discard_composition"),
                                               ("draw", "draw_composition"), ("exhaust", "exhausted_composition")])
def test_explicit_generated_destination_ignores_empty_hand_slots(destination, field):
    s = state()
    result = add_generated(s, (card("new", "NEW"),), destination)
    assert result.state.hand == ()
    assert dict(getattr(result.state, field))["CARD.NEW"] == 1
    assert result.overflow_to_discard == ()


def test_invalid_generation_and_capacity_fail_closed():
    with pytest.raises(ValueError):
        add_generated(state((card(1),)), (card(1),))
    with pytest.raises(ValueError):
        add_generated(state(), (card(1), card(1)))
    for args in [(3, 11, 7, 0), (-1, 0, 7, 0), (True, 0, 7, 0)]:
        with pytest.raises(ValueError):
            plan_draw(*args)
    with pytest.raises(ValueError):
        plan_draw(1, 0, 7, 0, hand_limit=12)


def test_draw_plan_reshuffle_and_pile_exhaustion():
    result = plan_draw(5, 0, 1, 3)
    assert (result.actual, result.from_draw, result.from_discard) == (4, 1, 3)
    assert result.reason == "piles_exhausted"
    assert not plan_draw(2, 0, 1, 0, discard_known=False).supported
    assert plan_draw(0, 0, 0, 0).actual == 0


def test_playing_draw_card_frees_a_hand_slot_and_keeps_play_card_out_of_shuffle():
    pommel = replace(card("p", "POMMEL_STRIKE", "Deal 9 damage. Draw 3 cards."),dynamic_variables=(("Damage",9),("Cards",3)))
    s = state((pommel,) + tuple(card(i, "UNMODELED") for i in range(9)))
    sim = CombatSimulator(s)
    result = sim.apply(s, Operation("play_p", "e"))
    assert len(result.state.hand) == 9 and result.draw_count == 1
    assert result.requested_draw == 3 and result.boundary == "draw_requires_observation"
    assert "CARD.POMMEL_STRIKE" not in dict(result.state.discard_composition)
    assert result.state.draw_composition == s.draw_composition  # unknown identities remain uncommitted until observation


def test_blocked_draw_does_not_stop_remaining_actions():
    shrug = card("s", "SHRUG_IT_OFF", "Gain 8 Block. Draw 1 card.", "Self")
    strike = card("a")
    s = state((shrug, strike), effect_draw_blocked=True)
    sim = CombatSimulator(s)
    first = sim.apply(s, Operation("play_s"))
    assert first.boundary is None and first.draw_count == 0 and first.draw_reason == "effect_draw_prevented"
    assert first.state.block == 8 and dict(first.state.discard_composition)["CARD.SHRUG_IT_OFF"] == 1
    second = sim.apply(first.state, Operation("play_a", "e"))
    assert second.state.enemies[0].hp == 34


def test_exhausted_card_never_enters_reshuffle():
    s = state((card("i", "IMPERVIOUS", "Gain 30 Block.", "Self"),))
    result = CombatSimulator(s).apply(s, Operation("play_i"))
    assert result.state.discard_composition == ()
    assert dict(result.state.exhausted_composition)["CARD.IMPERVIOUS"] == 1


def test_lethal_pommel_does_not_draw_after_combat_end():
    s = state((card("p", "POMMEL_STRIKE", "Deal 9 damage. Draw 2 cards."),))
    s = replace(s, enemies=(replace(s.enemies[0], hp=9),))
    result = CombatSimulator(s).apply(s, Operation("play_p", "e"))
    assert result.boundary is None and result.draw_count == 0


def snapshot():
    return {"schema_version": "1.0", "game_version": "0.111.0", "ready": False, "busy": False,
            "state": {"phase": "combat", "legal_actions": [], "player": {
                "creature": {"hp": 30, "max_hp": 80, "block": 0, "powers": []},
                "energy": 3, "hand_limit": 10, "turn_phase": "Play", "turn": 2,
                "hand": [{"instance_id": "p", "entity_id": "CARD.POMMEL_STRIKE", "energy_cost": 1,
                          "target_type": "AnyEnemy", "description": "Deal 9 damage. Draw 3 cards.",
                          "dynamic_variables": [dict(name="Damage",base_value=9),dict(name="Cards",base_value=3)]}],
                "draw_composition": [{"entity_id": "CARD.A", "count": 1}],
                "discard": [{"entity_id": "CARD.B"}, {"entity_id": "CARD.B"},
                            {"entity_id": "CARD.C"}, {"entity_id": "CARD.C"}],
                "relics": [], "potions": []},
                "enemies": [{"instance_id": "e", "entity_id": "MONSTER.TEST", "hp": 40, "block": 0,
                             "powers": [], "intents": [{"type": "Attack", "attack_damage": 8, "attack_repeats": 1}]}]}}


def test_reshuffle_probability_uses_observed_discard_not_playing_card():
    report = analyze(snapshot())
    draw = report["observation_boundaries"][0]["draw_analysis"]
    assert draw["supported"] and draw["reshuffle_required"]
    assert draw["at_least_one_by_entity"]["CARD.A"] == 1
    assert draw["at_least_one_by_entity"]["CARD.B"] == pytest.approx(5 / 6)
    assert "CARD.POMMEL_STRIKE" not in draw["at_least_one_by_entity"]
    assert not report["relic_timing"]["enabled"] and report["wait_candidates"] == []


@pytest.mark.parametrize("blocker", ["power", "fiddle"])
def test_real_adapter_draw_blockers_still_model_damage(blocker):
    data = snapshot()
    p = data["state"]["player"]
    if blocker == "power":
        p["creature"]["powers"] = [{"entity_id": "POWER.NO_DRAW_POWER", "amount": 1}]
    else:
        p["relics"] = [{"entity_id": "RELIC.FIDDLE", "counter": None}]
    assert normalize(data).effect_draw_blocked
    report = analyze(data)
    assert not report["coverage"]["interaction_warnings"]
    assert report["observation_boundaries"] == []
    assert any(c["draw_reason"] == "effect_draw_prevented" for c in report["candidates"])


def test_adapter_rejects_non_player_phase_or_unknown_capacity():
    data = snapshot()
    data["state"]["player"]["turn_phase"] = "End"
    with pytest.raises(ValueError):
        normalize(data)
    data["state"]["player"].update(turn_phase="Play", hand_limit=12)
    with pytest.raises(ValueError):
        normalize(data)


def test_counter_status_is_not_counter_and_unknown_is_not_zero():
    flower = read_periodic({"entity_id": "RELIC.HAPPY_FLOWER", "status": "Normal", "counter": 2})
    new, draws, energy = advance((flower,))
    assert new[0].counter == 0 and draws == 0 and energy == 1
    for value in (None, 3, True, -1):
        unknown = read_periodic({"entity_id": "RELIC.HAPPY_FLOWER", "counter": value})
        assert unknown.counter is None
        assert not evaluate_timing(state(periodic_relics=(unknown,)))["supported"]


def test_two_different_counters_need_not_both_be_period_minus_one():
    s = state(periodic_relics=(PeriodicRelic("R.A", 0, 3, draw_bonus=2),
                              PeriodicRelic("R.B", 1, 3, energy_bonus=1)))
    report = evaluate_timing(s)
    assert report["supported"]
    assert all(set(row["ending_counters"].values()) != {2} for row in report["options"])
    assert report["proposed_delay"] is None


def test_two_relic_draw_bonuses_share_capacity():
    s = state(periodic_relics=(PeriodicRelic("RELIC.PENDULUM", 2, 3, draw_bonus=1),
                              PeriodicRelic("RELIC.POLLINOUS_CORE", 3, 4, draw_bonus=2)))
    first = rows(evaluate_timing(s, context=OpeningContext(opening_draw=9)))[0]["next_combat"]["turns"][0]
    assert first["nominal_bonus_draw"] == 3 and first["actual_bonus_draw"] == 1
    assert len(first["triggering_relics"]) == 2


def test_full_opening_hand_prefers_second_turn_draw_under_free_verified_delay():
    # Generic fixture reproduces the user's 3-turn +2 example; not falsely registered as an actual game relic.
    s = state(periodic_relics=(PeriodicRelic("FIXTURE.DRAW_TWO", 2, 3, draw_bonus=2),))
    costs = tuple(DelayCost(n, 0, 0, True, "fixture_verified_finish") for n in range(4))
    policy = TimingPolicy(max_delay=3)
    ordinary = evaluate_timing(s, costs=costs, context=OpeningContext(opening_draw=5), policy=policy)
    assert ordinary["proposed_delay"] is None and ordinary["recommendation"] == "finish_now"
    full = evaluate_timing(s, costs=costs, context=OpeningContext(opening_draw=10), policy=policy)
    by_delay = rows(full)
    assert by_delay[0]["next_combat"]["turns"][0]["actual_bonus_draw"] == 0
    assert by_delay[2]["ending_counters"]["FIXTURE.DRAW_TWO"] == 1
    assert by_delay[2]["next_combat"]["turns"][1]["actual_bonus_draw"] == 2
    assert full["proposed_delay"] == 2


def test_waiting_hp_and_resource_cost_or_death_can_outweigh_draw_gain():
    s = state(periodic_relics=(PeriodicRelic("FIXTURE.DRAW_TWO", 2, 3, draw_bonus=2),))
    costs = (DelayCost(0, 0, 0, True, "fixture"),
             DelayCost(1, 10, 10, True, "fixture"), DelayCost(2, 5, 5, True, "fixture"),
             DelayCost(3, 30, 30, True, "fixture"))
    report = evaluate_timing(s, costs=costs, context=OpeningContext(opening_draw=10))
    assert report["recommendation"] == "finish_now" and report["proposed_delay"] is None
    assert rows(report)[3]["decision"] == "reject_death_or_reserve"
    penalized = (costs[0], DelayCost(2, 0, 0, True, "potion_spent", resource_penalty=5))
    assert evaluate_timing(s, costs=penalized, context=OpeningContext(opening_draw=10))["proposed_delay"] is None


def test_unknown_total_cost_or_future_kill_never_certifies_waiting():
    s = state(periodic_relics=(PeriodicRelic("FIXTURE.DRAW_TWO", 2, 3, draw_bonus=2),))
    for delayed in (DelayCost(2, 0, None, True, "unknown_cost"), DelayCost(2, 0, 0, False, "unknown_kill")):
        report = evaluate_timing(s, costs=(DelayCost(0, 0, 0, True, "fixture"), delayed),
                                 context=OpeningContext(opening_draw=10))
        assert report["proposed_delay"] is None
        assert rows(report)[2]["decision"] == "requires_future_observation"


def test_baseline_self_damage_is_counted_in_survival_and_incremental_cost():
    s = state(hp=8, periodic_relics=(PeriodicRelic("FIXTURE.DRAW_TWO", 2, 3, draw_bonus=2),))
    costs = (DelayCost(0, 5, 5, True, "self_damage_kill"), DelayCost(2, 8, 8, True, "self_damage_and_wait"))
    report = evaluate_timing(s, context=OpeningContext(opening_draw=10), costs=costs)
    assert rows(report)[0]["net_value_low"] == 0
    assert rows(report)[2]["decision"] == "reject_death_or_reserve"
    assert report["proposed_delay"] is None


def test_future_supply_and_retained_hand_limit_relic_value():
    s = state(periodic_relics=(PeriodicRelic("R", 2, 3, draw_bonus=2),))
    assert rows(evaluate_timing(s, context=OpeningContext(opening_draw=5, opening_hand=5)))[0]["next_combat"]["turns"][0]["actual_bonus_draw"] == 0
    assert rows(evaluate_timing(s, context=OpeningContext(supply_per_turn=(5,))))[0]["next_combat"]["turns"][0]["actual_bonus_draw"] == 0


def test_analysis_adds_wait_observation_and_keeps_current_kill_first():
    data = snapshot()
    p = data["state"]["player"]
    p["relics"] = [{"entity_id": "RELIC.PENDULUM", "counter": 2, "status": "Active"}]
    p["hand"][0].update(entity_id="CARD.STRIKE_IRONCLAD", description="Deal 6 damage.")
    data["state"]["enemies"][0]["hp"] = 6
    report = analyze(data, timing_context=OpeningContext(opening_draw=10))
    assert report["candidates"][0]["metrics"]["all_enemies_killed"]
    assert report["wait_candidates"][0]["boundary"] == "next_turn_requires_observation"
    assert report["relic_timing"]["recommendation"] == "finish_now"
    assert report["relic_timing"]["proposed_delay"] is None
    assert report["relic_timing"]["first_wait_visible_hp_forecast"] == 8
    assert not report["execution_authorized"]


@pytest.mark.parametrize("kwargs", [{"draw_value": -1}, {"energy_value": float("nan")},
                                     {"max_delay": 7}, {"horizon": 0}, {"hp_cost": 0}])
def test_timing_policy_validation(kwargs):
    with pytest.raises(ValueError):
        TimingPolicy(**kwargs)


def test_mechanisms_persist_offline_and_reject_other_versions():
    report = references("手牌")
    assert len(report["entries"]) >= 2 and report["game_version"] == "0.111.0"
    assert "source_inspected" in report["verification"]
    assert references("手牌", "../other")["error"] == "UNSUPPORTED_MECHANICS_VERSION"
