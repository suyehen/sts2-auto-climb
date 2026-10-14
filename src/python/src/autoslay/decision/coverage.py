"""Versioned coverage audit; encyclopedia presence is not simulation support."""
from collections import Counter
from autoslay.decision.normalize import normalize
from autoslay.simulation.engine import CombatSimulator

VERSION='1.0.0'


def diagnose(snapshot, report=None, primary=None):
    """Collect independent visible blockers without granting execution authority."""
    from autoslay.decision.deterministic import operations, ADMIN
    from autoslay.knowledge_base.repository import current_snapshot
    from autoslay.decision.cache import digest
    state=snapshot.get('state') or {};player=state.get('player') or {}
    actions=[a for a in state.get('legal_actions') or [] if a.get('type') not in ADMIN|{'discard_potion'}]
    affected=[a['action_id'] for a in actions]
    revision=current_snapshot()
    rows=[]
    def add(stage,code,entity=None,instance=None,details=None):
        row=dict(stage=stage,code=code,entity_id=entity,instance_id=instance,
                 affected_actions=affected,source_fingerprint=revision.revision if revision else None,
                 is_primary=not rows if not primary else (code==primary or details==primary) and not any(r['is_primary'] for r in rows),details=details)
        if row not in rows:rows.append(row)
    combat=(report or {}).get('combat') or {}
    legal_cards={a.get('card_instance_id') or a['action_id'].removeprefix('play_')
                 for a in actions if a.get('type')=='play_card'}
    try:
        model=normalize(snapshot);simulator=CombatSimulator(model)
        for warning in model.warnings:
            parts=warning.split(':')
            entity=next((p for p in parts[1:] if p.startswith(('RELIC.','POWER.','MONSTER.'))),None)
            instance=parts[1] if parts[0]=='UNMODELED_ENEMY_POWER' else None
            add('mechanism',parts[0],entity,instance,warning)
        for card in model.hand:
            if card.instance_id in simulator.unsupported and card.instance_id in legal_cards:
                add('card', 'unsupported_card',card.entity_id,card.instance_id,simulator.unsupported[card.instance_id])
        if simulator.unsupported and any(effect.kind not in {'damage','unpowered_damage','block','unpowered_block',
                                                            'vulnerable','weak_boundary','lose_hp','draw'}
                                        for key,card in simulator.compiled.items() if key in legal_cards for effect in card.effects):
            add('card','local_combat_resource_change_could_enable_unknown_card')
        for identity in simulator.unsupported_potions:add('potion','unsupported_potion',identity)
        for enemy in model.enemies:
            if enemy.hp>0:
                for kind in enemy.uncertain_intents:add('intent','unmodeled_intent',enemy.entity_id,enemy.instance_id,kind)
        legal={tuple(sorted(op.items())) for op in operations(actions) if op['action_id']!='end_turn'}
        modeled={tuple(sorted(dict(action_id=op.action_id,target_id=op.target_id).items())) for op in simulator.actions(model) if op.action_id!='end_turn'}
        if legal!=modeled:add('action','local_combat_legal_coverage_incomplete')
    except (ValueError,KeyError,TypeError) as exc:
        add('normalize','local_combat_normalization_incomplete',details=str(exc))
    for creature in [player.get('creature') or {},*(state.get('enemies') or [])]:
        if creature.get('hidden_power_ids')!=[]:
            add('observation','local_combat_hidden_hooks_not_observed',creature.get('entity_id'),creature.get('instance_id'))
    for card in player.get('hand') or []:
        if card.get('modifiers_complete') is not True:
            add('observation','local_combat_card_modifiers_not_observed',card.get('entity_id'),card.get('instance_id'))
    if combat.get('search_truncated'):add('search','local_combat_search_incomplete')
    for problem in combat.get('problems') or []:add('search','unsupported_branch',details=problem)
    if primary and not any(row['is_primary'] for row in rows):add('policy',primary)
    return dict(module_version='1.1.0',blockers=rows,diagnostic_only=True,
                fingerprint=digest(rows),primary_reason=primary)

def audit(snapshots):
    cards=Counter(); potions=Counter(); warnings=Counter(); boundaries=Counter(); errors=[]
    for snapshot in snapshots:
        try:
            state=normalize(snapshot); simulator=CombatSimulator(state)
            cards.update(c.entity_id for c in state.hand if c.instance_id in simulator.unsupported)
            potions.update(simulator.unsupported_potions); warnings.update(state.warnings)
            for action in simulator.actions(state):
                try:
                    result=simulator.apply(state,action)
                    if result.boundary: boundaries[result.boundary]+=1
                except ValueError: pass
        except (ValueError,KeyError,TypeError) as exc: errors.append(str(exc))
    return dict(module_version=VERSION,unsupported_cards=cards.most_common(),unsupported_potions=potions.most_common(),
                interactions=warnings.most_common(),observation_boundaries=boundaries.most_common(),errors=errors,
                priority_basis='frequency in supplied player-visible snapshots')
