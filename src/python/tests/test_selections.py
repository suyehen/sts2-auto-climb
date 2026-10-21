"""Source-bound selector effects, actual combinations and one-action UI plans."""
from copy import deepcopy
from dataclasses import replace
from types import MappingProxyType
import json
import pytest
from autoslay.paths import ROOT
from autoslay.rules.model_store import load_models,validate_models
from autoslay.rules.selections import SelectionRules,validate_selections
from autoslay.evaluation.selections import evaluate_selection
from autoslay.evaluation.run import RunContext,RunPolicy
from autoslay.knowledge_base.templates import index
from autoslay.decision.contracts import UnsupportedEffect


def snapshot(rule='SELECTION.REST_SITE_SMITH',count=1):
    from test_deterministic import live
    value=live();state=value['state'];state['phase']='card_selection'
    player=state['player'];player['gold']=325;player['creature'].update(hp=49,max_hp=80,powers=[])
    deck=[dict(entity_id='CARD.'+name,instance_id=str(i),upgrade_level=0) for i,name in enumerate(
        ('STRIKE_IRONCLAD','DEFEND_IRONCLAD','BASH'))]
    player['deck']=deepcopy(deck);player['relics']=[]
    declaration=load_models().selections[rule]
    semantics=dict(schema_version='observed-selection/1',source_instance_id='source-1',
        **{'source_'+k:v for k,v in declaration['source'].items()},
        operation={'upgrade_card':'upgrade','remove_card':'remove'}[declaration['selected_operation']],
        api_path=[declaration['selection_api'],'CardSelectCmd.FromDeckGeneric'],
        source_dynamic_variables=[dict(name='EmotionalAwarenessCost',value=125.9,int_value=125),
            dict(name='ArachnidAcupunctureCost',value=250,int_value=250)])
    selection=dict(screen_id='token1',screen_type='ArbitraryClass',prompt='Text is deliberately wrong: exhaust 999 cards',
        min_select=count,max_select=count,cancelable=False,require_manual_confirmation=True,preview=False,
        decision_ready=True,interaction='toggle',semantics=semantics,
        cards=[dict(card=card,eligible=True,selected=False,available=True,action_id='toggle_'+card['instance_id']) for card in deck],controls=[])
    state['card_selection']=selection
    state['legal_actions']=[dict(action_id=r['action_id'],type='toggle_selection_card',allows_no_target=True,allowed_target_ids=[]) for r in selection['cards']]
    state.pop('pending_combat_action',None)
    return value


def evaluate(value,**kwargs):
    p=value['state']['player'];c=p['creature']
    return evaluate_selection(value,RunContext(c['hp'],c['max_hp'],p['gold']),index()[0],RunPolicy(),**kwargs)


def test_smith_uses_combinations_not_independent_single_card_ranks():
    value=snapshot(count=2);before=deepcopy(value)
    report=evaluate(value)
    assert report['supported'] and report['comparison_complete']
    assert {tuple(c['instances']) for c in report['combinations']}=={('0','1'),('0','2'),('1','2')}
    assert all(sum(c['upgrade_level'] for c in row['after']['deck'])==2 for row in report['combinations'])
    assert all(row['after']['hp']==49 and row['after']['gold']==325 for row in report['combinations'])
    assert value==before and all(row['action_id'].startswith('toggle_') for row in report['candidates'])


def test_remove_two_then_pay_once_in_the_actual_callback_order():
    value=snapshot('SELECTION.ZEN_WEAVER_ARACHNID_ACUPUNCTURE',2)
    report=evaluate(value)
    assert report['supported'] and report['comparison_complete'] and len(report['combinations'])==3
    for row in report['combinations']:
        assert [t['operation'] for t in row['command_trace']]==['remove_card','remove_card','pay_gold']
        assert row['after']['gold']==75 and len(row['after']['deck'])==1
    assert value['state']['player']['gold']==325 and len(value['state']['player']['deck'])==3


