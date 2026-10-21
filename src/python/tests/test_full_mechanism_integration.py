from copy import deepcopy
from dataclasses import replace
import pytest
from autoslay.decision.contracts import Operation,Transition
from autoslay.decision.normalize import normalize
from autoslay.simulation.engine import CombatSimulator
from autoslay.telemetry.verification import compare_transition
from autoslay.execution.sequence import relic_projection
from test_combat_batches import combat,actions


@pytest.mark.local_knowledge
def test_live_counter_field_overrides_animated_display_and_verifies_expected_progress():
    snapshot=combat();player=snapshot['state']['player']
    player['relics']=[dict(entity_id='RELIC.NUNCHAKU',counter=10,status='Normal',
                          runtime_state={'fields':{'_attacksPlayed':9}})]
    initial=normalize(snapshot)
    assert initial.triggered_relics[0].counter==9 and not initial.warnings
    prediction=CombatSimulator(initial).apply(initial,Operation('play_s0','e'))
    assert prediction.state.energy==3 and prediction.state.triggered_relics[0].counter==0
    actual=deepcopy(snapshot);p=actual['state']['player']
    p['hand']=[c for c in p['hand'] if c['instance_id']!='s0']
    p['energy']=3;actual['state']['enemies'][0]['hp']-=6
    p['relics'][0]['runtime_state']['fields']['_attacksPlayed']=10
    assert normalize(actual).triggered_relics==prediction.state.triggered_relics
    assert compare_transition(prediction,actual)['numeric_fields_match']
    p['relics'][0]['runtime_state']['fields']['_attacksPlayed']=9
    assert not compare_transition(prediction,actual)['numeric_fields_match']
    assert relic_projection(p['relics'],prediction.state.triggered_relics)==relic_projection(player['relics'],initial.triggered_relics)


@pytest.mark.local_knowledge
@pytest.mark.parametrize('field,power',[
    ('regeneration','POWER.REGEN_POWER'),('plating','POWER.PLATING_POWER'),('ritual','POWER.RITUAL_POWER'),
    ('next_turn_block','POWER.BLOCK_NEXT_TURN_POWER'),('next_turn_energy','POWER.ENERGY_NEXT_TURN_POWER'),
    ('next_turn_stars','POWER.STAR_NEXT_TURN_POWER'),('next_turn_draw','POWER.DRAW_CARDS_NEXT_TURN_POWER'),
    ('dexterity_delta','POWER.DEXTERITY_POWER')])
def test_new_numeric_effects_require_actual_matching_observation(field,power):
    snapshot=combat();snapshot['state']['player']['relics']=[]
    initial=normalize(snapshot);prediction=Transition(replace(initial,**{field:2}))
    assert not compare_transition(prediction,snapshot)['numeric_fields_match']
    snapshot['state']['player']['creature']['powers']=[dict(entity_id=power,amount=2)]
    assert compare_transition(prediction,snapshot)['numeric_fields_match']


@pytest.mark.local_knowledge
def test_generated_hand_actions_never_escape_into_executable_real_batch():
    from autoslay.knowledge_base.templates import index
    from autoslay.planning.combat_batches import analyze
    from autoslay.planning.search import SearchBudget
    from autoslay.evaluation.combat import CombatPolicy
    snapshot=combat();player=snapshot['state']['player'];player['relics']=[]
    source=deepcopy(player['hand'][0]);source.update(entity_id='CARD.COLLISION_COURSE',energy_cost=0,base_energy_cost=0,
                                                   description='Deal 10 damage. Add a Debris to your hand.')
    player['hand']=[source];player['deck']=[source];player['draw_composition']=[]
    actions(snapshot)
    report=analyze(snapshot,index()[0],CombatPolicy(),dict(horizon=1,branches=4),
                   SearchBudget(max_nodes=150,time_ms=1000,max_depth=3,top_k=5))
    real={a['action_id'] for a in snapshot['state']['legal_actions']}
    assert report['top_batches']
    for batch in report['top_batches']:
        assert all(step['action_id'] in real for step in batch['steps'])
        assert all('generated:' not in step['action_id'] for step in batch['steps'])
