"""Mechanic impact scopes from native definitions and observed state."""
from autoslay.rules.registry import ENTITY_MECHANICS, mechanism_status

VERSION = '2.0.0'


def observed_pickup(snapshot):
    # These mechanics run only during acquisition; combat starts from their
    # observed result. This does not simulate acquiring a relic during combat.
    owned={r['entity_id'] for r in (snapshot.get('state',{}).get('player',{}).get('relics') or [])}
    return {identity:dict(entity_id=identity,phase='pickup',trigger='obtained',
                          treatment='observed_in_snapshot',hook='AfterObtained')
            for identity in owned if identity in ENTITY_MECHANICS
            and ENTITY_MECHANICS[identity].passive=='pickup'}


def coverage_for_snapshot(snapshot, warnings):
    from autoslay.decision.contracts import MechanismImpact
    records=[MechanismImpact(identity,row['hook'],'observed_in_snapshot',
                             phase='pickup',trigger='obtained')
             for identity,row in observed_pickup(snapshot).items()]
    for card in snapshot['state']['player'].get('hand') or []:
        identity=card['entity_id']
        if mechanism_status(identity)=='defined':
            records.append(MechanismImpact(identity,'native_card_effects','modeled',
                                            phase='bounded_effects',trigger='play'))
    for warning in warnings:
        identity=next((p for p in warning.split(':')[1:] if p.startswith(('RELIC.','POWER.','MONSTER.'))),'UNKNOWN')
        records.append(MechanismImpact(identity,warning.split(':')[0],'unknown',warning=warning))
    return tuple(records)


def action_access(operation, effects=()):
    reads={'player.hp','player.energy','legal_actions','enemy.hp','enemy.block'}
    writes=set()
    for effect in effects:
        writes.update({'damage':{'enemy.hp','enemy.block','death','legal_actions'},
                       'strength':{'player.strength','future_damage'},
                       'temporary_strength':{'player.strength','future_damage'},
                       'block':{'player.block'},'lose_hp':{'player.hp','death'},
                       'energy':{'player.energy','legal_actions'},
                       'draw':{'player.hand','player.piles','player.energy','legal_actions','draw_hooks'}}.get(effect.kind,{'*'}))
    if operation.action_id=='end_turn':writes.add('*')
    return dict(reads=sorted(reads),writes=sorted(writes),comparison_scope='all competing first actions and future trigger closure')


def action_blockers(state, operation, effects=()):
    access=action_access(operation,effects)
    blockers=[]
    for row in state.mechanism_scope:
        if row.treatment in {'modeled','observed_in_snapshot','irrelevant_to_combat'}:continue
        # Unknown dependencies include competing alternatives, not just the chosen action.
        if '*' in row.writes or '*' in access['writes'] or set(row.writes)&set(access['reads']+access['writes']):
            blockers.append(row.warning or row.entity_id+':'+row.hook)
    return blockers
