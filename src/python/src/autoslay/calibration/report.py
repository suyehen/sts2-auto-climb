"""Diagnostics and trial-grouped outcomes; no causal score/HP conversion."""
import json
from collections import Counter,defaultdict
from pathlib import Path
from statistics import mean
from math import sqrt
from autoslay.calibration.store import DIRECTORY

def summarize(directory=None):
    directory=Path(directory or DIRECTORY)
    def read(folder): return [json.loads(p.read_text(encoding='utf-8-sig')) for p in (directory/folder).glob('*.json')]
    analyses=read('analyses'); choices=read('choices'); observations=read('observations')
    chosen={c['decision_id']:c for c in choices}; outcomes=defaultdict(list); errors=defaultdict(list); matches=0
    for row in observations:
        if row.get('trial_result'): outcomes[row['trial_id']].append(row['trial_result'])
        choice=chosen.get(row['decision_id']); candidate=(choice or {}).get('candidate') or {}
        # Current-turn forecasts must be observed at a turn endpoint and without a draw/random boundary.
        metrics=candidate.get('metrics') or {}; prediction=metrics.get('projected_hp_loss')
        if prediction is None: prediction=candidate.get('projected_hp_loss')
        if row['horizon']=='turn' and prediction is not None and not candidate.get('boundary') and row.get('hp_loss') is not None:
            errors[row['trial_id']].append(row['hp_loss']-prediction); matches+=1
    conflicts=[trial for trial,labels in outcomes.items() if len(set(labels))>1]
    results={trial:labels[0] for trial,labels in outcomes.items() if len(set(labels))==1}
    complete=[v for v in results.values() if v in {'win','loss'}]
    trial_profiles=defaultdict(set); profiles=defaultdict(set)
    for choice in choices:
        metadata=choice.get('trial_metadata') or {}
        key=(choice['source']['game_version'],choice.get('policy_hash','legacy_unclassified'),metadata.get('character'),metadata.get('ascension'))
        trial_profiles[choice['trial_id']].add(key); profiles[key].add(choice['trial_id'])
    mixed=[trial for trial,keys in trial_profiles.items() if len(keys)>1]
    grouped=[]
    for (game_version,policy_hash,character,ascension),trials in profiles.items():
        labels=[results[t] for t in trials if t not in mixed and t in results and results[t] in {'win','loss'}]
        wins=labels.count('win'); n=len(labels)
        interval=None
        if n:
            z=1.96; p=wins/n; center=(p+z*z/(2*n))/(1+z*z/n)
            half=z*sqrt(p*(1-p)/n+z*z/(4*n*n))/(1+z*z/n)
            interval=[max(0,center-half),min(1,center+half)]
        grouped.append(dict(game_version=game_version,policy_hash=policy_hash,character=character,ascension=ascension,trials=len(trials),completed_labeled_trials=n,
            wins=wins,caller_labeled_win_fraction=wins/n if n else None,approximate_wilson_95_interval=interval))
    return dict(schema_version='calibration/report/1.0',analyses=len(analyses),choice_intents=len(choices),observations=len(observations),
        trials=len({c['trial_id'] for c in choices}),terminal_label_counts=dict(Counter(results.values())),conflicting_trials=conflicts,
        caller_labeled_win_fraction=complete.count('win')/len(complete) if complete else None,
        comparable_groups=grouped,mixed_policy_trials=mixed,
        turn_prediction_samples=matches,turn_prediction_trials=len(errors),
        trial_mean_hp_error=mean(mean(v) for v in errors.values()) if errors else None,
        trial_mean_absolute_hp_error=mean(mean(abs(x) for x in v) for v in errors.values()) if errors else None,
        limitations=['overall counts are inventory only; compare groups by version/policy and controlled character/ascension/seeds',
                    'mixed-policy trials are excluded from profile win estimates; interval assumes independent labeled runs',
                    'intent records do not prove execution; observations are separately recorded',
                    'run labels are caller reported; abandonments excluded from win fraction',
                    'HP errors need review for healing, missing hooks and additional actions',
                    'no calibration or automatic parameter update from unchosen outcomes'])
