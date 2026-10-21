from copy import deepcopy
from dataclasses import replace
import json
import pytest
from autoslay.decision.contracts import Potion
from autoslay.evaluation.resources import item_value,potion_proxy
from autoslay.evaluation.potion_value import reserve_cost
from autoslay.evaluation.run import RunContext,RunPolicy
from autoslay.knowledge_base.templates import index
from autoslay.rules.registry import KnowledgeRules,EntityMechanics,Trigger,EffectSpec


def bottle(identity='POTION.BLOCK_POTION',**kwargs):
    return Potion('b',identity,'Gain 999 Block.','use_potion_b',(),**kwargs)


def snapshot(hp=40):
    return dict(game_version='0.111.0',state=dict(player=dict(creature=dict(hp=hp,max_hp=80),gold=0,potions=[])))


def custom(effects):
    return KnowledgeRules(definitions={'POTION.TEST':EntityMechanics('potion',
        (Trigger('on_use',tuple(EffectSpec(op,n) for op,n in effects)),),target_type='Self')},
        references={'POTION.TEST':dict(category='potion')})


def test_declared_potion_numbers_ignore_description_and_use_instance_variables():
    entries=index()[0];policy=RunPolicy();context=RunContext(40,80,0)
    first=item_value(dict(entity_id='POTION.BLOCK_POTION',description='Gain 999 Block.'),context,[],entries,policy)
    assert first['utility']==12*policy.block_weight
    changed=deepcopy(entries)
    next(v for v in changed['POTION.BLOCK_POTION']['dynamic_variables'] if v['name']=='Block')['base_value']=21
    assert item_value(dict(entity_id='POTION.BLOCK_POTION'),context,[],changed,policy)['utility']==21*policy.block_weight
    observed=potion_proxy(bottle(dynamic_variables=(('Block',7),)),context,changed,policy)
    assert observed['utility']==7*policy.block_weight


def test_missing_or_random_effects_never_gain_a_description_score():
    entries=index()[0]
    entries=dict(entries,**{'POTION.UNDECLARED':dict(category='potion',description='Gain 999 Block.')})
    for identity in ('POTION.UNDECLARED','POTION.POWER_POTION','POTION.FLEX_POTION'):
        result=item_value(dict(entity_id=identity,description='Gain 999 Block.'),RunContext(40,80,0),[],entries,RunPolicy())
        assert result['utility'] is None and result['semantic_analysis']['unpriced']


def test_resource_effect_order_uses_common_clamps_without_double_counting():
    rules=custom([('gain_max_hp',5),('heal',10)])
    first=potion_proxy(bottle('POTION.TEST'),RunContext(78,80,0),rules.references,RunPolicy(),rules=rules)
    assert first['after']['hp']==85 and first['after']['max_hp']==85
    assert [t['operation'] for t in first['command_trace']]==['gain_max_hp','heal']
    assert first['utility']==pytest.approx(7*RunPolicy().hp_weight*RunContext(78,80,0).urgency+5)
    reordered=custom([('heal',10),('gain_max_hp',5)])
    second=potion_proxy(bottle('POTION.TEST'),RunContext(40,80,0),reordered.references,RunPolicy(),rules=reordered)
    assert second['after']['hp']==55
    assert second['resource_sensitive']


def test_unknown_hp_hooks_stop_the_resource_sequence():
    rules=custom([('lose_hp',5),('heal',10)])
    report=potion_proxy(bottle('POTION.TEST'),RunContext(40,80,0),rules.references,RunPolicy(),rules=rules,
        relics=({'entity_id':'RELIC.UNKNOWN'},))
    assert report['utility'] is None and report['unpriced']
    assert [t['operation'] for t in report['command_trace']]==['lose_hp']
    assert report['components'][1]['utility'] is None


