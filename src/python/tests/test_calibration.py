from copy import deepcopy
from dataclasses import replace
import json
import pytest
from autoslay.paths import ROOT
from autoslay.decision.unified import analyze_decision
from autoslay.evaluation.contextual import score,InteractionPolicy
from autoslay.evaluation.run import RunContext,RunPolicy,deck_value,intrinsic_value
from autoslay.evaluation.choices import cards_report
from autoslay.knowledge_base.templates import index
from autoslay.calibration import store
from autoslay.calibration.report import summarize
from autoslay.calibration.tuning import compare_profiles,partition

def raw(name,instance=None): return dict(entity_id='CARD.'+name,instance_id=instance or name,upgrade_level=0)

def feature(**values):
    return dict(cost=1,type='Skill',damage=0,block=0,draw=0,strength=0,vulnerable=0,self_hp=0,hits=0,exhaust_nonattack_block=0) | values

def evidence():
    snapshot=json.loads((ROOT/'data/replays/boss_turn4.json').read_text(encoding='utf-8-sig'))
    snapshot['state']['phase']='card_reward_selection'; snapshot['state']['pending_combat_action']=None
    snapshot['state']['player']['deck']=[raw('STRIKE_IRONCLAD','s'),raw('DEFEND_IRONCLAD','d')]
    snapshot['state']['reward_screen']=dict(cards=[dict(card=raw('TWIN_STRIKE','offer'),available=True,action_id='pick')])
    snapshot['connected']=True
    return snapshot,analyze_decision(snapshot,use_cache=False)

def test_component_sum_exact_and_blocked_draw_removes_base_and_synergy():
    features=[feature(draw=3),dict(feature(),cost=0)]
    context=RunContext(80,80,100,draw_blocked=True)
    low=score(features,context,RunPolicy(draw_weight=1),InteractionPolicy(draw_cheap_card_weight=1))
    high=score(features,context,RunPolicy(draw_weight=100),InteractionPolicy(draw_cheap_card_weight=100))
    assert low['effective']['draw']==0 and low['breakdown']['synergy']['draw_cheap_cards']==0
    assert low['utility_points']==pytest.approx(high['utility_points'])
    assert sum(high['components'].values())==pytest.approx(high['utility_points'])

@pytest.mark.parametrize('hand,available,expected',[(9,None,1),(10,None,0),(4,2,2),(4,0,0)])
def test_draw_capacity_and_available_cards_are_hard_limits(hand,available,expected):
    result=score([feature(draw=3)],RunContext(80,80,0,expected_hand_after_play=hand,expected_draw_available=available),RunPolicy(),InteractionPolicy())
    assert result['effective']['draw']==expected

def test_strength_multihit_synergy_changes_without_inventing_base_damage():
    provider=feature(strength=3)
    single=dict(feature(),type='Attack',damage=12,hits=1)
    multi=dict(single,hits=2)
    context=RunContext(80,80,0)
    a=score([provider,single],context,RunPolicy(),InteractionPolicy())
    b=score([provider,multi],context,RunPolicy(),InteractionPolicy())
    assert a['components']['base_value']==b['components']['base_value']
    assert b['breakdown']['synergy']['strength_multihit']>a['breakdown']['synergy']['strength_multihit']

def test_draw_synergy_responds_to_cheap_density():
    drawer=feature(draw=3)
    cheap=dict(feature(),cost=0,type='Attack',damage=6,hits=1)
    expensive=dict(cheap,cost=1)
    a=score([drawer,cheap],RunContext(80,80,0),RunPolicy(),InteractionPolicy())
    b=score([drawer,expensive],RunContext(80,80,0),RunPolicy(),InteractionPolicy())
    assert a['breakdown']['synergy']['draw_cheap_cards']>b['breakdown']['synergy']['draw_cheap_cards']

