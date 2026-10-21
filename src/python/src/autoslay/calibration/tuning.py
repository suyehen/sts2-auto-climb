"""Offline bounded profile comparison on independent reviewed card-choice pairs.

Labels must come from human/paired-trial review, never the calculator's own choice.
Split whole runs; average accuracy within runs before averaging across runs.
Writes proposals only, never edits active config. No win-rate fitting from one action.
"""
from collections import defaultdict
from dataclasses import replace
from statistics import mean
from autoslay.decision.cache import digest
from autoslay.calibration.store import load,candidates
from autoslay.evaluation.run import RunPolicy,RunContext,compare_choice,card_features,unmodeled
from autoslay.evaluation.contextual import load_interactions
from autoslay.knowledge_base.templates import index

def partition(trial_id):
    return 'holdout' if int(digest(trial_id)[:8],16)%5==0 else 'train'

def compare_profiles(labels,profiles,baseline,game_version,directory=None):
    if not isinstance(profiles,list) or not 1<=len(profiles)<=100: raise ValueError('1..100 profiles required')
    names=[p['name'] for p in profiles]
    if len(set(names))!=len(names) or 'baseline' not in names: raise ValueError('Unique profile names including baseline required')
    entries,_=index(expected_version=game_version); interactions=load_interactions()
    results={name:defaultdict(list) for name in names}; skipped=[]; used={}; profile_values={}
    signatures=defaultdict(set); run_trials=defaultdict(set)
    for label in labels:
        if label.get('label_source') not in {'human_review','paired_trial_review'}: continue
        try:
            better=label['preferred_index']; worse=label['other_index']
            key=(label['analysis_id'],min(better,worse),max(better,worse))
            signatures[key].add((label['trial_id'],better,worse))
            source=load(label['analysis_id'],directory)['report']['source']
            run=digest(dict(epoch=source.get('session_epoch'),run_id=source.get('run_id')))
            run_trials[run].add(label['trial_id'])
        except (KeyError,TypeError,ValueError,OSError): pass
    for p in profiles:
        if p.get('run',{}).get('version') or p.get('interactions',{}).get('version'): raise ValueError('Profile overrides cannot rewrite version labels')
        profile_values[p['name']]=(replace(baseline,**p.get('run',{})),replace(interactions,**p.get('interactions',{})))
    for line,label in enumerate(labels,1):
        try:
            if label.get('label_source') not in {'human_review','paired_trial_review'}: raise ValueError('Independent reviewed label required')
            trial=label['trial_id']; analysis=label['analysis_id']; better=label['preferred_index']; worse=label['other_index']
            if not isinstance(trial,str) or not trial or any(type(i) is not int or i<0 for i in (better,worse)) or better==worse: raise ValueError('Invalid reviewed pair')
            evidence=load(analysis,directory); report=evidence['report']
            if report['source']['game_version']!=game_version: raise ValueError('Game version mismatch')
            group=label.get('group','choices')
            if group!='choices': raise ValueError('This tuner supports card choices only')
            offers=candidates(report,group)
            if max(better,worse)>=len(offers): raise ValueError('Unknown reviewed candidate')
            key=(analysis,min(better,worse),max(better,worse))
            run=digest(dict(epoch=report['source'].get('session_epoch'),run_id=report['source'].get('run_id')))
            if len(signatures[key])>1: raise ValueError('Conflicting reviewed pair excluded entirely')
            if len(run_trials[run])>1: raise ValueError('Same physical run assigned multiple trial IDs; possible split leakage')
            if key in used:
                if used[key]!=(trial,better,worse): raise ValueError('Conflicting trial or reviewed pair')
                continue
            deck=evidence['snapshot']['state']['player']['deck']; context=RunContext(**report['context'])
            if any(unmodeled(card_features(c,entries)) for c in deck): raise ValueError('Baseline contains unmodeled card interactions')
            def value(candidate,policy,interaction):
                kind=candidate['kind']; card=candidate.get('card')
                if kind=='skip': return 0
                if kind not in {'add','remove','upgrade'} or unmodeled(card_features(card,entries)): raise ValueError('Unmodeled reviewed card')
                if kind=='upgrade' and unmodeled(card_features(dict(card,upgrade_level=card.get('upgrade_level',0)+1),entries)): raise ValueError('Unmodeled upgrade')
                kwargs={'add':card} if kind=='add' else {kind:card['instance_id']}
                comparison=compare_choice(deck,context,entries,policy=policy,interactions=interaction,
                    relics=evidence['snapshot']['state']['player'].get('relics') or [],**kwargs)
                if comparison['delta_utility'] is None:
                    raise ValueError('Unmodeled card choice continuation: '+str(comparison['mechanism_gaps']))
                return comparison['delta_utility']
            pair_results={}
            for name,(policy,interaction) in profile_values.items():
                margin=value(offers[better],policy,interaction)-value(offers[worse],policy,interaction)
                pair_results[name]=1 if margin>1e-9 else 0 if margin < -1e-9 else .5
            used[key]=(trial,better,worse)
            for name,correct in pair_results.items(): results[name][trial].append(correct)
        except (ValueError,KeyError,TypeError,IndexError,OSError) as exc: skipped.append(dict(line=line,reason=str(exc)))
    reports=[]
    for name,trials in results.items():
        item=dict(name=name)
        for split in ('train','holdout'):
            selected={trial:v for trial,v in trials.items() if partition(trial)==split}
            item[split]=dict(trials=len(selected),pairs=sum(len(v) for v in selected.values()),
                             agreement=mean(mean(v) for v in selected.values()) if selected else None)
        reports.append(item)
    best=max(reports,key=lambda r:r['train']['agreement'] if r['train']['agreement'] is not None else -1)
    enough=best['train']['trials']>=10 and best['holdout']['trials']>=5
    baseline_report=next(r for r in reports if r['name']=='baseline')
    independent_gain=enough and best['holdout']['agreement']>baseline_report['holdout']['agreement']
    return dict(schema_version='calibration/tuning/1.0',game_version=game_version,profiles=reports,skipped=skipped,
        unique_pairs=len(used),split='stable hash of whole trial; same run never appears in both sets',
        training_winner=best['name'] if used else None,sample_gate_met=enough,
        holdout_improved=bool(independent_gain),proposal=next(p for p in profiles if p['name']==best['name']) if independent_gain else None,
        applied=False,status='review_required' if independent_gain else 'insufficient_evidence_or_no_holdout_gain',
        limits=['reviewed ranking agreement is not win rate or causal card value',
                '10 train/5 holdout runs is an initial screening gate, not statistical proof',
                'untouched independent seeds and paired live trials are still required before promoting weights'])
