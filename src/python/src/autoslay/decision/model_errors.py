"""Per-decision failures for manual investigation, never persistent exclusions."""
from autoslay.knowledge_base.repository import scoped


class CombatModelError(ValueError):
    def __init__(self, code, **details):
        super().__init__(code)
        self.details = details


@scoped
def require_known_combat(snapshot):
    from autoslay.decision.normalize import normalize
    from autoslay.simulation.engine import CombatSimulator
    from autoslay.actions import operations
    from autoslay.decision.contracts import Operation
    try:
        state = normalize(snapshot)
    except (ValueError, KeyError, TypeError) as exc:
        raise CombatModelError('COMBAT_MODEL_UNKNOWN_EFFECT', blockers=[str(exc)]) from exc
    blockers = list(state.warnings)
    public = snapshot['state']
    player = public['player']
    from autoslay.knowledge_base.repository import read_json, dataset_path
    reviewed = read_json(dataset_path('certificates')).get('local_combat_modifiers') or {}
    for creature in [player.get('creature') or {}, *(public.get('enemies') or [])]:
        if creature.get('hidden_power_ids') != []:
            blockers.append('hidden_power_coverage_unknown:' + str(creature.get('instance_id', 'player')))
    for card in player.get('hand') or []:
        if card.get('modifiers_complete') is not True:
            blockers.append('card_modifiers_not_observed:' + card['instance_id'])
        enchantment = card.get('enchantment_id')
        if card.get('affliction_id') or enchantment and (
                enchantment not in reviewed or card['entity_id'] not in reviewed[enchantment].get('card_ids', [])):
            blockers.append('unmodeled_card_modifier:' + card['instance_id'])
    sim = CombatSimulator(state)
    # Ascender's Bane has a dedicated end-turn adapter in batch forecasts.
    for card in state.hand:
        if card.instance_id in sim.unsupported and card.entity_id != 'CARD.ASCENDERS_BANE':
            blockers.append(card.entity_id + ':' + sim.unsupported[card.instance_id])
    modeled = {(a.action_id, a.target_id) for a in sim.actions(state)}
    for op in operations([a for a in public.get('legal_actions') or [] if a.get('type') in {'play_card', 'use_potion'}]):
        if (op['action_id'], op.get('target_id')) not in modeled:
            blockers.append('unmodeled_legal_operation:' + op['action_id'])
            continue
        try:
            transition = sim.apply(state, Operation(**op))
            if transition.boundary not in {None, 'draw_requires_observation'}:
                blockers.append(op['action_id'] + ':' + transition.boundary)
        except (ValueError, KeyError, TypeError) as exc:
            blockers.append(op['action_id'] + ':' + str(exc))
    if blockers:
        raise CombatModelError('COMBAT_MODEL_UNKNOWN_EFFECT', blockers=sorted(set(blockers)))


def require_known_batch(report):
    batches = (report.get('combat') or {}).get('batches') or {}
    blockers = batches.get('execution_blockers') or []
    if blockers:
        raise CombatModelError('COMBAT_MODEL_UNKNOWN_EFFECT', blockers=blockers,
                               analysis_id=report.get('analysis_id'))