def test_cook_removes_both_cards_then_gains_max_hp_and_cancel_skips_everything():
    value=snapshot('SELECTION.REST_SITE_COOK',2)
    before=deepcopy(value)
    report=evaluate(value)
    assert report['comparison_complete'] and len(report['combinations'])==3
    for row in report['combinations']:
        assert [t['operation'] for t in row['command_trace']]==['remove_card','remove_card','gain_max_hp']
        assert len(row['after']['deck'])==1 and row['after']['hp']==54 and row['after']['max_hp']==85
    assert value==before
    selection=value['state']['card_selection'];selection['cancelable']=True
    selection['controls']=[dict(type='close_card_selection',available=True,action_id='cancel')]
    value['state']['legal_actions'].append(dict(action_id='cancel',allows_no_target=True))
    cancel=next(r for r in evaluate(value)['combinations'] if r.get('kind')=='cancel')
    assert cancel['utility']==0 and cancel['after']['hp']==49 and len(cancel['after']['deck'])==3
    assert not cancel['command_trace']


def test_cook_unreviewed_remove_hooks_never_grant_the_max_hp_reward():
    value=snapshot('SELECTION.REST_SITE_COOK',2)
    value['state']['player']['relics']=[dict(entity_id='RELIC.UNKNOWN')]
    report=evaluate(value)
    assert report['supported'] and not report['comparison_complete']
    for row in report['combinations']:
        assert not row['known'] and row['after']['max_hp']==80 and len(row['after']['deck'])==3
        assert not any(t['operation']=='gain_max_hp' for t in row['command_trace'])


def test_public_int_value_drives_payment_without_guessing_from_base_decimal():
    value=snapshot('SELECTION.ZEN_WEAVER_EMOTIONAL_AWARENESS')
    report=evaluate(value)
    assert all(r['after']['gold']==200 for r in report['combinations'])
    value['state']['card_selection']['semantics']['source_dynamic_variables'][0]['int_value']=135
    assert all(r['after']['gold']==190 for r in evaluate(value)['combinations'])
    del value['state']['card_selection']['semantics']['source_dynamic_variables'][0]['int_value']
    assert not evaluate(value)['supported'] and 'Cost' in evaluate(value)['reason']


def test_sapphire_eat_uses_current_post_heal_state_and_does_not_heal_twice():
    value=snapshot('SELECTION.SAPPHIRE_SEED_EAT')
    report=evaluate(value)
    assert report['comparison_complete']
    assert all(r['after']['hp']==49 and r['after']['max_hp']==80 for r in report['combinations'])
    assert all([t['operation'] for t in r['command_trace']]==['upgrade_card'] for r in report['combinations'])


@pytest.mark.parametrize('field,bad',[
    ('source_model_id','EVENT.OTHER'),('source_model_type','Other'),('source_callback','Other'),
    ('source_option_key','Other'),('source_instance_id',None),('operation','remove'),('api_path',[]),
])
def test_wrong_or_missing_source_keeps_selection_unknown(field,bad):
    value=snapshot('SELECTION.SAPPHIRE_SEED_EAT');value['state']['card_selection']['semantics'][field]=bad
    result=evaluate(value)
    assert not result['supported'] and not result['candidates']


def test_screen_names_and_localized_prompts_cannot_supply_missing_semantics():
    value=snapshot();selection=value['state']['card_selection'];selection.pop('semantics')
    selection.update(screen_type='NDeckUpgradeSelectScreen',prompt='Choose a card to upgrade')
    assert not evaluate(value)['supported']
    from autoslay.decision.unified import analyze_decision
    report=analyze_decision(value,use_cache=False)
    assert not report['choices']['supported'] and report['selection_requires_context']
    assert report['decision_options'] and all(r['group']=='legal_options' for r in report['decision_options'])


