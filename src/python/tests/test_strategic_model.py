import json
from dataclasses import asdict, replace
import pytest
from autoslay.evaluation.run import RunContext
from autoslay.evaluation.route import evaluate_routes
from autoslay.evaluation.strategic import (RunResources, ValueContract, resource_value,
    settle_victory, strategic_context, summarize_outcomes)
from autoslay.evaluation.combat_resources import DEFAULTS
from autoslay.decision.options import decision_options
from autoslay.agent.contracts import prompt


def chain(*kinds):
    return {'nodes':[dict(node_id=str(i),type=kind,available=i==0,action_id='go' if i==0 else None,
                          children=[str(i+1)] if i<len(kinds)-1 else []) for i,kind in enumerate(kinds)]}


def test_route_resources_are_sequential_and_order_matters():
    run=RunContext(30,80,100)
    row=evaluate_routes(chain('RestSite','RestSite'),run)['candidates'][0]
    assert row['resource_trace'][0]['central']['after']['hp']==54
    assert row['resource_trace'][1]['central']['before']['hp']==54
    assert row['scenario_outcomes']['central']['hp']==78
    first=evaluate_routes(chain('Elite','RestSite'),RunContext(12,80,100))['candidates'][0]
    second=evaluate_routes(chain('RestSite','Elite'),RunContext(12,80,100))['candidates'][0]
    assert first['scenario_outcomes']['central']['dead']
    assert not second['scenario_outcomes']['central']['dead']
    assert not first['known_fatal']  # scenario loss is not a known game lethal


def test_relic_healing_is_capped_once_per_survived_room():
    view=chain('Monster','Monster');run=RunContext(80,80,100)
    plain=evaluate_routes(view,run)['candidates'][0]
    relic=evaluate_routes(view,run,relic_ids=['RELIC.BURNING_BLOOD'])['candidates'][0]
    assert plain['scenario_outcomes']['central']['hp']==64
    assert relic['scenario_outcomes']['central']['hp']==76
    assert relic['resource_trace'][0]['low_loss']['victory_heal']==3
    dead=RunResources(0,80,100,dead=True)
    assert settle_victory(dead,6)==dead


def test_full_hp_rest_depends_on_explicit_upgrade_opportunity():
    view=chain('RestSite');run=RunContext(80,80,0)
    assert evaluate_routes(view,run)['candidates'][0]['utility']==0
    assert evaluate_routes(view,run,upgrade_value=7)['candidates'][0]['utility']==7


def test_route_entry_coverage_and_budget_do_not_depend_on_node_enumeration():
    nodes=[]
    for i in range(7):nodes.append(dict(node_id=str(i),type='Unknown',available=True,children=[],action_id='go'+str(i)))
    one=evaluate_routes({'nodes':nodes},RunContext(80,80,0),max_nodes=3)
    two=evaluate_routes({'nodes':list(reversed(nodes))},RunContext(80,80,0),max_nodes=3)
    assert one==two and one['nodes']==3 and len(one['candidates'])==7
    assert sum(c['evaluated'] for c in one['entry_coverage'])==3
    assert one['search_truncated']


def test_route_search_cutoff_retains_evaluated_prefix_only():
    row=evaluate_routes(chain('Monster','Elite','Boss'),RunContext(80,80,100),max_nodes=1)
    assert row['candidates'][0]['path']==['0'] and row['search_truncated']
    assert row['candidates'][0]['tail_unmodeled']
    assert row['scenario_probabilities'] is None and row['candidates'][0]['expected_utility'] is None


def test_scenario_inputs_change_smooth_resource_values_without_probability_thresholds():
    run=RunContext(80,80,0)
    a=evaluate_routes(chain('Monster'),run,scenarios={'central':dict(Monster=1,Elite=2,Boss=3)})
    b=evaluate_routes(chain('Monster'),run,scenarios={'central':dict(Monster=2,Elite=2,Boss=3)})
    assert a['candidates'][0]['utility']>b['candidates'][0]['utility']
    assert a['scenario_probabilities'] is None


def test_same_action_merges_atomic_sequence_and_relic_evidence_without_adding_scores():
    snapshot={'state':{'phase':'event','legal_actions':[dict(action_id='pick',allows_no_target=True)]}}
    report=dict(event={'candidates':[dict(action_id='pick',utility=2)]},
                event_sequence={'candidates':[dict(first_action_id='pick',utility=5)]},
                relic={'candidates':[dict(action_id='pick',utility=None)]})
    option=decision_options(report,snapshot)[0]
    assert len(decision_options(report,snapshot))==1
    assert [a['group'] for a in option['analyses']]==['event','event_sequence','relic']
    assert option['candidate']['utility']==2
    text=prompt(dict(decision_options=[option]))
    assert 'Never assign zero' in text and 'never add their scores' in text


