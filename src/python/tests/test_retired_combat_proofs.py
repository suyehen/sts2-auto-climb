"""Combat finishes must pass through normal analysis, including old profiles."""
from copy import deepcopy
import pytest

from autoslay import configuration
from autoslay.decision.deterministic import assess, load_policy
from autoslay.decision.unified import analyze_decision
from test_deterministic import live


@pytest.mark.parametrize('enemy_hp', [2, 9, 15])
def test_finish_routes_do_not_preempt_batch_evaluation(enemy_hp):
    snapshot=live()
    snapshot['state']['enemies'][0]['hp']=enemy_hp
    snapshot['state']['player']['deck']=deepcopy(snapshot['state']['player']['hand'])
    # Even directly supplied legacy policies cannot revive the old shortcuts.
    policy=load_policy()
    policy['enabled_rules']+=sorted(configuration.RETIRED_COMBAT_RULES)
    qualification=assess({},snapshot,policy)
    assert not qualification['eligible']
    report=analyze_decision(snapshot,use_cache=False)
    assert report['autonomy']['rule_id']=='local_combat_batch'
    assert report['combat']['batches']['selected_batch']
    assert not report['ai_required']


def test_lethal_analysis_really_runs_normal_combat_analysis(monkeypatch):
    from autoslay.decision import unified
    class AnalysisReached(Exception):
        pass
    def analyze(*args,**kwargs):
        raise AnalysisReached()
    monkeypatch.setattr(unified,'analyze',analyze)
    with pytest.raises(AnalysisReached):
        unified.analyze_decision(live(),use_cache=False)


def test_legacy_rules_are_removed_on_config_import():
    current=configuration.baseline('autonomy')
    old=deepcopy(current)
    old['enabled_rules']+=sorted(configuration.RETIRED_COMBAT_RULES)
    migrated=configuration.validate_policies({'autonomy':old})['autonomy']
    assert migrated==current
    assert not configuration.RETIRED_COMBAT_RULES.intersection(load_policy()['enabled_rules'])


def test_unknown_and_duplicate_rules_are_still_rejected():
    for rules in (['unknown_rule'],['certain_combat_finish']*2):
        with pytest.raises(ValueError,match='CONFIG_INVALID_LIST'):
            configuration.validate_policies({'autonomy':{'enabled_rules':rules}})


def test_local_legacy_profile_is_read_without_rewriting_it(tmp_path,monkeypatch):
    import json
    root=tmp_path/'root'
    policy=root/'config/decision/autonomy.default.json'
    policy.parent.mkdir(parents=True)
    baseline=configuration.baseline('autonomy')
    policy.write_text(json.dumps(baseline),encoding='utf-8')
    local=root/'.runtime/local/agent/profiles.json'
    local.parent.mkdir(parents=True)
    old=deepcopy(baseline)
    old['enabled_rules']+=sorted(configuration.RETIRED_COMBAT_RULES)
    local.write_text(json.dumps({'decision':{'autonomy':old}}),encoding='utf-8')
    before=local.read_bytes()
    monkeypatch.setattr(configuration,'ROOT',root)
    assert configuration.decision_config(policy)==baseline
    assert local.read_bytes()==before
