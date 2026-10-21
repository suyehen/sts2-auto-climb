"""Native event command order, public bindings and observation boundaries."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import pytest
from autoslay.paths import ROOT
from autoslay.decision.contracts import UnsupportedEffect
from autoslay.evaluation.strategic import RunResources
from autoslay.evaluation.event_sequences import evaluate_event_sequence
from autoslay.evaluation.run import RunContext,RunPolicy
from autoslay.knowledge_base.templates import index
from autoslay.rules.flows import FlowMachine,validate_flows
from autoslay.rules.model_store import load_models,validate_models
from autoslay.rules.run_commands import RunCommands
from autoslay.rules.registry import IMPLEMENTATION_SHA256


def observed(identity,page,*,variables=(),fields=None):
    record=load_models().flows['EVENT.'+identity]
    return dict(entity_id='EVENT.'+identity,game_assembly_sha256=IMPLEMENTATION_SHA256,
        dynamic_variables=list(variables),runtime_state=dict(fields=fields or {}),
        options=[dict(text_key=o['keys'][0],callback_method=o['callback'],
            callback_declaring_type=record['provenance']['model_type'],source_model_id='EVENT.'+identity,
            action_id=o['id'],description='Arbitrary localized text, 999 HP')
            for o in record['states'][page]['options']])


def deck():
    return tuple(dict(entity_id=e,instance_id=str(n),upgrade_level=0) for n,e in
        enumerate(('CARD.STRIKE_IRONCLAD','CARD.DEFEND_IRONCLAD','CARD.BASH')))


def test_bath_damage_keeps_growing_after_page_counter_cap_and_branch_isolation():
    machine=FlowMachine()
    event=observed('ABYSSAL_BATHS','inside',variables=[dict(name='Damage',value=15)],fields={'_lingerCount':9})
    state,_=machine.bind(event,RunResources(70,80,0))
    exit=machine.apply(state,'ExitBaths')[0]
    linger=machine.apply(state,'Linger')[0]
    again=machine.apply(linger.state,'Linger')[0]
    assert (linger.state.resources.hp,linger.state.resources.max_hp)==(57,82)
    assert (again.state.resources.hp,again.state.resources.max_hp)==(43,84)
    assert again.state.variables['LingerCount']==9 and again.state.variables['Damage']==17
    assert exit.state.resources==state.resources and state.variables['Damage']==15
    event['dynamic_variables']=[]
    with pytest.raises(UnsupportedEffect,match='Damage'):machine.bind(event,state.resources)


@pytest.mark.parametrize('field,value,match',[
    ('callback_method','Wrong','callback/key'),
    ('callback_declaring_type','Wrong','declaring type'),
    ('source_model_id','EVENT.WRONG','source model'),
])
def test_option_bindings_check_callback_and_owner(field,value,match):
    event=observed('ABYSSAL_BATHS','initial');event['options'][0][field]=value
    with pytest.raises(UnsupportedEffect,match=match):FlowMachine().bind(event,RunResources(40,80,0))


def test_tablet_uniform_pool_updates_and_final_upgrade_all():
    machine=FlowMachine()
    event=observed('TABLET_OF_TRUTH','0',variables=[dict(name='DecipherMaxHpLoss',value=3)],fields={'_decipherCount':0})
    state,_=machine.bind(event,RunResources(80,80,0,deck=deck()))
    outcomes=machine.apply(state,'Decipher')
    assert len(outcomes)==3 and [o.weight for o in outcomes]==[1/3]*3
    assert all((o.state.resources.hp,o.state.resources.max_hp)==(77,77) for o in outcomes)
    assert all([t['operation'] for t in o.trace][:2]==['lose_max_hp','upgrade_card'] for o in outcomes)
    next_outcomes=machine.apply(outcomes[0].state,'Decipher')
    assert len(next_outcomes)==2 and all(o.weight==.5 for o in next_outcomes)
    assert all(sum(c['upgrade_level'] for c in o.state.resources.deck)==2 for o in next_outcomes)
    final=replace(state,page='4',variables={'Count':4,'Cost':79,'Heal':20})
    all_upgraded=machine.apply(final,'Decipher')
    assert len(all_upgraded)==1 and all_upgraded[0].state.resources.max_hp==1
    assert [c['upgrade_level'] for c in all_upgraded[0].state.resources.deck]==[1,1,1]
    assert [c['upgrade_level'] for c in state.resources.deck]==[0,0,0]


def test_tablet_lethal_cost_lowers_maximum_then_kills_without_upgrading():
    machine=FlowMachine()
    event=observed('TABLET_OF_TRUTH','1',variables=[dict(name='DecipherMaxHpLoss',value=6)],fields={'_decipherCount':1})
    initial,_=machine.bind(event,RunResources(6,6,0,deck=deck()))
    outcome=machine.apply(initial,'Decipher')[0]
    assert outcome.state.resources.dead and outcome.state.resources.max_hp==1
    assert [t['operation'] for t in outcome.trace]==['lose_max_hp','kill']
    assert not any(c['upgrade_level'] for c in outcome.state.resources.deck)


def test_dense_vegetation_uses_public_gold_exact_decimal_and_mimic_boundary():
    machine=FlowMachine()
    event=observed('DENSE_VEGETATION','initial',variables=[dict(name='Gold',value=87),
        dict(name='Heal',value=24.3,decimal_value='24.300000000000000000000000001')])
    state,_=machine.bind(event,RunResources(30,81,0))
    trudge=machine.apply(state,'TrudgeOn')
    assert len(trudge)==1 and (trudge[0].state.resources.hp,trudge[0].state.resources.gold)==(22,87)
    rest=machine.apply(state,'Rest')[0]
    assert rest.state.resources.hp==54 and rest.trace[0]['source']=='MIMIC_REST.HEAL'
    fight=machine.apply(rest.state,'Fight')[0]
    assert fight.boundary['encounter_id']=='ENCOUNTER.DENSE_VEGETATION_EVENT_ENCOUNTER'
    assert fight.state.resources==rest.state.resources and not fight.gaps
    result=evaluate_event_sequence(event,[],RunContext(30,81,0),index()[0],RunPolicy(),'0.111.0')
    json.dumps(result)
    assert not next(c for c in result['candidates'] if c['option_id']=='Rest')['known']
    event['dynamic_variables']=event['dynamic_variables'][1:]
    with pytest.raises(UnsupportedEffect,match='Gold'):machine.bind(event,state.resources)


def test_decimal_damage_truncates_amount_after_hp_loss_modifier():
    commands=RunCommands()
    plain=RunResources(30,81,0)
    protected=replace(plain,relics=({'entity_id':'RELIC.TUNGSTEN_ROD'},))
    assert commands.execute(plain,dict(operation='damage',amount=Decimal('8.9')),source='test').state.hp==22
    assert commands.execute(protected,dict(operation='damage',amount=Decimal('8.9')),source='test').state.hp==23
    result=commands.execute(protected,dict(operation='lose_max_hp',amount=Decimal('8.9')),source='test')
    assert (result.state.hp,result.state.max_hp)==(30,72)
    full=replace(protected,hp=81)
    result=commands.execute(full,dict(operation='lose_max_hp',amount=Decimal('8.9')),source='test')
    assert (result.state.hp,result.state.max_hp)==(72,72)


def test_hp_loss_hooks_run_before_the_maximum_is_set_and_only_once():
    commands=RunCommands()
    from types import SimpleNamespace
    commands.models=SimpleNamespace(records={'RELIC.TEST':dict(run_hooks=[dict(event='after_hp_loss',
        effects=[dict(operation='gain_gold',amount={'state':'max_hp'})])])})
    state=RunResources(80,80,0,relics=({'entity_id':'RELIC.TEST'},))
    result=commands.execute(state,dict(operation='lose_max_hp',amount=6),source='test')
    assert (result.state.hp,result.state.max_hp,result.state.gold)==(74,74,80)
    assert sum(t['operation']=='gain_gold' for t in result.trace)==1


def test_inexact_float_is_unknown_but_exact_integral_observation_is_accepted():
    machine=FlowMachine()
    event=observed('DENSE_VEGETATION','initial',variables=[dict(name='Gold',value=75),dict(name='Heal',value=24.3)])
    with pytest.raises(UnsupportedEffect,match='Exact decimal'):machine.bind(event,RunResources(30,81,0))
    event['dynamic_variables'][1]['value']=24
    state,_=machine.bind(event,RunResources(30,81,0))
    assert state.variables['Heal']==Decimal(24)


def test_lantern_combat_boundary_does_not_award_special_card_before_combat():
    machine=FlowMachine()
    state,_=machine.bind(observed('THE_LANTERN_KEY','initial'),RunResources(40,80,10,deck=deck()))
    keep=machine.apply(state,'KeepTheKey')[0]
    fight=machine.apply(keep.state,'Fight')[0]
    assert fight.boundary['extra_rewards'][0]['card_id']=='CARD.LANTERN_KEY'
    assert fight.state.resources==state.resources and len(fight.state.resources.deck)==3
    assert machine.apply(state,'ReturnTheKey')[0].state.resources.gold==110


def test_json_change_controls_default_forecast_without_source_or_description_execution(monkeypatch):
    from autoslay.knowledge_base import repository
    original=repository.read_view
    data=json.loads((ROOT/'data/knowledge/models/game-0.111.0.json').read_text('utf-8'))
    next(f for f in data['flows'] if f['entity_id']=='EVENT.THE_LANTERN_KEY')['states']['initial']['options'][0]['commands'][0]['amount']=234
    changed=validate_models(data)
    def read(path,kind,loader):
        if kind=='game-models/1':return changed
        return original(path,kind,loader)
    monkeypatch.setattr('autoslay.rules.model_store.read_view',read)
    event=observed('THE_LANTERN_KEY','initial')
    result=evaluate_event_sequence(event,[],RunContext(40,80,10),index()[0],RunPolicy(),'0.111.0')
    row=next(c for c in result['candidates'] if c['option_id']=='ReturnTheKey')
    assert row['branches'][0]['immediate']['gold_after']==244


@pytest.mark.parametrize('change,match',[
    (lambda f:f['states']['initial']['options'][0].update(next='missing'),'successor'),
    (lambda f:f['states']['initial']['options'][0]['commands'][0].update(encounter_id='ENCOUNTER.MISSING'),'encounter'),
    (lambda f:f['states']['initial']['options'][0]['commands'][0].update(encounter_id='RELIC.MANGO'),'encounter'),
    (lambda f:f['variables'].update(Bad={'required_in':['missing']}),'scope'),
    (lambda f:f['states']['initial']['options'][1]['commands'].append(dict(operation='heal',amount={'state':'energy'})),'scope'),
    (lambda f:f['states']['initial']['options'][0]['commands'].append(dict(operation='gain_gold',amount=10)),'terminate'),
])
def test_invalid_flows_are_rejected(change,match):
    models=load_models()
    flow=deepcopy(dict(models.flows['EVENT.THE_LANTERN_KEY']))
    flow['states']['initial']['options'][0]['commands']=[deepcopy(flow['states']['fight']['options'][0]['commands'][0])]
    change(flow)
    with pytest.raises(ValueError,match=match):validate_flows([flow],models.model_references)


def test_nested_combat_boundary_cannot_silently_drop_enclosing_commands():
    models=load_models();flow=deepcopy(dict(models.flows['EVENT.THE_LANTERN_KEY']))
    fight=deepcopy(flow['states']['fight']['options'][0]['commands'])
    flow['states']['fight']['options'][0]['commands']=[dict(operation='branch',condition=1,then=fight,**{'else':[]}),
        dict(operation='gain_gold',amount=10)]
    with pytest.raises(ValueError,match='enclosing'):validate_flows([flow],models.model_references)


def test_event_capability_points_to_the_same_flow_definition():
    from autoslay.actions import entity_capabilities
    result=entity_capabilities('EVENT.THE_LANTERN_KEY')
    assert result['effects']['status']=='native_definition'
    assert result['effects']['flow']['states']['fight']['options'][0]['commands'][0]['operation']=='enter_combat'
