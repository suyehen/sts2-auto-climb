"""Bridge-specific adapter. No hidden draw order or RNG enters the model."""
from autoslay.knowledge_base.repository import scoped
from autoslay.decision.contracts import Card, CombatState, Enemy, Potion
from autoslay.rules.periodic_relics import read_periodic, RULES
from collections import Counter

VERSION = "1.4.4"
KNOWN_RELICS = {"RELIC.BURNING_BLOOD", "RELIC.BOOMING_CONCH", "RELIC.FRESNEL_LENS",
                "RELIC.VENERABLE_TEA_SET", "RELIC.CHANDELIER", "RELIC.LUCKY_FYSH",
                "RELIC.PAELS_LEGION", "RELIC.LANTERN", "RELIC.FIDDLE", "RELIC.TUNGSTEN_ROD",
                "RELIC.BEATING_REMNANT", "RELIC.PERMAFROST", "RELIC.SMALL_CAPSULE", "RELIC.REGAL_PILLOW",
                "RELIC.MANGO"} | set(RULES)
KNOWN_ENEMY_POWERS = {"POWER.VULNERABLE_POWER", "POWER.STRENGTH_POWER", "POWER.HARD_TO_KILL_POWER", "POWER.MINION_POWER", "POWER.WEAK_POWER"}


def card_view(value: dict) -> Card:
    return Card(value["instance_id"], value["entity_id"], value["energy_cost"],
                value["target_type"], value["description"], value.get("upgrade_level", 0), value.get("type"),
                star_cost=value.get('star_cost',-1),costs_x=bool(value.get('costs_x')),
                costs_star_x=bool(value.get('costs_star_x')))


