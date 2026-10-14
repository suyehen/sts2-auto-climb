"""Independent full-action references, whole-run splits and explicit interval provenance."""
from collections import defaultdict
from math import isfinite
from pathlib import Path
from autoslay.paths import ROOT
from autoslay.decision.cache import digest

VERSION = '1.1.0'


def state_class(snapshot):
    state=snapshot['state'];p=state.get('player') or {};c=p.get('creature') or {}
    return digest(dict(monsters=sorted(e['entity_id'] for e in state.get('enemies') or [] if e.get('hp',0)>0),
                       cards=sorted({(v['entity_id'],v.get('upgrade_level',0),v.get('enchantment_id'),v.get('affliction_id'))
                                     for v in (p.get('deck') or [])+(p.get('hand') or [])},key=str),
                       powers=sorted(v['entity_id'] for v in c.get('powers') or []),
                       potions=sorted(v['entity_id'] for v in p.get('potions') or []),
                       low_hp=c.get('hp',0)/max(1,c.get('max_hp',1))<=.35))


def build(records, identity):
    """No historical AI labels; every referenced state includes ALL legal first actions.

    Max-error envelopes are empirical, not deterministic guarantees or a claimed
    confidence level. Holdout is evaluation only and never widens the envelope.
    """
    splits={};groups=defaultdict(list)
    for row in records:
        run=row['run_id'];split=row['split']
        if split not in {'development','calibration','holdout'}:raise ValueError('Invalid combat split')
        if run in splits and splits[run]!=split:raise ValueError('Whole-run split leakage')
        splits[run]=split
        if row.get('reference_source') not in {'independent_review','controlled_trial','exhaustive_reference'}:
            raise ValueError('Independent combat reference required')
        legal={tuple(sorted(s.items())) for s in row['legal_first_steps']}
        referenced={tuple(sorted(v['first_step'].items())) for v in row['actions']}
        if not legal or legal!=referenced or len(referenced)!=len(row['actions']):
            raise ValueError('Reference must cover every competing first step exactly once')
        if any(row.get(k)!=v for k,v in identity.items()):raise ValueError('Combat reference identity changed')
        for value in row['actions']:
            if any(type(value.get(k)) is not bool for k in ('predicted_safe','reference_safe')):
                raise ValueError('Independent survival labels required')
            for key in ('Q_hat','reference_value','simulation_error','search_error','sampling_error','terminal_error'):
                number=value.get(key)
                if type(number) not in (int,float) or not isfinite(number):raise ValueError('Missing independent error component')
        groups[row['state_class']].append(row)
    cohorts={}
    for kind,rows in groups.items():
        cal=[r for r in rows if r['split']=='calibration'];hold=[r for r in rows if r['split']=='holdout']
        actions=[v for r in cal for v in r['actions']]
        errors={k:max((abs(v[k]) for v in actions),default=0)
                for k in ('simulation_error','search_error','sampling_error','terminal_error')}
        radius=max((abs(v['reference_value']-v['Q_hat']) for v in actions),default=0)
        radius=max(radius,sum(errors.values()))
        checked=[v for r in hold for v in r['actions']]
        misses=[dict(run_id=r['run_id'],first_step=v['first_step'],high_risk=r.get('high_risk',False))
                for r in hold for v in r['actions'] if abs(v['reference_value']-v['Q_hat'])>radius]
        mechanisms_clean=all(v['simulation_error']==0 for r in rows for v in r['actions'])
        survival_misses=[dict(run_id=r['run_id'],split=r['split'],first_step=v['first_step'],
                              high_risk=r.get('high_risk',False))
                         for r in rows for v in r['actions'] if v['predicted_safe'] and not v['reference_safe']]
        survival_clean=not survival_misses
        cohorts[kind]=dict(radius=radius,error_components=errors,
                          calibration_runs=len({r['run_id'] for r in cal}),holdout_runs=len({r['run_id'] for r in hold}),
                          holdout_actions=len(checked),holdout_coverage=1-len(misses)/len(checked) if checked else None,
                          high_risk_misses=[v for v in misses if v['high_risk']],misses=misses,
                          survival_misses=survival_misses,survival_validated=survival_clean,
                          holdout_false_safe_actions=sum(v['predicted_safe'] and not v['reference_safe'] for v in checked),
                          authorized=bool(len({r['run_id'] for r in cal})>=10 and
                                          len({r['run_id'] for r in hold})>=5 and not misses and mechanisms_clean and survival_clean),
                          bound_kind='validated_empirical_interval',mechanics_clean=mechanisms_clean)
    result=dict(schema_version='combat-calibration/1.1',module_version=VERSION,**identity,
                cohorts=cohorts,run_splits=splits,
                interval_semantics='empirical max-error envelope; finite holdout coverage is not a deterministic guarantee',
                sample_gate='10 independent calibration runs and 5 untouched holdout runs per state class')
    result['fingerprint']=digest(result)
    return result


def load(identity):
    if not identity:return None
    from autoslay.calibration.store import safe_id
    import json
    path=ROOT/'data/calibration/combat'/(safe_id(identity)+'.json')
    try:return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return None


def apply(rows,artifact,identity,kind):
    if not artifact:return ['combat_calibration_missing']
    payload={k:v for k,v in artifact.items() if k!='fingerprint'}
    if artifact.get('schema_version')!='combat-calibration/1.1' or artifact.get('fingerprint')!=digest(payload):
        return ['combat_calibration_invalid']
    if any(artifact.get(k)!=v for k,v in identity.items()):return ['combat_calibration_stale']
    cohort=(artifact.get('cohorts') or {}).get(kind)
    if not cohort or not cohort.get('authorized') or not cohort.get('mechanics_clean') or cohort.get('survival_validated') is not True:
        return ['combat_calibration_state_unvalidated']
    radius=cohort.get('radius')
    if type(radius) not in (int,float) or not isfinite(radius) or radius<0:return ['combat_calibration_invalid']
    for row in rows:
        # Empirical draw frequency has no exact covered probability mass.
        if not row.get('complete') or row.get('Q_hat') is None or row.get('reserve_lower') is None:continue
        row.update(lower=row['Q_hat']-radius-row['reserve_upper'],
                   upper=row['Q_hat']+radius-row['reserve_lower'],
                   bound_kind=cohort['bound_kind'],calibration_error=radius,bounds_include_reserve=True)
    return []