def test_intrinsic_draw_kept_separate_from_disabled_effect():
    entries=index()[0]; deck=[raw('STRIKE_IRONCLAD'),raw('OFFERING')]
    intrinsic=intrinsic_value(deck[1],entries)
    normal=deck_value(deck,RunContext(80,80,0),entries)
    blocked=deck_value(deck,RunContext(80,80,0,draw_blocked=True),entries)
    assert intrinsic['components']['draw']>0
    assert normal['metrics']['opening_draw_proxy']>0 and blocked['metrics']['opening_draw_proxy']==0
    assert blocked['scoring']['constraints']['effect_draw_blocked']

def test_marginal_components_sum_to_choice_delta():
    entries=index()[0]
    result=cards_report([raw('FIGHT_ME'),raw('STRIKE_IRONCLAD')],[raw('TWIN_STRIKE')],RunContext(80,80,0),entries)
    candidate=next(c for c in result['candidates'] if c['kind']=='add')
    assert sum(candidate['component_deltas'].values())==pytest.approx(candidate['delta_utility'])
    assert candidate['intrinsic']['utility'] is not None

def test_archive_intent_observation_and_idempotency(tmp_path):
    snapshot,report=evidence(); store.archive(report,snapshot,tmp_path)
    chosen=store.choose(report['analysis_id'],'choices',0,'keep current deck',snapshot,'trial-1',tmp_path)
    assert chosen==store.choose(report['analysis_id'],'choices',0,'keep current deck',snapshot,'trial-1',tmp_path)
    assert not chosen['execution_authorized']
    with pytest.raises(ValueError,match='different choice'):
        store.choose(report['analysis_id'],'choices',1,'take card',snapshot,'trial-1',tmp_path)
    after=deepcopy(snapshot); after['state_version']+=1; after['state']['player']['creature']['hp']-=3
    result=store.observe(chosen['decision_id'],'turn',after,directory=tmp_path)
    assert result==store.observe(chosen['decision_id'],'turn',after,directory=tmp_path)
    summary=summarize(tmp_path)
    assert summary['analyses']==1 and summary['choice_intents']==1 and summary['observations']==1
    assert summary['caller_labeled_win_fraction'] is None

def test_choice_rejects_stale_and_cross_session_observations(tmp_path):
    snapshot,report=evidence(); store.archive(report,snapshot,tmp_path)
    stale=deepcopy(snapshot); stale['state_version']+=1
    with pytest.raises(ValueError,match='Stale'):
        store.choose(report['analysis_id'],'choices',0,'skip',stale,directory=tmp_path)
    store.choose(report['analysis_id'],'choices',0,'skip',snapshot,directory=tmp_path)
    after=deepcopy(snapshot); after['session_epoch']='new-session'
    with pytest.raises(ValueError,match='different session'):
        store.observe(report['analysis_id'],'turn',after,directory=tmp_path)

@pytest.mark.parametrize('identifier',['../escape','C:/escape','x'*101,''])
def test_paths_cannot_escape_calibration_directory(identifier,tmp_path):
    with pytest.raises(ValueError): store.load(identifier,tmp_path)

def test_empty_data_never_promotes_weights(tmp_path):
    profiles=[dict(name='baseline'),dict(name='more_draw',run=dict(draw_weight=4))]
    result=compare_profiles([],profiles,RunPolicy(),'0.111.0',tmp_path)
    assert result['proposal'] is None and result['training_winner'] is None and not result['applied']

def test_review_pairs_not_own_choices_and_deduplicated(tmp_path):
    snapshot,report=evidence(); store.archive(report,snapshot,tmp_path)
    offers=report['choices']['candidates']
    preferred=next(i for i,c in enumerate(offers) if c['kind']=='add'); other=next(i for i,c in enumerate(offers) if c['kind']=='skip')
    label=dict(analysis_id=report['analysis_id'],trial_id='trial-1',preferred_index=preferred,other_index=other,label_source='human_review')
    result=compare_profiles([label,label,dict(label,label_source='model_choice')],[dict(name='baseline')],RunPolicy(),'0.111.0',tmp_path)
    assert result['unique_pairs']==1 and len(result['skipped'])==1 and result['proposal'] is None
    assert result['profiles'][0][partition('trial-1')]['trials']==1

