"""Complete finite comparisons of source-certified combat finishes.

The ordinary shortlist and its scores are deliberately not used. Each held card
can be played at most once; draw, unknown effects and persistent growth are
excluded. A resource upper bound also prevents a costly win from displacing a
potentially better later turn. Only the next action is authorized by the caller.
"""
from dataclasses import asdict
from time import perf_counter

from autoslay.decision.contracts import Operation, UnsupportedEffect
from autoslay.decision.outcomes import Outcome, compare

VERSION = '1.0.0'
MAX_NODES = 4096
TIME_MS = 100
ALLOWED = {'damage', 'unpowered_damage', 'block', 'unpowered_block', 'vulnerable', 'lose_hp'}
DIRECTIONS = {'hp': 1, 'max_hp': 1}


def enumerate_finishes(initial, simulator, root_operations, victory_heal, *, max_nodes=MAX_NODES, time_ms=TIME_MS):
    """Keep each first action distinct even when paths converge to one state."""
    started = perf_counter()
    stack = [(initial, ())]
    visited = set()
    finishes = []
    nodes = 0
    while stack:
        if nodes >= max_nodes or (perf_counter() - started) * 1000 >= time_ms:
            return None, dict(nodes=nodes, complete=False, blocker='proof_search_budget_exhausted')
        state, steps = stack.pop()
        key = (state, steps[0] if steps else None)
        if key in visited:
            continue
        visited.add(key)
        nodes += 1
        if state.hp <= 0:
            continue
        if state.combat_won or all(e.hp <= 0 for e in state.enemies):
            finishes.append((steps, Outcome('certified_combat_victory',
                (('hp', min(state.max_hp, state.hp + victory_heal)), ('max_hp', state.max_hp)), True, True)))
            continue
        actions = root_operations if not steps else simulator.actions(state)
        for action in sorted(actions, key=lambda a: (a.action_id, a.target_id or ''), reverse=True):
            if action.action_id == 'end_turn':
                continue  # Never substitute a current-intent forecast for the next turn.
            try:
                result = simulator.apply(state, action)
            except (UnsupportedEffect, ValueError, KeyError, TypeError):
                return None, dict(nodes=nodes, complete=False, blocker='proof_transition_unmodeled')
            if result.boundary:
                return None, dict(nodes=nodes, complete=False, blocker='proof_observation_boundary')
            if len(result.state.hand) >= len(state.hand):
                return None, dict(nodes=nodes, complete=False, blocker='proof_nonfinite_card_flow')
            stack.append((result.state, steps + (action,)))
    return finishes, dict(nodes=nodes, complete=True, elapsed_ms=(perf_counter() - started) * 1000)


