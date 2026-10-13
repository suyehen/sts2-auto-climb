from dataclasses import replace
from fractions import Fraction
import json
import pytest
from autoslay.decision.contracts import Card, CombatState, Enemy, Operation, UnsupportedEffect
from autoslay.decision.normalize import normalize
from autoslay.decision.service import analyze, load_policy
from autoslay.evaluation.combat import CombatEvaluator, CombatPolicy
from autoslay.evaluation.deck import compare_deck
from autoslay.planning.search import SearchBudget, search
from autoslay.probability.draw import at_least_one, distribution
from autoslay.simulation.engine import CombatSimulator
from autoslay.telemetry.verification import compare_transition


def card(instance, entity, description, cost=1, target="AnyEnemy"):
    return Card(instance, "CARD." + entity, cost, target, description)


def state(*cards, hp=30, enemy_hp=100, block=0, energy=3, vulnerable=0, cap=None, attacks=(10,), legion=False):
    return CombatState(hp, 80, block, energy, tuple(cards),
                       (Enemy("enemy", "MONSTER.TEST", enemy_hp, 0, vulnerable, cap, attacks),),
                       (("CARD.STRIKE_IRONCLAD", 1), ("CARD.DEFEND_IRONCLAD", 6)), legion_ready=legion)


def play(sim, s, instance, target="enemy"):
    return sim.apply(s, Operation("play_" + instance, target))


def test_draw_7_choose_3_exact_odds_and_duplicate_weights():
    assert [at_least_one(7, k, 3) for k in (1, 2, 3)] == [Fraction(3, 7), Fraction(5, 7), Fraction(31, 35)]
    dist = distribution({"A": 2, "B": 5}, 3)
    weights = {dict(o.cards).get("A", 0): o.probability for o in dist.outcomes}
    assert weights == {0: Fraction(2, 7), 1: Fraction(4, 7), 2: Fraction(1, 7)}
    assert sum(o.probability for o in dist.outcomes) == 1
    assert dist.method == "exact"


def test_draw_unique_35_outcomes_and_seeded_sampling():
    composition = {str(i): 1 for i in range(7)}
    assert len(distribution(composition, 3).outcomes) == 35
    a = distribution(composition, 3, exact_limit=1, samples=100, seed=42)
    assert a == distribution(composition, 3, exact_limit=1, samples=100, seed=42)
    assert a.method == "sampled" and sum(o.probability for o in a.outcomes) == 1
    assert sum(n for _, n in a.outcomes[0].cards) == 3


@pytest.mark.parametrize("args", [(7, 1, 8), (7, 8, 3), (-1, 0, 0), (7.0, 1, 3)])
def test_draw_rejects_invalid_or_reshuffle_inputs(args):
    with pytest.raises(ValueError):
        at_least_one(*args)


def test_empty_and_guaranteed_draws():
    assert at_least_one(0, 0, 0) == 0
    assert at_least_one(7, 1, 7) == 1
    assert distribution({}, 0).outcomes[0].probability == 1
    with pytest.raises(ValueError):
        distribution({"A": 2}, 3)


def test_multihit_vulnerable_rounding_and_cap():
    twin = card("twin", "TWIN_STRIKE", "Deal 7 damage twice.")
    s = state(twin, vulnerable=2, cap=9)
    sim = CombatSimulator(s)
    result = play(sim, s, "twin")
    assert result.state.enemies[0].hp == 82
    assert s.enemies[0].hp == 100  # immutable input


def test_damage_effect_order_strength_is_not_retroactive():
    setup = card("setup", "SETUP_STRIKE", "Deal 7 damage. Gain 3 Strength this turn.")
    twin = card("twin", "TWIN_STRIKE", "Deal 7 damage twice.")
    s = state(setup, twin)
    sim = CombatSimulator(s)
    first = play(sim, s, "setup")
    assert first.state.enemies[0].hp == 93
    second = play(sim, first.state, "twin")
    assert second.state.enemies[0].hp == 73
    assert second.state.strength_delta == second.state.temporary_strength == 3


def test_legion_only_first_block_card_doubled():
    defend = card("def", "DEFEND_IRONCLAD", "Gain 10 Block.", target="Self")
    shrug = card("shrug", "SHRUG_IT_OFF", "Gain 20 Block. Draw 1 card.", target="Self")
    s = state(defend, shrug, legion=True)
    sim = CombatSimulator(s)
    one = play(sim, s, "def", None)
    two = play(sim, one.state, "shrug", None)
    assert one.state.block == 10 and two.state.block == 20
    assert two.boundary == "draw_requires_observation"