def test_reserve_fallback_uses_declared_numbers_and_rejects_hp_sensitive_values():
    entries=index()[0];s=snapshot();p=bottle(dynamic_variables=(('Block',7),))
    result=reserve_cost(s,p,dict(costs={}),entries=entries)
    assert result['known'] and result['upper']==7*RunPolicy().block_weight
    changed=replace(p,description='Gain 100000 Block.')
    assert reserve_cost(s,changed,dict(costs={}),entries=entries)['upper']==result['upper']
    for identity in ('POTION.UNDECLARED','POTION.POWER_POTION','POTION.BLOOD_POTION'):
        assert not reserve_cost(s,bottle(identity),dict(costs={}),entries=entries)['known']
    assert not reserve_cost(snapshot(79),bottle('POTION.BLOOD_POTION'),dict(costs={}),entries=entries)['known']


def test_default_reserve_intervals_have_one_config_source(monkeypatch):
    from autoslay import configuration
    monkeypatch.setattr(configuration,'decision_config',lambda _:dict(potion_reserve=dict(
        costs={'POTION.BLOCK_POTION':dict(lower=2,upper=3)},substitute_discount=.5)))
    s=snapshot();s['state']['player']['potions']=[dict(entity_id='POTION.BLOCK_POTION')]*2
    report=reserve_cost(s,bottle())
    assert (report['lower'],report['upper'])==(1,1.5)
    json.dumps(report,allow_nan=False)


def test_shop_default_entry_uses_observed_potion_variables_instead_of_description():
    from test_deterministic import live
    from autoslay.decision.unified import analyze_decision
    value=live();state=value['state'];state.update(phase='shop',pending_combat_action=None)
    player=state['player'];player.update(gold=100,potions=[],potion_slots=2,relics=[])
    player['deck']=[dict(entity_id='CARD.STRIKE_IRONCLAD',instance_id='a',upgrade_level=0)]
    state['shop']=dict(items=[dict(item_id='p',type='MerchantPotionEntry',cost=0,base_cost=0,stocked=True,
        available=True,action_id='buy_p',details=dict(entity_id='POTION.BLOCK_POTION',description='Gain 999 Block.',
            dynamic_variables=[dict(name='Block',base_value=7)]))])
    state['legal_actions']=[dict(action_id='buy_p',type='buy_shop_item',allows_no_target=True,allowed_target_ids=[])]
    report=analyze_decision(value,use_cache=False)
    bought=next(c for c in report['shop']['candidates'] if c['purchases'])
    assert bought['resource_utility']==7*RunPolicy().block_weight
    state['shop']['items'][0]['details']['description']='Gain 1 Block.'
    changed=analyze_decision(value,use_cache=False)
    assert next(c for c in changed['shop']['candidates'] if c['purchases'])['resource_utility']==bought['resource_utility']


def test_shop_potion_proxy_starts_after_purchase_hooks(monkeypatch):
    from autoslay.paths import ROOT
    from autoslay.rules.model_store import validate_models
    from autoslay.evaluation.shop import evaluate_shop
    data=json.loads((ROOT/'data/knowledge/models/game-0.111.0.json').read_text('utf-8'))
    relic=next(r for r in data['models'] if r['entity_id']=='RELIC.MEMBERSHIP_CARD')
    relic['run_hooks'].append(dict(event='after_item_purchased',effects=[dict(operation='heal',amount=80)]))
    models=validate_models(data);monkeypatch.setattr('autoslay.rules.model_store.load_models',lambda:models)
    context=RunContext(40,80,100,potion_slots_free=1);policy=RunPolicy()
    report=evaluate_shop([dict(entity_id='CARD.STRIKE_IRONCLAD',instance_id='base')],
        [dict(item_id='blood',category='potion',entity_id='POTION.BLOOD_POTION',price=0,base_price=0)],
        context,index()[0],policy,relics=[dict(entity_id='RELIC.MEMBERSHIP_CARD')])
    bought=next(c for c in report['candidates'] if c['purchases'])
    assert bought['after']['hp']==80
    assert bought['resource_utility']==40*policy.hp_weight*context.urgency
    assert [t['operation'] for t in bought['command_trace']]==['obtain_potion','pay_gold','heal']