def prove(snapshot, actions, cert, enabled, *, max_nodes=MAX_NODES, time_ms=TIME_MS):
    from autoslay.decision.deterministic import finish_model, operations
    p = snapshot['state']['player']
    if p.get('character') not in cert.get('finite_comparison_characters', []):
        return None, ['proof_character_mechanics_not_certified']
    if not isinstance(p.get('deck'), list) or not p['deck']:
        return None, ['proof_full_deck_not_observed']
    if any(key not in p for key in ('relics', 'potions', 'discard', 'exhaust', 'draw_composition')):
        return None, ['proof_piles_or_resources_not_observed']
    deck_ids = {c['entity_id'] for c in p['deck']}
    pile_ids = {c['entity_id'] for pile in ('hand', 'discard', 'exhaust') for c in p.get(pile, [])}
    pile_ids.update(c['entity_id'] for c in p['draw_composition'])
    if not pile_ids <= deck_ids:
        return None, ['proof_deck_and_piles_inconsistent']
    if any(not cert.get('resource_upper_bound_monsters', {}).get(e['entity_id'])
           or cert['resource_upper_bound_monsters'][e['entity_id']] != cert['finish_safe_monsters'].get(e['entity_id'], {}).get('source_sha256')
           for e in snapshot['state'].get('enemies', [])):
        return None, ['proof_future_resource_bound_not_certified']
    allowed_powers = set(cert.get('finite_comparison_enemy_powers', {})) & {'POWER.VULNERABLE_POWER'}
    initial, simulator, reasons = finish_model(snapshot, actions, cert, ALLOWED, allowed_powers)
    if reasons:
        return None, reasons
    for card in p['deck'] + p.get('hand', []):
        identity = card['entity_id']
        if identity in cert['finish_safe_unplayable_cards']:
            continue
        certificate = cert.get('finite_comparison_cards', {}).get(identity)
        if not certificate or certificate.get('reference_sha256') != simulator.rules.rulebook.data['entries'][identity]['reference_sha256']:
            return None, ['proof_card_sequence_not_certified:' + identity]
    legal = tuple(Operation(**op) for op in operations(actions) if op['action_id'] != 'end_turn')
    modeled = simulator.actions(initial)
    if len(legal) != len(set(legal)) or set(legal) != set(modeled):
        return None, ['proof_legal_action_coverage_incomplete']
    victory_heal = 6 if any(r['entity_id'] == 'RELIC.BURNING_BLOOD' for r in p['relics']) else 0
    finishes, search = enumerate_finishes(initial, simulator, legal, victory_heal, max_nodes=max_nodes, time_ms=time_ms)
    if finishes is None:
        return None, [search['blocker']]
    if not finishes:
        return None, ['no_certified_current_turn_finish']
    # No supported card/monster grants healing, max HP, gold or persistent growth.
    # The only certified heal is the fixed victory heal. A later turn cannot
    # exceed this bound; a win below it still requires AI to consider waiting.
    upper = Outcome('certified_combat_victory',
        (('hp', min(initial.max_hp, initial.hp + victory_heal)), ('max_hp', initial.max_hp)), True, True)
    best = [(steps, outcome) for steps, outcome in finishes if compare(outcome, upper, DIRECTIONS) == 'equal']
    if not best:
        return None, ['proof_finish_below_resource_upper_bound']
    dominated = [(steps, outcome) for steps, outcome in finishes if compare(upper, outcome, DIRECTIONS) == 'dominates']
    firsts = {steps[0] for steps, _ in best}
    # Deterministic selection: shortest proven continuation, then stable action IDs.
    best.sort(key=lambda pair: (len(pair[0]), tuple((op.action_id, op.target_id or '') for op in pair[0])))
    selected, outcome = best[0]
    if dominated and 'dominant_outcome' not in enabled:
        return None, ['proof_dominance_rule_disabled']
    if dominated:
        rule = 'dominant_outcome'
        reason = '完整比较本回合结束战斗的路线；所选路线达到战后资源上界，且严格优于存在额外失血的路线。'
    elif len(best) > 1 and len(firsts) == 1 and 'common_first_step' in enabled:
        rule = 'common_first_step'
        reason = '完整比较后的最佳结束战斗路线，第一步动作和目标一致；先执行这一动作，再读取状态重新证明。'
    elif len(best) > 1 and 'equivalent_outcomes' in enabled:
        rule = 'equivalent_outcomes'
        reason = '已完整比较多条本回合结束战斗的路线；战后持久资源与后续条件等价，按固定规则选择。'
    elif len(firsts) == 1 and 'common_first_step' in enabled:
        rule = 'common_first_step'
        reason = '完整搜索证明只有这一首步能在本回合达到最佳战后资源；先执行一步，再读取状态重新证明。'
    else:
        return None, ['proof_comparison_rule_disabled']
    evidence = dict(module_version=VERSION, comparison='dominates' if dominated else 'equal',
        scope='certified combat victory; unchanged deck/gold/relics/potions; combat-only resources reset',
        search=search, finish_count=len(finishes), equivalent_best_count=len(best), dominated_finish_count=len(dominated),
        hp_before=initial.hp, hp_after_victory=dict(outcome.values)['hp'],
        persistent_outcome=dict(outcome.values), resource_upper_bound=dict(upper.values),
        common_first_step=len(firsts) == 1, selected_continuation=[asdict(op) for op in selected],
        alternative_first_steps=[asdict(op) for op in sorted(firsts, key=lambda a: (a.action_id, a.target_id or ''))],
        compared_finishes=[dict(steps=[asdict(op) for op in steps], persistent_outcome=dict(value.values),
            relation_to_selected=compare(outcome, value, DIRECTIONS)) for steps, value in finishes[:20]],
        additional_finish_records=max(0, len(finishes) - 20), certificate_version=cert['version'])
    return dict(rule_id=rule, reason=reason, steps=[asdict(selected[0])], evidence=evidence), []