def test_selection_change_rebinds_to_one_live_toggle_then_preview_or_confirm():
    value=snapshot(count=2);selection=value['state']['card_selection']
    selection['cards'][2]['selected']=True
    result=evaluate(value)
    row=next(c for c in result['combinations'] if c['instances']==['0','1'])
    assert row['action_id']=='toggle_2'  # clear unwanted selection before adding two cards
    selection['cards'][2]['selected']=False
    for i in (0,1):selection['cards'][i]['selected']=True
    selection['controls']=[dict(type='preview_card_selection',available=True,action_id='preview')]
    value['state']['legal_actions'].append(dict(action_id='preview',type='preview_card_selection',allows_no_target=True))
    row=next(c for c in evaluate(value)['combinations'] if c['instances']==['0','1'])
    assert row['action_id']=='preview'
    selection['preview']=True;selection['controls']=[dict(type='confirm_card_selection',available=True,action_id='confirm')]
    value['state']['legal_actions']=[dict(action_id='confirm',type='confirm_card_selection',allows_no_target=True)]
    result=evaluate(value)
    assert len(result['candidates'])==1 and result['candidates'][0]['action_id']=='confirm'
    assert len(result['combinations'])==3


def test_optional_selection_includes_zero_and_smith_cancel_has_no_hook_or_effect():
    value=snapshot();selection=value['state']['card_selection']
    selection.update(min_select=0,cancelable=True)
    selection['controls']=[dict(type='close_card_selection',action_id='close',available=True)]
    value['state']['legal_actions'].append(dict(action_id='close',type='close_card_selection',allows_no_target=True))
    result=evaluate(value)
    cancel=next(c for c in result['combinations'] if c.get('kind')=='cancel')
    assert cancel['known'] and cancel['utility']==0 and not cancel['command_trace']
    assert any(c['instances']==[] and c.get('kind')!='cancel' for c in result['combinations'])


def test_unknown_hooks_stop_before_later_cards_and_payment():
    value=snapshot('SELECTION.ZEN_WEAVER_ARACHNID_ACUPUNCTURE',2)
    value['state']['player']['relics']=[dict(entity_id='RELIC.UNKNOWN')]
    result=evaluate(value)
    assert result['supported'] and not result['comparison_complete']
    assert all(not c['known'] and c['utility'] is None and c['after']['gold']==325 for c in result['combinations'])
    assert all('pay_gold' not in [t['operation'] for t in c['command_trace']] for c in result['combinations'])


def test_smith_hook_runs_once_after_all_upgrades(monkeypatch):
    from autoslay.knowledge_base.repository import freeze
    models=load_models();records=dict(models.records)
    records['RELIC.TEST']=freeze(dict(run_hooks=[dict(event='after_rest_smith',effects=[dict(operation='gain_gold',amount=3)])]))
    monkeypatch.setattr('autoslay.rules.model_store.load_models',lambda:replace(models,records=MappingProxyType(records)))
    value=snapshot(count=2);value['state']['player']['relics']=[dict(entity_id='RELIC.TEST')]
    result=evaluate(value)
    assert all(c['after']['gold']==328 and [t['operation'] for t in c['command_trace']]==['upgrade_card','upgrade_card','gain_gold'] for c in result['combinations'])