def test_route_uses_only_current_legal_action_bindings():
    snapshot={'state':{'phase':'map','legal_actions':[dict(action_id='current',allows_no_target=True)]}}
    report={'route':{'candidates':[dict(first_action_id='stale'),dict(first_action_id='current',utility=8)]}}
    options=decision_options(report,snapshot)
    assert len(options)==1 and options[0]['group']=='route'
    assert options[0]['steps'][0]['action_id']=='current'


def leaf(state,weight,potions_used=0,**extra):
    return dict(resources=asdict(state),weight=weight,potions_used=potions_used,contract=asdict(ValueContract()),**extra)


def test_joint_results_preserve_correlated_inventory_death_and_unknown_mass():
    rows=[leaf(RunResources(60,80,0,()),.3,1),
          leaf(RunResources(10,80,0,('p',)),.4),
          leaf(RunResources(0,80,0,('p',),dead=True),.1)]
    summary=summarize_outcomes(rows,ValueContract(),unknown_mass=.2)
    assert summary['represented_mass']==pytest.approx(.8)
    assert summary['death_mass']==pytest.approx(.1)
    assert summary['expected_potions_used']==pytest.approx(.3)
    assert summary['unknown_mass']==.2
    assert rows[0]['resources']['potions']==() and rows[1]['resources']['potions']==('p',)
    assert summary['independent_sample_count'] is None
    with pytest.raises(ValueError,match='mass'):summarize_outcomes(rows,ValueContract())
    rows[0]['contract']['value_version']='different'
    with pytest.raises(ValueError,match='Incompatible'):summarize_outcomes(rows,ValueContract(),.2)
    rows[0]['contract']=asdict(replace(ValueContract(),valuation_inputs_sha256='different-parameters'))
    with pytest.raises(ValueError,match='Incompatible'):summarize_outcomes(rows,ValueContract(),.2)


def test_shared_ledger_healing_and_death_values_have_one_boundary():
    state=RunResources(78,80,20)
    healed=settle_victory(state,6)
    route=dict(reserve_fraction=.4,rest_weight=0)
    assert healed.hp==80
    assert resource_value(healed,DEFAULTS,route)-resource_value(state,DEFAULTS,route)>0
    assert resource_value(replace(state,hp=0,dead=True),DEFAULTS,route)==ValueContract().death_value


def test_strategic_context_is_copied_and_map_fingerprint_changes():
    snapshot={'state':{'run_id':'run','player':{'creature':dict(hp=80,max_hp=80),'gold':0},'map':chain('Monster')}}
    first=strategic_context(snapshot)
    snapshot['state']['map']['nodes'][0]['type']='Elite'
    assert first['map']['nodes'][0]['type']=='Monster'
    assert first['map_sha256']!=strategic_context(snapshot)['map_sha256']


def test_relic_route_component_stays_separate_from_acquisition_costs():
    from autoslay.evaluation.relics import evaluate_relic_options
    from autoslay.knowledge_base.templates import index
    snapshot={'state':{'player':{'creature':dict(hp=40,max_hp=80),'gold':0},'map':chain('Monster','Monster')}}
    rows=evaluate_relic_options([dict(relic_id='RELIC.BURNING_BLOOD',action_id='pick')],
                               strategic_context(snapshot),RunContext(40,80,0),index()[0])['candidates']
    assert rows[0]['utility'] is None
    assert rows[0]['route_components'][0]['scenario_deltas']['central']>0


def test_combat_joint_leaf_report_and_experimental_budget():
    from test_combat_batches import combat
    from autoslay.planning.combat_batches import analyze
    from autoslay.knowledge_base.templates import index
    from autoslay.evaluation.combat import CombatPolicy
    from autoslay.planning.search import SearchBudget
    report=analyze(combat(),index()[0],CombatPolicy(),dict(horizon=1,branches=4,fair_candidate_budget=True),
                   SearchBudget(max_nodes=80,time_ms=1000,max_depth=2,top_k=10))
    assert report['budget_allocation']=='equal_remaining_share'
    json.dumps(report,allow_nan=False)
    for row in report['top_batches']:
        assert sum(o['weight'] for o in row['joint_outcomes'])==pytest.approx(1)
        assert row['outcome_summary']['expected_potions_used']==pytest.approx(row['expected_potions_used'])
        assert row['evaluation_budget']['nodes']<=row['evaluation_budget']['node_allowance']
        assert all(o['boundary'] in {'combat_cutoff','dead','post_victory_heal_pre_rewards'} for o in row['joint_outcomes'])


def test_combat_route_context_does_not_splice_exclusive_rest_and_elite_paths():
    from autoslay.evaluation.combat_resources import route_context
    view={'nodes':[dict(node_id='rest',type='RestSite',available=True,children=[]),
                   dict(node_id='elite',type='Elite',available=True,children=[])]}
    result=route_context({'state':{'map':view}},DEFAULTS)
    assert result['pressure']==1 and result['nearest_possible_rest'] is None
    assert result['selected_path']==['elite'] and len(result['route_scenarios'])==2
