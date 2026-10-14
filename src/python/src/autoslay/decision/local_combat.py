"""A bounded current-turn policy, explicitly distinct from a global outcome proof."""
from math import isfinite
from autoslay.decision.contracts import Operation
from autoslay.decision.normalize import normalize
from autoslay.simulation.engine import CombatSimulator
from autoslay.knowledge_base.repository import read_json, dataset_path

VERSION = '1.0.0'


def select(report, snapshot):
    from .deterministic import source, operations, ADMIN
    expected = source(snapshot)
    if not report or any((report.get('source') or {}).get(k) != v for k, v in expected.items()):
        return None, ['local_combat_requires_matching_analysis']
    combat = report.get('combat') or {}
    coverage = combat.get('coverage') or {}
    if any(key not in coverage for key in ('unsupported_cards', 'unsupported_potions', 'interaction_warnings')) or coverage['interaction_warnings']:
        return None, ['local_combat_mechanics_incomplete']
    if combat.get('search_truncated') is not False or combat.get('problems') != []:
        return None, ['local_combat_search_incomplete']
    state = snapshot['state'];player = state.get('player') or {}
    creatures = [player.get('creature') or {}, *(state.get('enemies') or [])]
    if any(c.get('hidden_power_ids') != [] for c in creatures):
        return None, ['local_combat_hidden_hooks_not_observed']
    if any(c.get('modifiers_complete') is not True for c in player.get('hand') or []):
        return None, ['local_combat_card_modifiers_not_observed']
    reviewed=read_json(dataset_path('certificates')).get('local_combat_modifiers') or {}
    if any(c.get('affliction_id') or c.get('enchantment_id') and (
               c['enchantment_id'] not in reviewed or c['entity_id'] not in reviewed[c['enchantment_id']].get('card_ids',[]))
           for c in player.get('hand') or []):
        return None, ['local_combat_card_modifier_not_certified']
    actions = [a for a in state.get('legal_actions') or [] if a.get('type') not in ADMIN | {'discard_potion'}]
    if any(a.get('type') not in {'play_card', 'end_turn', 'use_potion'} for a in actions):
        return None, ['local_combat_action_not_modeled']
    legal = {Operation(**op) for op in operations(actions)}
    try:
        initial = normalize(snapshot);simulator = CombatSimulator(initial)
        if initial.warnings or legal - {Operation('end_turn')} != set(simulator.actions(initial)) - {Operation('end_turn')}:
            return None, ['local_combat_legal_coverage_incomplete']
        # Unplayable/too expensive held cards need not block a currently legal
        # prefix. However, a resource/cost-changing card could enable them later.
        legal_cards={a.get('card_instance_id') or a['action_id'].removeprefix('play_')
                     for a in actions if a.get('type')=='play_card'}
        unsupported=set(coverage['unsupported_cards'])
        if unsupported & legal_cards:
            return None, ['local_combat_playable_card_unmodeled']
        if unsupported and any(effect.kind not in {'damage','unpowered_damage','block','unpowered_block',
                                                   'vulnerable','weak_boundary','lose_hp','draw'}
                               for key,card in simulator.compiled.items() if key in legal_cards for effect in card.effects):
            return None, ['local_combat_resource_change_could_enable_unknown_card']
    except (ValueError, KeyError, TypeError):
        return None, ['local_combat_normalization_incomplete']
    candidates = (combat.get('candidates') or []) + (combat.get('wait_candidates') or []) + (combat.get('observation_boundaries') or [])
    options = [];potion_tradeoffs=[]
    for candidate in candidates:
        metrics = candidate.get('metrics') or {}
        boundary=candidate.get('boundary')
        if boundary not in {None,'draw_requires_observation','next_turn_requires_observation'}:
            continue
        uncertainties=set(metrics.get('uncertainty') or [])
        if uncertainties-({boundary} if boundary else set()) or metrics.get('confidence') not in {'modeled_current_turn','partial'}:
            continue
        if metrics.get('confidence')=='partial' and (not boundary or uncertainties!={boundary}):
            continue
        if metrics.get('projected_death') is not False:
            continue
        if any(type(metrics.get(k)) not in (int, float) or not isfinite(metrics[k]) for k in
               ('projected_hp_loss', 'heuristic_score')):
            continue
        steps = candidate.get('steps') or [dict(action_id='end_turn', target_id=None)]
        if Operation(**steps[0]) not in legal:
            continue
        # Scarce consumables require a separate long-term comparison with AI.
        if any(step['action_id'].startswith('use_potion_') for step in steps):
            potion_tradeoffs.append((metrics.get('all_enemies_killed') is True,-metrics['projected_hp_loss']))
            continue
        # This policy does not price persistent card growth.
        if metrics.get('permanent_strength_gain', 0):
            continue
        rank = (metrics.get('all_enemies_killed') is True, -metrics['projected_hp_loss'], metrics['heuristic_score'],
                candidate.get('requested_draw',0) if boundary=='draw_requires_observation' else 0)
        options.append((rank, steps, metrics))
    if not options:
        return None, ['local_combat_no_safe_modeled_candidate']
    options.sort(key=lambda item: (tuple(-float(x) for x in item[0]), len(item[1]),
                                   tuple((s['action_id'], s.get('target_id') or '') for s in item[1])))
    _, steps, metrics = options[0]
    if any(value>(metrics.get('all_enemies_killed') is True,-metrics['projected_hp_loss']) for value in potion_tradeoffs):
        return None, ['local_combat_potion_survival_or_finish_tradeoff']
    return dict(rule_id='bounded_combat_search',
                reason='本地当前回合策略：在已完整覆盖的动作中，优先结束战斗、减少本回合失血，再比较战斗收益；保留药水，只执行首步后重新观察。这不是整局最优证明。',
                steps=[steps[0]], decision_basis='bounded_combat_strategy',
                evidence=dict(module_version=VERSION, decision_basis='bounded_combat_strategy',
                              optimality_proven=False, scope='visible action prefixes up to observation boundaries; known current intents',
                              search_complete=True, search_stops_at_observation=True,modeled_legal_operations=len(legal),
                              projected_hp_loss=metrics['projected_hp_loss'], heuristic_score=metrics['heuristic_score'],
                              selected_continuation=steps, consumables_preserved=True,
                              long_term_value_proven=False)), []
