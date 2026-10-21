"""Normalize observed bridge state. No hidden draw order or RNG enters the model."""
from autoslay.knowledge_base.repository import scoped
from autoslay.decision.contracts import Card, CombatState, Enemy, Potion,OstyState
from autoslay.rules.periodic_relics import read_periodic
from autoslay.rules.registry import KnowledgeRules, ENTITY_MECHANICS, IMPLEMENTATION_SHA256, STACK_POWERS,TEMPORARY_ENEMY_STRENGTH,TEMPORARY_PLAYER_STATS
from collections import Counter

VERSION = '2.1.0'


def card_view(value: dict) -> Card:
    return Card(value["instance_id"], value["entity_id"], value["energy_cost"],
                value["target_type"], value["description"], value.get("upgrade_level", 0), value.get("type"),
                star_cost=value.get('star_cost',-1),costs_x=bool(value.get('costs_x')),
                costs_star_x=bool(value.get('costs_star_x')),variant_key=value.get('variant_key',''),
                enchantment_id=value.get('enchantment_id'),enchantment_amount=value.get('enchantment_amount'),
                affliction_id=value.get('affliction_id'),
                dynamic_variables=tuple((v.get('name'),v.get('base_value')) for v in value.get('dynamic_variables') or []),
                keywords=tuple(value['keywords']) if 'keywords' in value else None)


