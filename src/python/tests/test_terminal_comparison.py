from copy import deepcopy
import asyncio
import json
import pytest

from autoslay.decision.deterministic import assess, certificates, load_policy
from autoslay.decision.terminal_comparison import prove
from autoslay.execution.deterministic import DeterministicExecutor
from test_deterministic import live


@pytest.fixture(autouse=True)
def baseline_policy(monkeypatch):
    # Local user overrides must not decide which rules these fixtures exercise.
    monkeypatch.setattr('autoslay.decision.deterministic.decision_config',
        lambda path: json.loads(path.read_text(encoding='utf-8-sig')))


def combat(hp=9):
    s = live()
    p = s['state']['player']
    p['character'] = 'CHARACTER.IRONCLAD'
    p['deck'] = deepcopy(p['hand'])
    s['state']['enemies'][0]['hp'] = hp
    return s


def add_card(s, identity, entity, cost, description):
    p = s['state']['player']
    card = dict(instance_id=identity, entity_id=entity, energy_cost=cost, target_type='AnyEnemy',
        description=description, modifiers_complete=True, enchantment_id=None, affliction_id=None)
    p['hand'].append(card)
    p['deck'].append(deepcopy(card))
    s['state']['legal_actions'].insert(-1, dict(action_id='play_'+identity, type='play_card',
        allowed_target_ids=['e'], allows_no_target=False))


def test_complete_multicard_finishes_are_equivalent_and_select_only_next_step():
    s = combat()
    before = deepcopy(s)
    result = assess({}, s)
    assert result['eligible'] and result['rule_id'] == 'equivalent_outcomes'
    assert result['selected_steps'] == [dict(action_id='play_a', target_id='e')]
    evidence = result['evidence']
    assert evidence['search']['complete'] and evidence['equivalent_best_count'] == 2
    assert evidence['persistent_outcome'] == dict(hp=30, max_hp=80)
    assert len(evidence['selected_continuation']) == 2 and len(evidence['alternative_first_steps']) == 2
    assert result['execution_authorized'] is False and s == before


def test_lossless_finish_strictly_dominates_self_damage_finish_without_scores():
    s = combat(10)
    add_card(s, 'h', 'CARD.HEMOKINESIS', 1, 'Lose 2 HP. Deal 15 damage.')
    report = dict(shortlist=[dict(steps=[dict(action_id='play_h', target_id='e')], metrics={'heuristic_score':999999})])
    result = assess(report, s)
    assert result['eligible'] and result['rule_id'] == 'dominant_outcome'
    assert result['selected_steps'][0]['action_id'] == 'play_a'
    assert result['evidence']['comparison'] == 'dominates'
    assert result['evidence']['dominated_finish_count'] > 0
    assert result['evidence']['hp_after_victory'] == 30


def test_common_first_step_uses_complete_routes_not_the_top_k_shortlist():
    s = combat(15)
    add_card(s, 'bash', 'CARD.BASH', 2, 'Deal 8 damage. Apply 2 Vulnerable.')
    result = assess({}, s)
    assert result['eligible'] and result['rule_id'] == 'common_first_step'
    assert result['selected_steps'] == [dict(action_id='play_bash', target_id='e')]
    assert result['evidence']['equivalent_best_count'] == 2
    assert result['evidence']['common_first_step'] is True


def test_after_common_first_step_vulnerable_is_reproved_from_new_snapshot():
    s = combat(7)
    s['state_version'] += 1
    s['state']['player']['energy'] = 1
    s['state']['enemies'][0]['powers'] = [dict(entity_id='POWER.VULNERABLE_POWER',amount=2)]
    result = assess({},s)
    assert result['eligible'] and result['rule_id'] == 'equivalent_outcomes'
    assert result['source']['state_version'] == s['state_version']
    assert len(result['evidence']['selected_continuation']) == 1


def test_costly_victory_does_not_displace_possibly_better_future_turn():
    s = combat(10)
    p = s['state']['player']
    p['hand'] = []
    s['state']['legal_actions'] = s['state']['legal_actions'][-1:]
    add_card(s, 'h', 'CARD.HEMOKINESIS', 1, 'Lose 2 HP. Deal 15 damage.')
    result = assess({}, s)
    assert not result['eligible']
    assert 'proof_finish_below_resource_upper_bound' in result['reasons']


def test_victory_heal_cap_can_make_self_damage_and_lossless_finishes_equivalent():
    s = combat(10)
    s['state']['player']['creature']['hp'] = 79
    s['state']['player']['relics'] = [dict(entity_id='RELIC.BURNING_BLOOD')]
    add_card(s, 'h', 'CARD.HEMOKINESIS', 1, 'Lose 2 HP. Deal 15 damage.')
    result = assess({}, s)
    assert result['eligible'] and result['rule_id'] == 'equivalent_outcomes'
    assert result['evidence']['hp_after_victory'] == 80
    assert result['evidence']['dominated_finish_count'] == 0