def test_run_outcomes_deduplicate_and_exclude_abandonments(tmp_path):
    snapshot,report=evidence(); store.archive(report,snapshot,tmp_path)
    store.choose(report['analysis_id'],'choices',0,'skip',snapshot,'trial-1',tmp_path)
    store.observe(report['analysis_id'],'run',snapshot,trial_result='loss',directory=tmp_path)
    result=summarize(tmp_path)
    assert result['terminal_label_counts']=={'loss':1} and result['caller_labeled_win_fraction']==0
    after=deepcopy(snapshot); after['state_version']+=1
    store.observe(report['analysis_id'],'run',after,trial_result='win',directory=tmp_path)
    result=summarize(tmp_path)
    assert result['conflicting_trials']==['trial-1'] and result['caller_labeled_win_fraction'] is None

def test_conflicting_review_labels_both_excluded(tmp_path):
    snapshot,report=evidence(); store.archive(report,snapshot,tmp_path)
    label=dict(analysis_id=report['analysis_id'],trial_id='trial-1',preferred_index=0,other_index=1,label_source='human_review')
    result=compare_profiles([label,dict(label,preferred_index=1,other_index=0)],[dict(name='baseline')],RunPolicy(),'0.111.0',tmp_path)
    assert result['unique_pairs']==0 and len(result['skipped'])==2

def test_same_run_cannot_leak_through_different_trial_names(tmp_path):
    snapshot,report=evidence(); store.archive(report,snapshot,tmp_path)
    other=analyze_decision(snapshot,use_cache=False); store.archive(other,snapshot,tmp_path)
    labels=[dict(analysis_id=r['analysis_id'],trial_id=t,preferred_index=0,other_index=1,label_source='human_review') for r,t in [(report,'train_alias'),(other,'test_alias')]]
    result=compare_profiles(labels,[dict(name='baseline')],RunPolicy(),'0.111.0',tmp_path)
    assert result['unique_pairs']==0 and all('leakage' in r['reason'] for r in result['skipped'])

def test_live_draw_ban_cannot_be_overridden_by_context():
    from autoslay.decision.unified import context_for
    snapshot,_=evidence()
    snapshot['state']['player']['relics']=[dict(entity_id='RELIC.FIDDLE')]
    assert context_for(snapshot,dict(draw_blocked=False)).draw_blocked

def test_profile_proposal_requires_distinct_train_and_holdout_runs(tmp_path):
    from autoslay.decision.unified import source
    snapshot,_=evidence()
    snapshot['state']['reward_screen']['cards'][0]['card']=raw('OFFERING','offer')
    template=analyze_decision(snapshot,use_cache=False)
    better=next(i for i,c in enumerate(template['choices']['candidates']) if c['kind']=='add')
    worse=next(i for i,c in enumerate(template['choices']['candidates']) if c['kind']=='skip')
    split_counts={'train':0,'holdout':0}; labels=[]; i=0
    while split_counts['train']<10 or split_counts['holdout']<5:
        trial='reviewed-trial-'+str(i); i+=1; split=partition(trial)
        if split_counts[split]>=(10 if split=='train' else 5): continue
        s=deepcopy(snapshot); s['session_epoch']=trial; s['state']['run_id']=trial; s['state']['command_context_id']=trial
        report=deepcopy(template); report['analysis_id']=trial; report['source']=source(s)
        store.archive(report,s,tmp_path)
        labels.append(dict(analysis_id=trial,trial_id=trial,preferred_index=better,other_index=worse,label_source='human_review'))
        split_counts[split]+=1
    result=compare_profiles(labels,[dict(name='baseline'),dict(name='draw_candidate',run=dict(draw_weight=100))],RunPolicy(hp_weight=10),'0.111.0',tmp_path)
    assert result['sample_gate_met'] and result['holdout_improved']
    assert result['proposal']['name']=='draw_candidate' and not result['applied']