@scoped
def normalize(snapshot: dict) -> CombatState:
    if snapshot.get("schema_version") != "1.0" or snapshot.get("game_version") != "0.111.0":
        raise ValueError("Unsupported snapshot or game version")
    rules=KnowledgeRules()
    if snapshot.get('game_assembly_sha256') and snapshot['game_assembly_sha256']!=IMPLEMENTATION_SHA256:
        raise ValueError('Game implementation changed; native mechanics require updating')
    s = snapshot["state"]
    if s.get("phase") != "combat" or s.get("pending_combat_action") or s.get("pending_event_action"):
        raise ValueError("Only stable combat states are modeled; nested choices require a fresh decision")
    p = s["player"]
    if p.get("turn_phase") not in {None, "Play"}:
        raise ValueError("Only player's play phase is modeled")
    if type(p.get("hand_limit", 10)) is not int or p.get("hand_limit", 10) != 10 or len(p.get("hand") or []) > 10:
        raise ValueError("Unsupported hand capacity/state")
    creature = p["creature"]
    counter_fields=('cards_played','attacks_played','skills_played','combat_cards_played',
                    'cards_exhausted','turn_cards_exhausted','turn_cards_drawn')
    counters=p.get('combat_counters') or {}
    history_known=all(type(counters.get(k)) is int and counters[k]>=0 for k in counter_fields)
    warnings = []
    from autoslay.rules.statuses import PET_POWER_IDS
    osty=None
    for pet in p.get('pets') or []:
        if pet['entity_id']!='MONSTER.OSTY' or osty is not None:
            warnings.append('UNMODELED_PET:'+pet['entity_id']);continue
        for power in pet.get('powers') or []:
            if power['entity_id'] not in PET_POWER_IDS:warnings.append('UNMODELED_PET_POWER:'+power['entity_id'])
        osty=OstyState(pet['hp'],pet['max_hp'])
    if 'pets' not in p and p.get('character')=='CHARACTER.NECROBINDER':warnings.append('UNOBSERVED_PET_STATE')
    from autoslay.rules.statuses import PLAYER_POWER_IDS, ENEMY_POWER_IDS
    for power in creature.get("powers", []):
        if power["entity_id"] not in PLAYER_POWER_IDS:
            warnings.append("UNMODELED_PLAYER_POWER:" + power["entity_id"])
    legion = False
    periodic = []
    scheduled=[]
    triggered=[]
    relic_state={}
    for relic in p.get("relics", []):
        rid = relic["entity_id"]
        mechanic=ENTITY_MECHANICS.get(rid)
        if mechanic:
            for field,value in mechanic.state_fields:
                if field=='turn_hp_loss_cap':relic_state[field]=min(relic_state.get(field,value),value)
                elif type(value) is bool:relic_state[field]=relic_state.get(field,False) or value
                else:relic_state[field]=relic_state.get(field,0)+value
        tracked = read_periodic(relic)
        if tracked:
            periodic.append(tracked)
        from autoslay.rules.scheduled_relics import read_scheduled
        try:scheduled.extend(read_scheduled(relic,p.get('turn'),rules))
        except ValueError as exc:warnings.append(str(exc))
        from autoslay.rules.triggered_relics import read_triggered
        try:triggered.extend(read_triggered(relic,p.get('turn'),rules))
        except ValueError as exc:warnings.append(str(exc))
        if rid not in ENTITY_MECHANICS:
            warnings.append("UNMODELED_RELIC:" + rid)
        if mechanic and mechanic.passive=='next_card_block_multiplier':
            legion = relic.get("status") == "Active"
            if relic.get("counter") is None and not legion:
                warnings.append("UNKNOWN_LEGION_READINESS")
    enemies = []
    from autoslay.rules.monsters import load as load_behavior
    from autoslay.rules.monsters import unmodeled_special_hooks
    behavior=load_behavior()
    monster_entries={e['entity_id']:e for e in behavior.get('entries',[]) if e['category']=='monster'} if behavior.get('game_assembly_sha256')==snapshot.get('game_assembly_sha256') else {}
    for e in s.get("enemies") or []:
        hooks=unmodeled_special_hooks(monster_entries.get(e['entity_id'],{}))
        if hooks:
            warnings.append('UNMODELED_MONSTER_SPECIAL_HOOKS:'+e['entity_id']+':'+','.join(sorted(hooks)))
        powers = {x["entity_id"]: x["amount"] for x in e.get("powers", [])}
        for power in powers.keys() - ENEMY_POWER_IDS:
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
                             move_state=e.get('current_move_id'),
                             runtime_fields=tuple(sorted((k,v) for k,v in (e.get('runtime_state') or {}).get('fields',{}).items()
                                                        if type(v) in {int,bool,str})),
                             poison=powers.get('POWER.POISON_POWER',0),
                             temporary_strength_loss=sum(powers.get(STACK_POWERS[k],0) for k in TEMPORARY_ENEMY_STRENGTH),
                             power_stacks=tuple(sorted((k,v) for k,v in powers.items() if k in STACK_POWERS.values()))))
    actions = {a["action_id"]: a for a in s.get("legal_actions", [])}
    potions = []
    for potion in p.get("potions", []):
        aid = potion.get("use_action_id")
        if aid in actions:
            potions.append(Potion(potion["instance_id"], potion["entity_id"], potion["description"],
                                  aid, tuple(actions[aid]["allowed_target_ids"]),bool(actions[aid].get('allows_no_target')),
                                  tuple((v.get('name'),v.get('base_value')) for v in potion.get('dynamic_variables') or [])))
    from autoslay.rules.mechanism_scope import coverage_for_snapshot
    return CombatState(creature["hp"], creature["max_hp"], creature["block"], p["energy"],
                       tuple(card_view(c) for c in p.get("hand") or []), tuple(enemies),
                       tuple(sorted((x.get('variant_key') or x["entity_id"], x["count"])
                                    for x in p.get('draw_variants',p.get("draw_composition")) or [])),
                       tuple(potions), legion_ready=legion,
                       colossus=any(p["entity_id"] == "POWER.COLOSSUS_POWER" for p in creature.get("powers", [])),
                       warnings=tuple(sorted(set(warnings))), hand_limit=p.get("hand_limit", 10),
                       effect_draw_blocked=any(x["entity_id"] == "POWER.NO_DRAW_POWER" for x in creature.get("powers", []))
                           or bool(relic_state.get('permanent_draw_blocked')),
                       discard_composition=tuple(sorted(Counter(c.get('variant_key') or c["entity_id"] for c in p.get("discard") or []).items())),
                       discard_known="discard" in p,
                       exhausted_composition=tuple(sorted(Counter(c.get('variant_key') or c["entity_id"] for c in p.get("exhaust") or []).items())),
                       periodic_relics=tuple(periodic), turn=p.get("turn"),
                       hp_loss_reduction=relic_state.get('hp_loss_reduction',0),
                       turn_hp_loss_cap=relic_state.get('turn_hp_loss_cap'),
                       base_strength=next((r["amount"] for r in creature.get("powers", []) if r["entity_id"] == "POWER.STRENGTH_POWER"), 0),
                       temporary_strength=sum(r['amount'] for r in creature.get('powers',[]) if r['entity_id'] in {STACK_POWERS[k] for k,v in TEMPORARY_PLAYER_STATS.items() if v=='strength'}),
                       player_frail=any(r["entity_id"] == "POWER.FRAIL_POWER" and r["amount"] > 0 for r in creature.get("powers", [])),
                       weak_turns=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.WEAK_POWER'),0),
                       frail_turns=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.FRAIL_POWER'),0),
                       weak_skip_next_tick=any(r.get('skip_next_duration_tick',False) for r in creature.get('powers',[]) if r['entity_id']=='POWER.WEAK_POWER'),
                       frail_skip_next_tick=any(r.get('skip_next_duration_tick',False) for r in creature.get('powers',[]) if r['entity_id']=='POWER.FRAIL_POWER'),
                       stars=p.get('stars') or 0,
                       base_dexterity=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.DEXTERITY_POWER'),0),
                       temporary_dexterity=sum(r['amount'] for r in creature.get('powers',[]) if r['entity_id'] in {STACK_POWERS[k] for k,v in TEMPORARY_PLAYER_STATS.items() if v=='dexterity'}),
                       temporary_draw_blocked=any(r['entity_id']=='POWER.NO_DRAW_POWER' for r in creature.get('powers',[])),
                       permanent_draw_blocked=bool(relic_state.get('permanent_draw_blocked')),
                       scheduled_relics=tuple(scheduled),
                       triggered_relics=tuple(triggered),
                       next_turn_block=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.BLOCK_NEXT_TURN_POWER'),0),
                       next_turn_energy=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.ENERGY_NEXT_TURN_POWER'),0),
                       next_turn_stars=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.STAR_NEXT_TURN_POWER'),0),
                       next_turn_draw=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.DRAW_CARDS_NEXT_TURN_POWER'),0),
                       ritual=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.RITUAL_POWER'),0),
                       power_stacks=tuple((r['entity_id'],r['amount']) for r in creature.get('powers',[])
                           if r['entity_id'] in STACK_POWERS.values()),
                       mechanism_scope=tuple(r for r in coverage_for_snapshot(snapshot,warnings) if r.warning),
                       regeneration=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.REGEN_POWER'),0),
                       plating=next((r['amount'] for r in creature.get('powers',[]) if r['entity_id']=='POWER.PLATING_POWER'),0),
                       history_known=history_known,**{k:counters[k] for k in counter_fields if history_known},
                       **{k:v for k,v in relic_state.items() if k in {'retain_hand_relic','retain_energy',
                           'extra_hand_draw','discard_block','exhaust_damage','shuffle_block','osty_attack_block'}},
                       osty=osty,pets_known='pets' in p,
                       player_weak=any(r["entity_id"] == "POWER.WEAK_POWER" and r["amount"] > 0 for r in creature.get("powers", [])))
