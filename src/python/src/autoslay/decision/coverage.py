"""Versioned coverage audit; encyclopedia presence is not simulation support."""
from collections import Counter
from autoslay.decision.normalize import normalize
from autoslay.simulation.engine import CombatSimulator

VERSION='1.0.0'

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
