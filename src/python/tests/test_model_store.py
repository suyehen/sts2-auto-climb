"""JSON declarations exercised through default simulation and run entry points."""
from copy import deepcopy
from dataclasses import replace
import json
import pytest
from autoslay.paths import ROOT
from autoslay.knowledge_base import repository
from autoslay.knowledge_base.repository import knowledge_scope
from autoslay.knowledge_base.templates import index,canonical
from autoslay.rules.model_store import load_models,validate_models
from autoslay.rules.registry import KnowledgeRules
from autoslay.rules.run_commands import RunCommands
from autoslay.evaluation.strategic import RunResources
from autoslay.decision.contracts import CombatState,Enemy,Operation,UnsupportedEffect
from autoslay.simulation.engine import CombatSimulator


@pytest.fixture
def model_repository(tmp_path,monkeypatch):
    data=json.loads((ROOT/'data/knowledge/models/game-0.111.0.json').read_text('utf-8'))
    wanted={'CARD.PATTER','CARD.STRIKE_IRONCLAD','RELIC.MANGO','RELIC.POTION_BELT',
            'RELIC.BURNING_BLOOD','RELIC.REGAL_PILLOW','RELIC.MEMBERSHIP_CARD','POTION.FLEX_POTION'}
    data['models']=[r for r in data['models'] if r['entity_id'] in wanted]
    data['flows']=[]
    data['monsters']=[]
    catalog=deepcopy(repository.read_json(repository.dataset_path('catalog')))
    catalog['entries']=[e for e in catalog['entries'] if e['entity_id'] in wanted]
    manifest=dict(schema_version='knowledge-manifest/1.0',datasets=dict(models='models.json',catalog='catalog.json'))
    for name,value in [('manifest.json',manifest),('models.json',data),('catalog.json',catalog)]:
        (tmp_path/name).write_text(json.dumps(value),encoding='utf-8')
    monkeypatch.setattr(repository,'REPOSITORY',repository.Repository(tmp_path))
    return tmp_path,data


def combat():
    entries=index()[0]
    card=canonical(dict(entity_id='CARD.PATTER',instance_id='p'),entries)[0]
    return CombatState(40,80,0,3,(card,),(Enemy('e','MONSTER.TEST',100,0,0,None,()),),())


def test_json_value_and_composition_drive_the_default_simulator(model_repository):
    path,data=model_repository
    initial=combat()
    original=CombatSimulator(initial).apply(initial,Operation('play_p')).state
    assert original.block==8
    row=next(r for r in data['models'] if r['entity_id']=='CARD.PATTER')
    row['triggers'][0]['effects'][0]['amount']=19
    row['triggers'][0]['effects'].append(dict(operation='energy',amount=2))
    before=load_models().fingerprint
    (path/'models.json').write_text(json.dumps(data),encoding='utf-8')
    after=CombatSimulator(initial).apply(initial,Operation('play_p')).state
    assert (after.block,after.energy)==(19,4)
    assert load_models().fingerprint!=before
    assert initial.block==0 and original.block==8


def test_snapshot_pins_rules_and_returned_rule_view_across_refresh(model_repository):
    path,data=model_repository
    with knowledge_scope() as snapshot:
        rules=KnowledgeRules();before=load_models().fingerprint
        next(r for r in data['models'] if r['entity_id']=='CARD.PATTER')['triggers'][0]['effects'][0]['amount']=31
        (path/'models.json').write_text(json.dumps(data),encoding='utf-8')
        assert load_models().fingerprint==before
        assert rules.card(combat().hand[0],combat()).effects[0].amount==8
    with knowledge_scope() as updated:
        assert snapshot.revision!=updated.revision
        assert KnowledgeRules().card(combat().hand[0],combat()).effects[0].amount==31
    assert rules.card(combat().hand[0],combat()).effects[0].amount==8


@pytest.mark.parametrize('change,match',[
    (lambda d:d['models'].append(deepcopy(d['models'][0])),'duplicate'),
    (lambda d:d.update(game_version='old'),'version'),
    (lambda d:d['models'][0].update(category='event'),'category'),
    (lambda d:d['models'][0]['triggers'][0]['effects'][0].update(operation='invented'),'implemented'),
    (lambda d:d['models'][0]['triggers'][0]['effects'][0].update(amount={'visible':'block'}),'expression'),
    (lambda d:d['models'][0]['triggers'][0]['effects'][0].update(amount={'state':'private_rng'}),'state'),
    (lambda d:d['models'][0]['triggers'][0]['effects'][0].update(effect_model='POWER.INVENTED'),'effect_model'),
])
def test_invalid_definitions_fail_before_execution(model_repository,change,match):
    _,data=model_repository
    change(data)
    with pytest.raises(ValueError,match=match):validate_models(data)


def test_models_and_nested_effects_are_immutable(model_repository):
    model=load_models()
    with pytest.raises(TypeError):model.records['CARD.PATTER']['category']='potion'
    with pytest.raises(TypeError):model.definitions['CARD.PATTER'].triggers[0].effects[0].amount['variable']='wrong'