def test_rest_smith_forecast_uses_the_same_json_continuation(monkeypatch):
    from autoslay.evaluation.rest import evaluate_rest
    data=json.loads((ROOT/'data/knowledge/models/game-0.111.0.json').read_text('utf-8'))
    next(r for r in data['selections'] if r['entity_id']=='SELECTION.REST_SITE_SMITH')['after_commands']=[dict(operation='gain_max_hp',amount=4)]
    models=validate_models(data)
    monkeypatch.setattr('autoslay.rules.model_store.load_models',lambda:models)
    value=snapshot(count=2);player=value['state']['player']
    report=evaluate_rest([dict(option_id='SMITH',available=True,smith_count=2)],player['deck'],RunContext(49,80,325),index()[0],RunPolicy())
    row=report['candidates'][0]
    assert row['comparison_complete'] and row['selection_model_id']=='SELECTION.REST_SITE_SMITH'
    assert all(c['after']['max_hp']==84 and c['after']['hp']==53 for c in row['upgrade_combinations'])
    assert all([t['operation'] for t in c['command_trace']]==['upgrade_card','upgrade_card','gain_max_hp'] for c in row['upgrade_combinations'])
    from autoslay.evaluation.events import transition_value
    from autoslay.evaluation.strategic import RunResources
    for combination in row['upgrade_combinations']:
        assert combination['utility']==transition_value(RunResources(49,80,325,deck=tuple(player['deck'])),
            RunResources(**combination['after']),RunContext(49,80,325),index()[0],RunPolicy())['utility']


def test_rest_smith_partial_search_never_reports_a_complete_scalar_value():
    from autoslay.evaluation.rest import evaluate_rest
    value=snapshot(count=2);player=value['state']['player'];entries=index()[0]
    report=evaluate_rest([dict(option_id='SMITH',available=True,smith_count=2)],player['deck'],RunContext(49,80,325),entries,RunPolicy(shop_nodes=1))
    row=report['candidates'][0]
    assert row['search_truncated'] and not row['comparison_complete'] and row['utility'] is None
    assert row['known_component_utility'] is not None
    entries=deepcopy(entries)
    entries['CARD.UNKNOWN']=dict(entries['CARD.STRIKE_IRONCLAD'],entity_id='CARD.UNKNOWN')
    player['deck'][0]['entity_id']='CARD.UNKNOWN'
    report=evaluate_rest([dict(option_id='SMITH',available=True,smith_count=2)],player['deck'],RunContext(49,80,325),entries,RunPolicy())
    row=report['candidates'][0]
    assert row['unmodeled_instances']==['0'] and not row['comparison_complete'] and row['utility'] is None


def test_json_continuation_changes_default_decision_and_old_rules_remain_pinned(monkeypatch):
    old=SelectionRules();data=json.loads((ROOT/'data/knowledge/models/game-0.111.0.json').read_text('utf-8'))
    next(r for r in data['selections'] if r['entity_id']=='SELECTION.ZEN_WEAVER_EMOTIONAL_AWARENESS')['after_commands'][0]['amount']=17
    changed=validate_models(data)
    monkeypatch.setattr('autoslay.rules.model_store.load_models',lambda:changed)
    value=snapshot('SELECTION.ZEN_WEAVER_EMOTIONAL_AWARENESS')
    from autoslay.decision.unified import analyze_decision
    report=analyze_decision(value,use_cache=False)
    assert report['choices']['supported'] and all(c['after']['gold']==308 for c in report['choices']['combinations'])
    assert old.models.fingerprint!=changed.fingerprint
    assert any(o['group']=='choices' for o in report['decision_options'])


def test_budget_and_unknown_card_values_never_claim_complete_comparison():
    value=snapshot(count=2)
    result=evaluate(value,max_combinations=1)
    assert result['search_truncated'] and not result['comparison_complete'] and len(result['combinations'])==1
    value['state']['player']['deck'][0]['enchantment_id']='ENCHANTMENT.SHARP'
    assert all(not c['known'] for c in evaluate(value)['combinations'])


