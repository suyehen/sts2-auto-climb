"""Compare observable numeric effects; this does not certify complete game equivalence."""
from autoslay.decision.contracts import Transition

VERSION = "0.3.0"


def compare_transition(prediction: Transition, actual_snapshot: dict) -> dict:
    actual = actual_snapshot["state"]
    player = actual["player"]
    s = prediction.state
    checks = []

    def check(field, expected, observed):
        checks.append(dict(field=field, predicted=expected, observed=observed, matches=expected == observed))

    check("player.hp", s.hp, player["creature"]["hp"])
    check('player.max_hp',s.max_hp,player['creature'].get('max_hp'))
    check("player.block", s.block, player["creature"]["block"])
    check("player.energy", s.energy, player["energy"])
    check("player.stars", s.stars, player.get("stars", 0))
    if s.pets_known and 'pets' in player:
        pet=next((p for p in player['pets'] or [] if p['entity_id']=='MONSTER.OSTY' and p['hp']>0),None)
        check('osty.hp',s.osty.hp if s.osty else 0,pet['hp'] if pet else 0)
        if s.osty and s.osty.hp>0:check('osty.max_hp',s.osty.max_hp,pet['max_hp'] if pet else None)
    if player.get('combat_counters') and s.history_known:
        for field in ('cards_played','attacks_played','skills_played','combat_cards_played',
                      'cards_exhausted','turn_cards_exhausted','turn_cards_drawn'):
            check('player.'+field,getattr(s,field),player['combat_counters'].get(field))
    powers = {p['entity_id']: p['amount'] for p in player['creature'].get('powers', [])}
    from autoslay.rules.registry import STACK_POWERS,TEMPORARY_PLAYER_STATS
    from autoslay.rules.statuses import PLAYER_POWER_IDS,ENEMY_POWER_IDS
    for identity in sorted(set(STACK_POWERS.values()) & PLAYER_POWER_IDS):
        check('player.'+identity,dict(s.power_stacks).get(identity,0),powers.get(identity,0))
    check("player.strength", s.base_strength+s.strength_delta, powers.get('POWER.STRENGTH_POWER', 0))
    check('player.dexterity',s.base_dexterity+s.dexterity_delta,powers.get('POWER.DEXTERITY_POWER',0))
    for field in ('strength','dexterity'):
        check('player.temporary_'+field,getattr(s,'temporary_'+field),
              sum(powers.get(STACK_POWERS[k],0) for k,v in TEMPORARY_PLAYER_STATS.items() if v==field))
    check('player.regeneration',s.regeneration,powers.get('POWER.REGEN_POWER',0))
    check('player.plating',s.plating,powers.get('POWER.PLATING_POWER',0))
    for field,identity in [('next_turn_block','POWER.BLOCK_NEXT_TURN_POWER'),('next_turn_energy','POWER.ENERGY_NEXT_TURN_POWER'),
                           ('next_turn_stars','POWER.STAR_NEXT_TURN_POWER'),('next_turn_draw','POWER.DRAW_CARDS_NEXT_TURN_POWER'),
                           ('ritual','POWER.RITUAL_POWER')]:
        check('player.'+field,getattr(s,field),powers.get(identity,0))
    check("player.weak_turns", s.weak_turns, powers.get('POWER.WEAK_POWER', 0))
    check("player.frail_turns", s.frail_turns, powers.get('POWER.FRAIL_POWER', 0))
    check("player.colossus", s.colossus, 'POWER.COLOSSUS_POWER' in powers)
    check("player.draw_blocked", s.effect_draw_blocked,
          'POWER.NO_DRAW_POWER' in powers or any(r['entity_id']=='RELIC.FIDDLE' for r in player.get('relics', [])))
    enemies = {e["instance_id"]: e for e in actual.get("enemies") or []}
    for e in s.enemies:
        observed = enemies.get(e.instance_id)
        check(e.instance_id + ".hp", e.hp, observed["hp"] if observed else 0)
        if observed:
            check(e.instance_id + ".block", e.block, observed["block"])
            vulnerable = next((p["amount"] for p in observed.get("powers", [])
                               if p["entity_id"] == "POWER.VULNERABLE_POWER"), 0)
            check(e.instance_id + ".vulnerable", e.vulnerable, vulnerable)
            enemy_powers = {p['entity_id']: p['amount'] for p in observed.get('powers', [])}
            for identity in sorted(set(STACK_POWERS.values()) & ENEMY_POWER_IDS):
                check(e.instance_id+'.'+identity,dict(e.power_stacks).get(identity,0),enemy_powers.get(identity,0))
            check(e.instance_id + ".strength", e.strength, enemy_powers.get('POWER.STRENGTH_POWER', 0))
            check(e.instance_id + ".weak_turns", e.weak_turns, enemy_powers.get('POWER.WEAK_POWER', 0))
            check(e.instance_id+'.poison',e.poison,enemy_powers.get('POWER.POISON_POWER',0))
    if s.triggered_relics:
        from autoslay.rules.registry import KnowledgeRules
        from autoslay.rules.triggered_relics import read_triggered
        rules=KnowledgeRules();observed_relics={r['entity_id']:r for r in player.get('relics') or []}
        for relic in s.triggered_relics:
            counter=None
            if relic.entity_id in observed_relics:
                try:
                    modeled=read_triggered(observed_relics[relic.entity_id],s.turn,rules)
                    if len(modeled)==1:
                        counter=modeled[0].counter
                        check(relic.entity_id+'.remaining_triggers',relic.remaining_triggers,modeled[0].remaining_triggers)
                except (ValueError,KeyError):pass
            check(relic.entity_id+'.counter',relic.counter,counter)
    expected_ids = {e.instance_id for e in s.enemies}
    check("unexpected_enemy_ids", [], sorted(enemies.keys() - expected_ids))
    return dict(numeric_fields_match=all(c["matches"] for c in checks), checks=checks,
                boundary=prediction.boundary,
                limitations=["does not compare hidden state, hand draws, unmodeled counters/hooks or end-of-combat healing"])
