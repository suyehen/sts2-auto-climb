"""Compact presentation of a saved observation, no decisions or game calls."""
import json
import sys
from autoslay.paths import ROOT

path = ROOT / (sys.argv[1] if len(sys.argv) > 1 else ".runtime/logs/latest-decision.json")
data = json.loads(path.read_text(encoding="utf-8-sig"))
snapshot = data.get("snapshot") or data.get("after", data)
s = snapshot.get("state", {})
p = s.get("player") or {}
def powers(values):
    return [{"id": v["entity_id"], "amount": v["amount"]} for v in values or []]

def enemy(e):
    return {"id": e["instance_id"], "entity": e["entity_id"], "hp": e["hp"], "block": e["block"],
            "powers": powers(e.get("powers")), "intents": [{k: i.get(k) for k in
            ("type", "attack_damage", "attack_repeats")} for i in e.get("intents") or []]}
print(json.dumps({"request_id": data.get("request", {}).get("request_id"),
    "receipt": {k: data.get("receipt", {}).get(k) for k in ("status", "error", "message")},
    "ready": snapshot.get("ready"), "busy": snapshot.get("busy"), "phase": s.get("phase"), "floor": s.get("floor"),
    "hp": p.get("creature", {}).get("hp"), "block": p.get("creature", {}).get("block"),
    "powers": powers(p.get("creature", {}).get("powers")), "turn": p.get("turn"), "energy": p.get("energy"),
    "hand": [{k: c.get(k) for k in ("instance_id", "entity_id", "energy_cost", "description")} for c in p.get("hand") or []],
    "enemies": [enemy(e) for e in s.get("enemies") or []],
    "legal_actions": s.get("legal_actions"),
    "selection": s.get("card_selection") if s.get("card_selection", {}).get("screen_id") else None,
    "rewards": s.get("reward_screen") if s.get("reward_screen", {}).get("phase") else None}, ensure_ascii=True))