def test_effect_order_bash_then_dismantle_and_overkill():
    bash = card("bash", "BASH", "Deal 8 damage. Apply 2 Vulnerable.", cost=2)
    dismantle = card("dismantle", "DISMANTLE", "Deal 8 damage. Attack twice if Vulnerable.")
    s = state(bash, dismantle, enemy_hp=20)
    sim = CombatSimulator(s)
    result = play(sim, play(sim, s, "bash").state, "dismantle")
    metrics = CombatEvaluator().evaluate(s, result, ())
    assert metrics["effective_hp_damage"] == 20
    assert metrics["all_enemies_killed"] and metrics["projected_hp_loss"] == 0


def test_enemy_strength_raises_every_visible_hit():
    fight = card("fight", "FIGHT_ME", "Deal 5 damage twice. Gain 3 Strength.", cost=2)
    s = state(fight, attacks=(1, 1, 1, 1))
    result = play(CombatSimulator(s), s, "fight")
    assert result.state.enemies[0].attacks == (2, 2, 2, 2)


def test_draw_search_stops_at_information_boundary():
    pommel = card("pom", "POMMEL_STRIKE", "Deal 9 damage. Draw 1 card.")
    strike = card("strike", "STRIKE_IRONCLAD", "Deal 6 damage.")
    s = state(pommel, strike)
    report = search(s, CombatSimulator(s), CombatEvaluator(), SearchBudget())
    for candidate in report["observation_boundaries"]:
        assert candidate["steps"][-1]["action_id"] == "play_pom"
        assert candidate["draw_count"] == 1
    assert report["observation_boundaries"]


def test_search_prioritizes_survival_and_reports_budget():
    strike = card("strike", "STRIKE_IRONCLAD", "Deal 6 damage.")
    s = state(strike, hp=5, enemy_hp=6, attacks=(20,))
    sim = CombatSimulator(s)
    report = search(s, sim, CombatEvaluator(), SearchBudget())
    assert report["candidates"][0]["metrics"]["all_enemies_killed"]
    assert not report["candidates"][0]["metrics"]["projected_death"]
    limited = search(s, sim, CombatEvaluator(), SearchBudget(max_nodes=1))
    assert limited["search_truncated"] and not limited["optimality_proven"]


def test_unknown_cards_and_interactions_fail_closed():
    unsupported = card("x", "UNIMPLEMENTED", "Do something complicated.")
    strike = card("strike", "STRIKE_IRONCLAD", "Deal 6 damage.")
    s = state(unsupported, strike)
    sim = CombatSimulator(s)
    assert "x" in sim.unsupported
    assert all(a.action_id != "play_x" for a in sim.actions(s))
    with pytest.raises(UnsupportedEffect):
        play(sim, replace(s, warnings=("UNMODELED_PLAYER_POWER:WEAK",)), "strike")


def test_policy_and_search_budget_validation(tmp_path):
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"version": "custom-2", "damage_weight": 4}), encoding="utf-8")
    assert load_policy(p).damage_weight == 4
    for weight in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            CombatPolicy(damage_weight=weight)
    with pytest.raises(ValueError):
        SearchBudget(time_ms=0)


def fixture_snapshot():
    return {"schema_version": "1.0", "game_version": "0.111.0", "bridge_version": "0.18.0",
            "session_epoch": "test", "state_version": 1, "ready": False, "busy": False,
            "state": {"phase": "combat", "legal_actions": [], "player": {
                "creature": {"hp": 30, "max_hp": 80, "block": 0, "powers": []},
                "energy": 3, "hand": [{"instance_id": "strike", "entity_id": "CARD.STRIKE_IRONCLAD",
                    "energy_cost": 1, "target_type": "AnyEnemy", "description": "Deal 6 damage."}],
                "relics": [], "potions": [], "draw_composition": [{"entity_id": "CARD.STRIKE_IRONCLAD", "count": 1}]},
                "enemies": [{"instance_id": "enemy", "entity_id": "MONSTER.TEST", "hp": 6, "block": 0,
                             "powers": [], "intents": [{"type": "Attack", "attack_damage": 10, "attack_repeats": 1}]}]}}