@scoped
def normalize(snapshot: dict) -> CombatState:
    if snapshot.get("schema_version") != "1.0" or snapshot.get("game_version") != "0.111.0":
        raise ValueError("Unsupported snapshot or game version")
    from autoslay.knowledge_base.rulebook import Rulebook
    book=Rulebook()
    if snapshot.get('game_assembly_sha256') and snapshot['game_assembly_sha256']!=book.data.get('game_assembly_sha256'):
        raise ValueError('Game implementation changed; executable rules require review')
    s = snapshot["state"]
    if s.get("phase") != "combat" or s.get("pending_combat_action") or s.get("pending_event_action"):
        raise ValueError("Only stable combat states are modeled; nested choices require a fresh decision")
    p = s["player"]
    if p.get("turn_phase") not in {None, "Play"}:
        raise ValueError("Only player's play phase is modeled")
    if type(p.get("hand_limit", 10)) is not int or p.get("hand_limit", 10) != 10 or len(p.get("hand") or []) > 10:
        raise ValueError("Unsupported hand capacity/state")
    creature = p["creature"]
    warnings = []
    for power in creature.get("powers", []):
        if power["entity_id"] not in {"POWER.STRENGTH_POWER", "POWER.COLOSSUS_POWER", "POWER.NO_DRAW_POWER", "POWER.WEAK_POWER", "POWER.FRAIL_POWER"}:
            warnings.append("UNMODELED_PLAYER_POWER:" + power["entity_id"])
    legion = False
    periodic = []
    for relic in p.get("relics", []):
        rid = relic["entity_id"]
        compatibility=book.status(rid,snapshot['game_version'])
        # Mango has only AfterObtained; its max-HP gain is already reflected in
        # the live creature. It needs no in-combat rulebook execution adapter.
        if rid in KNOWN_RELICS and rid!='RELIC.MANGO' and compatibility!='compatible':
            warnings.append('STALE_RELIC_RULE:'+rid+':'+compatibility)
        tracked = read_periodic(relic)
        if tracked:
            periodic.append(tracked)
        if rid not in KNOWN_RELICS:
            warnings.append("UNMODELED_RELIC:" + rid)
        if rid == "RELIC.PAELS_LEGION":
            legion = relic.get("status") == "Active"
            if relic.get("counter") is None and not legion:
                warnings.append("UNKNOWN_LEGION_READINESS")
    enemies = []
    from autoslay.knowledge_base.behavior import load as load_behavior
    from autoslay.rules.monsters import unmodeled_special_hooks
    behavior=load_behavior()
    monster_entries={e['entity_id']:e for e in behavior.get('entries',[]) if e['category']=='monster'} if behavior.get('game_assembly_sha256')==snapshot.get('game_assembly_sha256') else {}
    for e in s.get("enemies") or []:
        hooks=unmodeled_special_hooks(monster_entries.get(e['entity_id'],{}))
        if hooks:
            warnings.append('UNMODELED_MONSTER_SPECIAL_HOOKS:'+e['entity_id']+':'+','.join(sorted(hooks)))
        powers = {x["entity_id"]: x["amount"] for x in e.get("powers", [])}
        for power in powers.keys() - KNOWN_ENEMY_POWERS:
            warnings.append("UNMODELED_ENEMY_POWER:" + e["instance_id"] + ":" + power)
        attacks = []
        uncertain = []
        for intent in e.get("intents", []):
            damage, repeats = intent.get("attack_damage"), intent.get("attack_repeats")
            if damage is not None and repeats is not None:
                if damage < 0 or not 0 <= repeats <= 100:
                    raise ValueError("Invalid visible attack")
                attacks.extend([damage] * repeats)
            else:
                uncertain.append(intent["type"])
        enemies.append(Enemy(e["instance_id"], e["entity_id"], e["hp"], e["block"],
                             powers.get("POWER.VULNERABLE_POWER", 0), powers.get("POWER.HARD_TO_KILL_POWER"),
                             tuple(attacks), tuple(sorted(powers)), tuple(uncertain), bool(powers.get("POWER.MINION_POWER")),
                             powers.get("POWER.STRENGTH_POWER", 0), powers.get("POWER.WEAK_POWER", 0) > 0,
                             max_hp=e.get('max_hp',0),weak_turns=powers.get('POWER.WEAK_POWER',0),
                             runtime_fields=tuple(sorted((k,v) for k,v in (e.get('runtime_state') or {}).get('fields',{}).items()
                                                        if type(v) in {int,bool,str}))))
    actions = {a["action_id"]: a for a in s.get("legal_actions", [])}
    potions = []
    for potion in p.get("potions", []):
        aid = potion.get("use_action_id")
        if aid in actions:
            potions.append(Potion(potion["instance_id"], potion["entity_id"], potion["description"],
                                  aid, tuple(actions[aid]["allowed_target_ids"])))
    return CombatState(creature["hp"], creature["max_hp"], creature["block"], p["energy"],
                       tuple(card_view(c) for c in p.get("hand") or []), tuple(enemies),
                       tuple(sorted((x["entity_id"], x["count"]) for x in p.get("draw_composition") or [])),
                       tuple(potions), legion_ready=legion,
                       colossus=any(p["entity_id"] == "POWER.COLOSSUS_POWER" for p in creature.get("powers", [])),
                       warnings=tuple(sorted(set(warnings))), hand_limit=p.get("hand_limit", 10),
                       effect_draw_blocked=any(x["entity_id"] == "POWER.NO_DRAW_POWER" for x in creature.get("powers", []))
                           or any(x["entity_id"] == "RELIC.FIDDLE" for x in p.get("relics", [])),
                       discard_composition=tuple(sorted(Counter(c["entity_id"] for c in p.get("discard") or []).items())),
                       discard_known="discard" in p,
                       exhausted_composition=tuple(sorted(Counter(c["entity_id"] for c in p.get("exhaust") or []).items())),
                       periodic_relics=tuple(periodic), turn=p.get("turn"),
                       hp_loss_reduction=1 if any(r["entity_id"] == "RELIC.TUNGSTEN_ROD" for r in p.get("relics", [])) else 0,
                       turn_hp_loss_cap=20 if any(r["entity_id"] == "RELIC.BEATING_REMNANT" for r in p.get("relics", [])) else None,
                       permafrost=any(r["entity_id"] == "RELIC.PERMAFROST" for r in p.get("relics", [])),
                       base_strength=next((r["amount"] for r in creature.get("powers", []) if r["entity_id"] == "POWER.STRENGTH_POWER"), 0),
                       player_frail=any(r["entity_id"] == "POWER.FRAIL_POWER" and r["amount"] > 0 for r in creature.get("powers", [])),
                       weak_turns=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.WEAK_POWER'),0),
                       frail_turns=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.FRAIL_POWER'),0),
                       stars=p.get('stars') or 0,
                       player_weak=any(r["entity_id"] == "POWER.WEAK_POWER" and r["amount"] > 0 for r in creature.get("powers", [])))