def test_variable_reference_and_actual_power_remain_distinct(model_repository):
    record=load_models().records['POTION.FLEX_POTION']
    effect=record['triggers'][0]['effects'][0]
    assert effect['amount']=={'variable':'StrengthPower'}
    assert effect['effect_model']=='POWER.FLEX_POTION_POWER'


@pytest.mark.parametrize('source',['event','shop','reward','treasure'])
def test_obtain_uses_one_hook_order_and_never_mutates_other_branches(model_repository,source):
    initial=RunResources(40,80,150,potion_capacity=0)
    commands=RunCommands()
    mango=commands.execute(initial,dict(operation='obtain_relic',entity_id='RELIC.MANGO'),source=source)
    assert (mango.state.hp,mango.state.max_hp)==(54,94)
    belt=commands.execute(initial,dict(operation='obtain_relic',entity_id='RELIC.POTION_BELT'),source=source)
    assert belt.state.potion_capacity==2 and not initial.relics
    assert [t['operation'] for t in mango.trace]==['obtain_relic','gain_max_hp']


def test_price_changes_after_obtain_and_stock_is_consumed(model_repository):
    commands=RunCommands()
    initial=RunResources(40,80,100,potion_capacity=0,shop_stock=('discount','card'))
    discount=dict(item_id='discount',category='relic',entity_id='RELIC.MEMBERSHIP_CARD',price=50,base_price=50)
    after=commands.purchase(initial,discount)
    assert after.state.gold==50 and after.state.shop_stock==('card',)
    item=dict(item_id='card',category='card',price=80,base_price=80,
        card=dict(entity_id='CARD.STRIKE_IRONCLAD',instance_id='bought'))
    assert commands.quote(after.state,item)==(40,())
    bought=commands.purchase(after.state,item)
    assert bought.state.gold==10 and len(bought.state.deck)==1
    assert [t['operation'] for t in bought.trace]==['add_to_deck','pay_gold']
    with pytest.raises(UnsupportedEffect,match='stock'):commands.purchase(after.state,discount)
    assert initial.gold==100 and not initial.deck


def test_unknown_purchase_step_stops_payment_and_stock_continuation(model_repository,monkeypatch):
    from autoslay.rules.run_commands import RunTransition
    commands=RunCommands();original=commands.execute
    initial=RunResources(40,80,100,deck=({'entity_id':'CARD.STRIKE_IRONCLAD','instance_id':'c'},),shop_stock=('remove',))
    def interrupted(state,command,*,source):
        if command['operation']=='remove_card':return RunTransition(state,gaps=('unmodeled_before_remove',))
        return original(state,command,source=source)
    monkeypatch.setattr(commands,'execute',interrupted)
    result=commands.purchase(initial,dict(item_id='remove',category='removal',price=50),remove_instance='c')
    assert result.gaps and result.state==initial and not result.trace


def test_unknown_price_hooks_do_not_spend_an_assumed_amount(model_repository):
    initial=RunResources(40,80,100,potion_capacity=1,shop_stock=('potion',),relics=({'entity_id':'RELIC.UNKNOWN'},))
    result=RunCommands().purchase(initial,dict(item_id='potion',category='potion',entity_id='POTION.FLEX_POTION',price=25))
    assert result.state==initial and result.gaps and not result.trace


def test_rest_and_combat_use_shared_healing_and_data_parameters(model_repository):
    commands=RunCommands()
    state=RunResources(40,81,0,relics=({'entity_id':'RELIC.REGAL_PILLOW'},))
    assert commands.rest(state).state.hp==79  # floor(24.3 + 15)
    assert commands.rest(state,observed_heal=39).state.hp==79  # no second modifier
    dead=RunResources(0,81,0,dead=True,relics=state.relics)
    assert commands.rest(dead).state==dead


def test_description_never_changes_default_card_execution(model_repository):
    initial=combat()
    initial=replace(initial,hand=(replace(initial.hand[0],description='Gain 999 Block.'),))
    assert CombatSimulator(initial).apply(initial,Operation('play_p')).state.block==8


def test_missing_catalog_binding_reports_entity_variant_and_dependency(model_repository):
    path,_=model_repository
    catalog=json.loads((path/'catalog.json').read_text('utf-8'))
    next(e for e in catalog['entries'] if e['entity_id']=='CARD.PATTER')['variants'][0]['dynamic_variables']=[]
    (path/'catalog.json').write_text(json.dumps(catalog),encoding='utf-8')
    with pytest.raises(ValueError,match=r'CARD.PATTER: variants\[0\].Block'):
        load_models()


def test_catalog_version_is_checked_at_the_same_pinned_revision(model_repository):
    path,_=model_repository
    with knowledge_scope():
        before=load_models().fingerprint
        catalog=json.loads((path/'catalog.json').read_text('utf-8'))
        catalog['game_assembly_sha256']='0'*64
        (path/'catalog.json').write_text(json.dumps(catalog),encoding='utf-8')
        assert load_models().fingerprint==before
    with pytest.raises(ValueError,match='catalog: game version or assembly hash mismatch'):load_models()
