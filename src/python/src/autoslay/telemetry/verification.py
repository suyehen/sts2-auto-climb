"""Compare observable numeric effects; this does not certify complete game equivalence."""
from autoslay.decision.contracts import Transition

VERSION = "0.1.0"


def compare_transition(prediction: Transition, actual_snapshot: dict) -> dict:
    actual = actual_snapshot["state"]
    player = actual["player"]
    s = prediction.state
    checks = []

    def check(field, expected, observed):
        checks.append(dict(field=field, predicted=expected, observed=observed, matches=expected == observed))

    check("player.hp", s.hp, player["creature"]["hp"])
    check("player.block", s.block, player["creature"]["block"])
    check("player.energy", s.energy, player["energy"])
    enemies = {e["instance_id"]: e for e in actual.get("enemies") or []}
    for e in s.enemies:
        observed = enemies.get(e.instance_id)
        check(e.instance_id + ".hp", e.hp, observed["hp"] if observed else 0)
        if observed:
            check(e.instance_id + ".block", e.block, observed["block"])
            vulnerable = next((p["amount"] for p in observed.get("powers", [])
                               if p["entity_id"] == "POWER.VULNERABLE_POWER"), 0)
            check(e.instance_id + ".vulnerable", e.vulnerable, vulnerable)
    expected_ids = {e.instance_id for e in s.enemies}
    check("unexpected_enemy_ids", [], sorted(enemies.keys() - expected_ids))
    return dict(numeric_fields_match=all(c["matches"] for c in checks), checks=checks,
                boundary=prediction.boundary,
                limitations=["does not compare hidden state, hand draws, counters, hooks or end-of-combat healing"])
