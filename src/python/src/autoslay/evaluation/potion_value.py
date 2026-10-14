"""Explainable reserve intervals; each consumed bottle is charged exactly once."""
from math import isfinite

VERSION = '1.0.0'
DEFAULTS = {'POTION.FIRE_POTION': (8.0, 20.0), 'POTION.BLOCK_POTION': (6.0, 14.0),
            'POTION.ENERGY_POTION': (6.0, 18.0), 'POTION.STRENGTH_POTION': (8.0, 20.0)}


def reserve_cost(snapshot, potion, parameters=None):
    parameters = parameters or {}
    values = parameters.get('costs', DEFAULTS).get(potion.entity_id)
    if values is None: return dict(lower=None,upper=None,known=False,reason='potion_reserve_unmodeled')
    low, high = (values['lower'],values['upper']) if isinstance(values,dict) else values
    if any(type(x) not in (int,float) or not isfinite(x) or x<0 for x in (low,high)) or low>high:
        raise ValueError('Invalid potion reserve interval')
    player = snapshot['state'].get('player') or {}
    same = sum(p.get('entity_id') == potion.entity_id for p in player.get('potions') or [])
    factor = parameters.get('substitute_discount', 0.75) if same>1 else 1.0
    if type(factor) not in (int,float) or not 0<factor<=1:raise ValueError('Invalid substitute discount')
    # No prediction of a future drop. Only a currently offered overflow counts.
    offered = (snapshot['state'].get('reward_screen') or {}).get('rewards') or []
    pressure = (len(player.get('potions') or []) >= player.get('potion_slots',99)
                and any(r.get('type')=='PotionReward' and r.get('available') is True for r in offered))
    if pressure: factor *= 0.5
    return dict(lower=low*factor,upper=high*factor,known=True,
                basis='explicit future reserve interval; unknown route retained in interval',
                substitutes=same,observed_overflow=pressure,entity_id=potion.entity_id,
                action_id=potion.action_id)


def compare_pairs(rows, costs, margin=0):
    keep = [r for r in rows if not r.get('consumed_potions') and r.get('complete')]
    best_keep = max(keep,key=lambda r:r['Q_hat'],default=None)
    comparisons = []
    for row in rows:
        used = set(row.get('consumed_potions') or [])
        if not used: continue
        reserves = [costs.get(identity) for identity in used]
        known = all(c and c['known'] for c in reserves)
        lower = sum(c['lower'] for c in reserves) if known else None
        upper = sum(c['upper'] for c in reserves) if known else None
        delta = row['Q_hat']-best_keep['Q_hat'] if best_keep and row.get('Q_hat') is not None else None
        decision = 'unknown'
        if known and keep and all(r.get('death_upper')==1 for r in keep) and row.get('complete') and row.get('death_upper')==0:
            decision='only_modeled_survival'
        if known and best_keep and row.get('lower') is not None and best_keep.get('upper') is not None:
            # Calibrated intervals already include the reserve charge; raw
            # utility intervals require the explicit subtraction exactly once.
            net=row.get('bounds_include_reserve',False)
            if decision=='only_modeled_survival':pass
            elif row['lower']-best_keep['upper']>(0 if net else upper)+margin:decision='use'
            elif row['upper']-best_keep['lower']<(0 if net else lower):decision='keep'
            else:decision='overlapping_intervals'
        comparisons.append(dict(first_step=row['first_step'],consumed_potions=sorted(used),
                                modeled_gain=delta,reserve_lower=lower,reserve_upper=upper,
                                decision=decision,keep_first_step=best_keep['first_step'] if best_keep else None))
    return comparisons