def test_nested_combat_selection_requires_api_semantics_and_ignores_localized_prompt():
    from test_combat_batches import combat
    from autoslay.decision.local_combat import select_combat_selection
    value=combat();s=value['state'];s['phase']='card_selection';s['pending_combat_action']={'request_id':'parent'}
    cards=[s['player']['hand'][0],s['player']['hand'][3]]
    s['card_selection']=dict(decision_ready=True,prompt='Upgrade and keep the strongest card',min_select=1,max_select=1,
        semantics=dict(schema_version='observed-selection/1',operation='discard'),cards=[
            dict(card=c,selected=False,available=True,action_id='choose_'+c['instance_id']) for c in cards])
    s['legal_actions']=[dict(action_id=r['action_id'],type='select_combat_card',allows_no_target=True) for r in s['card_selection']['cards']]
    before=select_combat_selection(value)
    assert before and len(before['steps'])==1
    for card in cards:card['description']='Heal 9999 HP and deal 9999 damage.'
    s['card_selection']['prompt']='弃牌、保留、升级全部相反的文案'
    assert select_combat_selection(value)['steps']==before['steps']
    del s['card_selection']['semantics']
    assert select_combat_selection(value) is None
    from autoslay.decision.deterministic import assess
    decision=assess({},value)
    assert decision['mode']=='ai_required' and decision['primary_reason']=='combat_selection_semantics_unmodeled'


def test_unknown_selection_card_does_not_get_a_proxy_from_its_description():
    from autoslay.evaluation.selections import declared_card_proxy
    with pytest.raises(UnsupportedEffect):declared_card_proxy(dict(entity_id='CARD.UNKNOWN',description='Deal 999 damage.'))


def test_unknown_before_remove_hook_preserves_the_deck_and_payment():
    value=snapshot('SELECTION.ZEN_WEAVER_EMOTIONAL_AWARENESS')
    value['state']['player']['relics']=[dict(entity_id='RELIC.UNKNOWN')]
    report=evaluate(value)
    assert all(list(c['after']['deck'])==value['state']['player']['deck'] and c['after']['gold']==325 for c in report['combinations'])


def test_actual_gold_debit_clamps_after_selection_but_purchase_requires_initial_funds():
    value=snapshot('SELECTION.ZEN_WEAVER_EMOTIONAL_AWARENESS')
    value['state']['player']['gold']=50
    assert all(c['after']['gold']==0 and len(c['after']['deck'])==2 for c in evaluate(value)['combinations'])
    from autoslay.rules.run_commands import RunCommands
    from autoslay.evaluation.strategic import RunResources
    with pytest.raises(UnsupportedEffect,match='Insufficient gold'):
        RunCommands().purchase(RunResources(49,80,50),dict(item_id='mango',category='relic',entity_id='RELIC.MANGO',price=250,base_price=250))


def test_selection_packet_bounds_combination_details_but_keeps_full_local_report():
    from autoslay.telemetry.decisions import selection_packet
    value=snapshot(count=2);selection=value['state']['card_selection']
    for i in range(3,9):
        card=dict(entity_id='CARD.STRIKE_IRONCLAD',instance_id=str(i),upgrade_level=0)
        selection['cards'].append(dict(card=card,eligible=True,selected=False,available=True,action_id='toggle_'+str(i)))
        value['state']['player']['deck'].append(card)
        value['state']['legal_actions'].append(dict(action_id='toggle_'+str(i),type='toggle_selection_card',allows_no_target=True))
    report=evaluate(value);packet=selection_packet(report)
    assert len(report['combinations'])==36 and len(packet['combinations'])==12
    assert packet['additional_combinations_local']==24 and packet['combination_count']==36


@pytest.mark.parametrize('change,match',[
    (lambda d:d['selections'].append(deepcopy(d['selections'][0])),'unique'),
    (lambda d:d['selections'][0].update(selected_operation='remove_card'),'mismatch'),
    (lambda d:d['selections'][0]['after_commands'].append(dict(operation='gain_gold',amount={'variable':'Missing'})),'undefined'),
    (lambda d:d['selections'][0].update(after_hooks=['modify_rest_heal']),'lifecycle'),
])
def test_invalid_selection_declarations_rejected_before_use(change,match):
    data=json.loads((ROOT/'data/knowledge/models/game-0.111.0.json').read_text('utf-8'));change(data)
    with pytest.raises(ValueError,match=match):validate_models(data)
