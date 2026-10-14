"""Compare predeclared policy candidates on independent development references."""
from math import isfinite
from autoslay.calibration.combat import build
from autoslay.decision.cache import digest


def compare(profiles):
    if not 1 <= len(profiles) <= 16:
        raise ValueError('Declare between 1 and 16 development profiles')
    names=set(); common=None; summaries=[]
    for profile in profiles:
        name=profile['name']; records=profile['records']
        if name in names:raise ValueError('Duplicate development profile')
        names.add(name)
        if not records or any(r['split']!='development' for r in records):
            raise ValueError('Profile selection accepts development runs only')
        # Reuse full-action, provenance, numeric and whole-run validation.
        build(records,profile['identity'])
        reference={}; regrets=[]; unsafe=[]; errors=[]
        for row in records:
            key=(row['run_id'],row['state_id'])
            if key in reference:raise ValueError('Duplicate development state')
            actions=row['actions']
            for action in actions:
                if type(action.get('predicted_safe')) is not bool or type(action.get('reference_safe')) is not bool:
                    raise ValueError('Independent survival labels required')
                if not isfinite(action['reference_value']):raise ValueError('Invalid reference value')
            reference[key]=digest(sorted([(a['first_step'],a['reference_value'],a['reference_safe']) for a in actions],key=str))
            safe=[a for a in actions if a['predicted_safe']]
            chosen=max(safe,key=lambda a:a['Q_hat'],default=None)
            oracle=max((a['reference_value'] for a in actions if a['reference_safe']),default=None)
            if chosen is None:
                unsafe.append(dict(run_id=key[0],state_id=key[1],reason='no_safe_recommendation'))
            elif not chosen['reference_safe']:
                unsafe.append(dict(run_id=key[0],state_id=key[1],reason='unsafe_recommendation'))
            elif oracle is not None:regrets.append(max(0,oracle-chosen['reference_value']))
            errors.extend(abs(a['simulation_error']) for a in actions)
        if common is not None and reference!=common:
            raise ValueError('Profiles must use identical independent development states and references')
        common=reference
        summaries.append(dict(name=name,identity=profile['identity'],states=len(records),
            runs=len({r['run_id'] for r in records}),unsafe_or_missing=unsafe,
            mechanics_clean=not any(errors),mean_regret=sum(regrets)/len(regrets) if regrets else None,
            worst_regret=max(regrets,default=None)))
    eligible=[s for s in summaries if s['mechanics_clean'] and not s['unsafe_or_missing']]
    best=min(eligible,key=lambda s:(s['worst_regret'],s['mean_regret'],s['name']),default=None)
    return dict(schema_version='combat-profile-comparison/1.0',profiles=summaries,
                suggested_profile=best['name'] if best else None,development_state_fingerprint=digest(sorted(common.items())),
                execution_authorized=False,
                limitations=['Development comparison only; freeze selected policy before calibration and untouched holdout.',
                             'No active configuration is modified; ordinary AI choices are not reference labels.'])
