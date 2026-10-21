from dataclasses import replace
import pytest
from autoslay.decision.contracts import Operation
from autoslay.decision.normalize import normalize
from autoslay.decision.local_combat import select
from autoslay.evaluation.combat import CombatEvaluator
from autoslay.planning.search import SearchBudget, search
from autoslay.simulation.engine import CombatSimulator
from autoslay.rules.mechanism_scope import observed_pickup
from test_deterministic import live
from test_decision import card, state


@pytest.mark.parametrize('identity',['RELIC.MANGO','RELIC.LARGE_CAPSULE','RELIC.POTION_BELT'])
def test_reviewed_pickup_uses_observed_resources_once(identity):
    snapshot=live();snapshot['state']['player']['relics']=[dict(entity_id=identity)]
    before=normalize(live());after=normalize(snapshot)
    assert before==after
    assert identity in observed_pickup(snapshot)
    snapshot['game_assembly_sha256']='changed'
    with pytest.raises(ValueError,match='Game implementation changed'):normalize(snapshot)


def test_capsule_does_not_exempt_granted_unknown_relic():
    snapshot=live();snapshot['state']['player']['relics']=[dict(entity_id=i) for i in ('RELIC.LARGE_CAPSULE','RELIC.NEW')]
    assert normalize(snapshot).warnings==('UNMODELED_RELIC:RELIC.NEW',)


def test_first_steps_survive_display_limit_and_converging_states():
    initial=state(card('a','STRIKE_IRONCLAD','Deal 6 damage.'),card('b','STRIKE_IRONCLAD','Deal 6 damage.'),energy=2)
    simulator=CombatSimulator(initial)
    report=search(initial,simulator,CombatEvaluator(),SearchBudget(top_k=1,time_ms=1000))
    assert not report['search_truncated'] and not report['problems']
    assert len(report['candidates'])==1
    assert {Operation(**s) for s in report['first_step_coverage']}==set(simulator.actions(initial))|{Operation('end_turn')}
    # Both a->b and b->a reach the same public state, retaining both origins.
    assert {'play_a','play_b'} <= {c['steps'][0]['action_id'] for c in report['decision_candidates']
                                  if c['metrics']['effective_hp_damage']==12 and c['steps']}


def test_nonpotion_option_is_retained_when_ui_shortlist_contains_potion():
    from autoslay.decision.contracts import Potion
    initial=replace(state(card('a','STRIKE_IRONCLAD','Deal 6 damage.'),attacks=()),
                    potions=(Potion('fire','POTION.FIRE_POTION','Deal 20 damage.','use_potion_fire',('enemy',)),))
    report=search(initial,CombatSimulator(initial),CombatEvaluator(),SearchBudget(top_k=1,time_ms=1000))
    assert any(s['action_id'].startswith('use_potion_') for s in report['candidates'][0]['steps'])
    assert any(c['steps'] and c['steps'][0]['action_id']=='play_a'
               and all(not s['action_id'].startswith('use_potion_') for s in c['steps']) for c in report['decision_candidates'])


def test_local_gate_uses_candidate_pool_when_display_is_empty():
    from test_local_combat import analyzed
    snapshot,report=analyzed()
    report['combat']['candidates']=[];report['combat']['wait_candidates']=[];report['combat']['observation_boundaries']=[]
    proof,reasons=select(report,snapshot)
    assert proof and not reasons and proof['decision_basis']=='bounded_combat_strategy'


@pytest.mark.parametrize('identity,hook',[
    ('MONSTER.KIN_FOLLOWER','SetupSkins'),('MONSTER.SHRINKER_BEETLE','ShouldShowMoveInBestiary')])
def test_presentation_scope_comes_from_native_code_and_new_hooks_remain_unknown(identity,hook):
    from copy import deepcopy
    from autoslay.rules.monsters import load,unmodeled_special_hooks
    entry=deepcopy(next(e for e in load()['entries'] if e['entity_id']==identity))
    assert not unmodeled_special_hooks(entry)
    changed=deepcopy(entry);changed['provenance']['source_sha256']='changed'
    assert not unmodeled_special_hooks(changed)
    entry['unmodeled_hooks'].append('AfterDeath')
    assert 'AfterDeath' in unmodeled_special_hooks(entry)


def test_known_death_link_is_still_blocked_without_its_mechanic_definition():
    from autoslay.rules.monsters import load,unmodeled_special_hooks
    entry=next(e for e in load()['entries'] if e['entity_id']=='MONSTER.KIN_PRIEST')
    assert 'AfterDeath' in unmodeled_special_hooks(entry)


def test_diagnostics_collect_independent_blockers_and_one_primary():
    from autoslay.decision.coverage import diagnose
    snapshot=live();snapshot['state']['player']['relics']=[dict(entity_id='RELIC.NEW')]
    snapshot['state']['enemies'][0]['hidden_power_ids']=['POWER.NEW']
    held=dict(snapshot['state']['player']['hand'][0])
    held.update(instance_id='unplayable',entity_id='CARD.ASCENDERS_BANE',energy_cost=-1,
                description='Unplayable. Ethereal.')
    snapshot['state']['player']['hand'].append(held)
    result=diagnose(snapshot,primary='UNMODELED_RELIC:RELIC.NEW')
    assert {'UNMODELED_RELIC','local_combat_hidden_hooks_not_observed'} <= {row['code'] for row in result['blockers']}
    assert sum(row['is_primary'] for row in result['blockers'])==1
    assert not any(row['entity_id']=='CARD.ASCENDERS_BANE' for row in result['blockers'])
    assert result['diagnostic_only']