@pytest.mark.parametrize('change', ['draw','growth','potion','periodic','hidden','deck_missing','deck_mismatch','illegal_target','unknown_enemy','stale_game','uncertified_card','uncertified_character'])
def test_incomplete_or_persistent_interactions_fall_back_to_ai(change):
    s = combat()
    p = s['state']['player']
    if change == 'draw': add_card(s,'draw','CARD.SHRUG_IT_OFF',1,'Gain 8 Block. Draw 1 card.')
    if change == 'growth': p['deck'].append(dict(entity_id='CARD.FEED',modifiers_complete=True))
    if change == 'potion': p['potions'] = [dict(entity_id='POTION.FIRE_POTION')]
    if change == 'periodic': p['relics'] = [dict(entity_id='RELIC.INK_BOTTLE',counter=9)]
    if change == 'hidden': p['creature']['hidden_power_ids'] = ['POWER.UNKNOWN']
    if change == 'deck_missing': del p['deck']
    if change == 'deck_mismatch': p['draw_composition'] = [dict(entity_id='CARD.FEED',count=1)]
    if change == 'illegal_target': s['state']['legal_actions'][0]['allowed_target_ids'] = ['other']
    if change == 'unknown_enemy': s['state']['enemies'][0]['entity_id'] = 'MONSTER.NEW'
    if change == 'stale_game': s['game_assembly_sha256'] = 'new-version'
    if change == 'uncertified_character': p['character'] = 'CHARACTER.DEFECT'
    if change == 'uncertified_card':
        s['state']['enemies'][0]['hp'] = 23
        add_card(s,'howl','CARD.HOWL_FROM_BEYOND',1,'Deal 22 damage.')
    assert not assess({},s)['eligible']


def test_same_score_without_certified_finish_does_not_enable_local_execution():
    s = combat(100)
    report = dict(combat=dict(candidates=[dict(metrics={'heuristic_score':19.5})]*3))
    assert not assess(report, s)['eligible']


def test_incomplete_proof_search_never_uses_partial_winners():
    s = combat()
    actions = s['state']['legal_actions']
    enabled = set(load_policy()['enabled_rules'])
    result, reasons = prove(s, actions, certificates(s), enabled, max_nodes=2)
    assert result is None and reasons == ['proof_search_budget_exhausted']


def test_comparison_rules_can_be_disabled_independently():
    s = combat()
    policy = load_policy()
    policy['enabled_rules'] = ['equivalent_outcomes']
    assert assess({},s,policy)['rule_id'] == 'equivalent_outcomes'
    policy['enabled_rules'] = []
    assert not assess({},s,policy)['eligible']
    s = combat(10)
    add_card(s,'h','CARD.HEMOKINESIS',1,'Lose 2 HP. Deal 15 damage.')
    policy['enabled_rules'] = ['equivalent_outcomes','common_first_step']
    assert not assess({},s,policy)['eligible']


def test_future_resource_bound_requires_its_own_source_certificate():
    s = combat()
    cert = certificates(s)
    cert['resource_upper_bound_monsters'] = {}
    result,reasons = prove(s,s['state']['legal_actions'],cert,set(load_policy()['enabled_rules']))
    assert result is None and reasons == ['proof_future_resource_bound_not_certified']


def test_score_gap_is_diagnostic_and_never_a_shortcut():
    s = combat(100)
    report = dict(combat=dict(candidates=[dict(metrics={'heuristic_score':v}) for v in (100,1,1)]))
    result = assess(report,s)
    assert not result['eligible']
    assert result['score_review']['score_gap'] == 99
    assert result['score_review']['automatic_gap_selection'] is False
    assert result['score_review']['error_bound'] is None


def test_new_proof_executor_submits_one_action_and_rechecks_source(tmp_path):
    async def run():
        s = combat()
        calls = []
        async def fetch(): return deepcopy(s)
        async def submit(request): calls.append(request);return dict(status='completed')
        executor = DeterministicExecutor(tmp_path,fetch,submit,record=False)
        result = await executor.execute('equivalent-finish')
        assert result['status'] == 'completed' and len(calls) == 1
        assert calls[0].action_id == 'play_a'
        assert calls[0].expected_state_version == s['state_version']
        await executor.execute('equivalent-finish')
        assert len(calls) == 1
    asyncio.run(run())


def test_agent_skips_model_for_new_equivalent_finish_rule(tmp_path):
    from test_agent import make_agent
    async def run():
        agent,s,control,submit = make_agent(tmp_path,factory=lambda *args:pytest.fail('AI must not be called'))
        p = s['state']['player']
        p['character'] = 'CHARACTER.IRONCLAD'
        p['deck'] = deepcopy(p['hand'])
        s['state']['enemies'][0]['hp'] = 9
        class Local:
            async def execute(self,request_id):
                assert assess({},s)['rule_id'] == 'equivalent_outcomes'
                s['state']['phase'] = 'game_over'
                return dict(status='completed')
        agent.deterministic = Local()
        await agent.command('/start',{})
        await asyncio.wait_for(agent.task,3)
        assert agent.phase == 'finished' and agent.last['source'] == 'local_deterministic'
        assert submit.await_count == 0
    asyncio.run(run())
