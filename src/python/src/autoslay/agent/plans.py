"""Bounded AI plans, simulated prefixes and fresh-state continuation guards."""
from dataclasses import asdict

from autoslay.decision.contracts import Operation, UnsupportedEffect
from autoslay.decision.normalize import normalize
from autoslay.simulation.engine import CombatSimulator


def prepare(plan, snapshot):
    """Only modeled prefixes can continue without another model request."""
    if len(plan.steps) == 1:
        return [(plan.steps[0], None)]
    try:
        state = normalize(snapshot)
        simulator = CombatSimulator(state)
    except (ValueError, KeyError, TypeError):
        return [(plan.steps[0], None)]
    result = []
    for step in plan.steps:
        before = state
        if step.action_id == 'end_turn':
            if step.target_id is not None:
                raise ValueError('ILLEGAL_TARGET')
            result.append((step, before))
            break
        try:
            transition = simulator.apply(state, Operation(**step.model_dump()))
        except (UnsupportedEffect, ValueError, KeyError, TypeError):
            # Unknown effects can still be chosen as a single fresh legal action.
            return result or [(plan.steps[0], None)]
        result.append((step, before))
        state = transition.state
        if transition.boundary or state.hp <= 0 or state.combat_won:
            break
    return result


def fingerprint(state):
    value = asdict(state)
    value['base_strength'] += value.pop('strength_delta')
    # Power presence is represented by normalized amounts, not stale ID tuples.
    value['enemies'] = [e for e in value['enemies'] if e['hp'] > 0]
    for enemy in value['enemies']:
        enemy['powers'] = tuple(p for p in enemy['powers'] if p not in {
            'POWER.VULNERABLE_POWER', 'POWER.STRENGTH_POWER', 'POWER.WEAK_POWER'})
    value['hand'] = sorted(value['hand'], key=lambda c: c['instance_id'])
    value['enemies'].sort(key=lambda e: e['instance_id'])
    return value


def matches(expected, fresh, origin):
    if not fresh.get('connected') or not fresh.get('ready') or fresh.get('busy') or fresh.get('error'):
        return False
    state, initial = fresh.get('state', {}), origin['state']
    if state.get('control_mode') != 'auto' or state.get('phase') != 'combat':
        return False
    selection = state.get('card_selection') or {}
    if (any(state.get(k) for k in ('pending_combat_action', 'pending_event_action'))
            or any(selection.get(k) for k in ('screen_id', 'screen_type', 'decision_ready', 'cards'))):
        return False
    if any(fresh.get(k) != origin.get(k) for k in ('session_epoch', 'game_version', 'game_assembly_sha256')):
        return False
    if state.get('run_id') != initial.get('run_id'):
        return False
    player, original = state.get('player', {}), initial['player']
    if any(player.get(k) != original.get(k) for k in ('turn', 'character', 'deck', 'relics', 'potions')):
        return False
    if player.get('creature', {}).get('hidden_power_ids') != original['creature'].get('hidden_power_ids'):
        return False
    enemies = {e['instance_id']: e for e in initial.get('enemies', [])}
    for enemy in state.get('enemies', []):
        before = enemies.get(enemy['instance_id'])
        if before is None or any(enemy.get(k) != before.get(k) for k in ('intents', 'hidden_power_ids')):
            return False
    try:
        return fingerprint(normalize(fresh)) == fingerprint(expected)
    except (ValueError, KeyError, TypeError):
        return False