def test_report_reproducibility_versions_pause_and_journal(tmp_path):
    snapshot = fixture_snapshot()
    before = json.dumps(snapshot, sort_keys=True)
    journal = tmp_path / "journal.jsonl"
    a, b = analyze(snapshot, journal_path=journal), analyze(snapshot)
    assert a["candidates"] == b["candidates"]
    assert a["source"] == b["source"] and a["module_versions"] == b["module_versions"]
    assert not a["execution_authorized"] and not a["live_state_ready"]
    assert not a["candidates"][0]["first_step_currently_legal"]
    assert json.dumps(snapshot, sort_keys=True) == before
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 1


def test_normalizer_rejects_new_game_version_and_nested_state():
    snapshot = fixture_snapshot()
    snapshot["game_version"] = "0.112.0"
    with pytest.raises(ValueError):
        normalize(snapshot)
    snapshot["game_version"] = "0.111.0"
    snapshot["state"]["pending_combat_action"] = {"request_id": "waiting"}
    with pytest.raises(ValueError):
        normalize(snapshot)


def test_verification_detects_actual_numeric_mismatch():
    snapshot = fixture_snapshot()
    initial = normalize(snapshot)
    predicted = CombatSimulator(initial).apply(initial, Operation("play_strike", "enemy"))
    actual = fixture_snapshot()
    actual["state"]["enemies"] = []
    actual["state"]["player"]["energy"] = 2
    assert compare_transition(predicted, actual)["numeric_fields_match"]
    actual["state"]["player"]["energy"] = 1
    check = compare_transition(predicted, actual)
    assert not check["numeric_fields_match"]
    assert any(c["field"] == "player.energy" and not c["matches"] for c in check["checks"])


def test_deck_probability_delta_is_not_deck_strength():
    deck = [{"instance_id": str(i), "entity_id": "KEY" if i == 0 else "OTHER",
             "type": "Attack", "energy_cost": 1} for i in range(7)]
    report = compare_deck(deck, {"entity_id": "OTHER", "type": "Attack", "energy_cost": 2},
                          target_entities=("KEY",), opening_draw=3)
    assert report["after"]["opening_target_probability"] < report["before"]["opening_target_probability"]
    assert report["limitations"]


def test_draw_report_does_not_invent_reshuffle_probability():
    snapshot = fixture_snapshot()
    snapshot["state"]["player"]["hand"][0].update(entity_id="CARD.POMMEL_STRIKE", description="Deal 9 damage. Draw 2 cards.")
    snapshot["state"]["enemies"][0]["hp"] = 50
    report = analyze(snapshot)
    boundary = report["observation_boundaries"][0]
    assert boundary["draw_analysis"]["supported"] is False
    assert "reshuffle" in boundary["draw_analysis"]["reason"]

@pytest.mark.parametrize('flag',['costs_x','costs_star_x'])
def test_variable_cost_is_reported_without_crashing_or_fixed_cost_simulation(flag):
    from test_deterministic import live
    from autoslay.decision.unified import analyze_decision
    from autoslay.agent.context import build
    from autoslay.agent.contracts import validate_plan
    from autoslay.decision.deterministic import source,assess
    from autoslay.agent.plans import prepare
    s=live();s['state']['enemies'][0]['hp']=100
    held=s['state']['player']['hand'][0];held[flag]=True;held['energy_cost']=0
    model=normalize(s);sim=CombatSimulator(model)
    assert held['instance_id'] in sim.unsupported
    assert not any(a.action_id=='play_'+held['instance_id'] for a in sim.actions(model))
    assert not assess({},s)['eligible']
    report=analyze_decision(s,use_cache=False)
    assert held['instance_id'] in report['combat']['coverage']['unsupported_cards']
    packet=build(s,report)
    assert packet['visible_state']['state']['player']['hand'][0][flag]
    action=next(a for a in s['state']['legal_actions'] if a['action_id']=='play_'+held['instance_id'])
    raw=json.dumps(dict(state_token=source(s)['state_sha256'],reason='需依据可见信息决策',steps=[dict(action_id=action['action_id'],target_id=action['allowed_target_ids'][0] if action['allowed_target_ids'] else None)]))
    plan=validate_plan(raw,s)
    assert len(prepare(plan,s))==1
