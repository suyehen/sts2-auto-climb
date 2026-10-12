from copy import deepcopy
from dataclasses import replace
import json
import pytest
from autoslay.paths import ROOT
from autoslay.decision.cache import AnalysisCache
from autoslay.decision.contracts import Card,CombatState,Enemy,Potion,Operation
from autoslay.decision.unified import analyze_decision,CACHE
from autoslay.knowledge.templates import index,canonical
from autoslay.planning.lookahead import draw_branches,evaluate_lookahead,templates_for
from autoslay.evaluation.run import RunContext,RunPolicy,deck_value
from autoslay.evaluation.choices import cards_report
from autoslay.evaluation.shop import evaluate_shop
from autoslay.evaluation.events import evaluate_events,parse_visible
from autoslay.evaluation.rest import evaluate_rest
from autoslay.evaluation.route import evaluate_routes

@pytest.fixture
def entries(): return index()[0]

def raw(entity,instance=None,level=0):
    return dict(entity_id='CARD.'+entity,instance_id=instance or entity,upgrade_level=level)

def context(hp=80,gold=100,slots=1): return RunContext(hp,80,gold,potion_slots_free=slots)

def battle(cards=(),draw=(('CARD.STRIKE_IRONCLAD',1),('CARD.DEFEND_IRONCLAD',6)),enemy_hp=100):
    return CombatState(30,80,0,3,tuple(cards),(Enemy('enemy','MONSTER.TEST',enemy_hp,0,0,None,(2,)),),draw,discard_known=True,turn=1)

def snapshot(cards): return {'state':{'player':{'deck':cards}}}

def toy_scenario(): return [dict(name='synthetic_attack_only',future_attacks={'enemy':[2]})]

def test_draw_three_of_seven_keeps_exact_probability_and_card_count(entries):
    templates=templates_for(snapshot([raw('STRIKE_IRONCLAD'),raw('DEFEND_IRONCLAD')]),entries)
    branches,method=draw_branches(battle(),3,templates,8,42)
    assert method=='exact'
    assert sum(p for p,s in branches)==pytest.approx(1)
    assert sum(p for p,s in branches if any(c.entity_id=='CARD.STRIKE_IRONCLAD' for c in s.hand))==pytest.approx(3/7)
    assert all(len(s.hand)==3 and sum(n for _,n in s.draw_composition)==4 for _,s in branches)

def test_unknown_draw_mass_is_preserved(entries):
    templates=templates_for(snapshot([raw('STRIKE_IRONCLAD')]),entries)
    branches,_=draw_branches(battle(),1,templates,8,42)
    assert sum(p for p,s in branches if s is None)==pytest.approx(6/7)
    assert sum(p for p,s in branches)==pytest.approx(1)

def test_full_hand_does_not_consume_draw_pile(entries):
    c=replace(canonical(raw('DEFEND_IRONCLAD'),entries)[0],canonical=True)
    s=replace(battle([replace(c,instance_id=str(i)) for i in range(10)]),draw_composition=(('CARD.STRIKE_IRONCLAD',7),))
    branches,_=draw_branches(s,3,{},8,42)
    assert branches[0][1].draw_composition==s.draw_composition and len(branches[0][1].hand)==10

def test_reshuffle_only_remainders_and_drawn_hand(entries):
    templates=templates_for(snapshot([raw('STRIKE_IRONCLAD'),raw('DEFEND_IRONCLAD')]),entries)
    s=replace(battle(draw=(('CARD.STRIKE_IRONCLAD',1),)),discard_composition=(('CARD.DEFEND_IRONCLAD',3),))
    branches,_=draw_branches(s,3,templates,8,42)
    assert len(branches)==1
    result=branches[0][1]
    assert len(result.hand)==3 and result.draw_composition==( ('CARD.DEFEND_IRONCLAD',1),) and result.discard_composition==()

def test_ambiguous_upgrade_variant_is_not_invented(entries):
    assert 'CARD.STRIKE_IRONCLAD' not in templates_for(snapshot([raw('STRIKE_IRONCLAD','a',0),raw('STRIKE_IRONCLAD','b',1)]),entries)

def test_draw_prefix_continuation_finishes_with_one_in_seven(entries):
    shrug=Card('shrug','CARD.SHRUG_IT_OFF',1,'Self','Gain 8 Block. Draw 1 card.')
    s=battle([shrug],enemy_hp=6)
    data=snapshot([raw('SHRUG_IT_OFF'),raw('STRIKE_IRONCLAD'),raw('DEFEND_IRONCLAD')])
    report=evaluate_lookahead(data,s,[],[{'steps':[dict(action_id='play_shrug',target_id=None)]}],entries,
                              dict(horizon=1,branches=8,time_ms=1000),toy_scenario())
    result=report['candidates'][0]['scenarios'][0]['result']
    assert result['covered_probability']==pytest.approx(1)
    assert result['conditional_metrics']['finished']==pytest.approx(1/7)
    assert result['conditional_metrics']['hp_loss']==0


def test_potion_draw_continuation_finishes_with_three_in_seven(entries):
    potion=Potion('p','POTION.SWIFT_POTION','Draw 3 cards.','use_p',('player',))
    state=replace(battle(enemy_hp=6),potions=(potion,))
    report=evaluate_lookahead(snapshot([raw('STRIKE_IRONCLAD'),raw('DEFEND_IRONCLAD')]),state,[],
         [{'steps':[dict(action_id='use_p',target_id='player')]}],entries,dict(horizon=1,branches=8,time_ms=1000),toy_scenario())
    result=report['candidates'][0]['scenarios'][0]['result']
    assert result['covered_probability']==pytest.approx(1)
    assert result['conditional_metrics']['finished']==pytest.approx(3/7)
    assert result['conditional_metrics']['hp_loss']==0


def test_multiple_draw_operations_keep_distinct_card_instances(entries):
    from autoslay.simulation.engine import CombatSimulator
    templates=templates_for(snapshot([raw('STRIKE_IRONCLAD')]),entries)
    first=draw_branches(battle(draw=(('CARD.STRIKE_IRONCLAD',4),)),1,templates,8,42)[0][0][1]
    second=draw_branches(first,1,templates,8,42)[0][0][1]
    assert len(second.hand)==2 and len({c.instance_id for c in second.hand})==2
    result=CombatSimulator(second).apply(second,Operation('play_'+second.hand[0].instance_id,'enemy'))
    assert len(result.state.hand)==1

def test_two_turn_finishing_scenario(entries):
    strike=Card('strike','CARD.STRIKE_IRONCLAD',1,'AnyEnemy','Deal 6 damage.')
    s=battle([strike],draw=(('CARD.STRIKE_IRONCLAD',1),),enemy_hp=12)
    report=evaluate_lookahead(snapshot([raw('STRIKE_IRONCLAD')]),s,[{'steps':[dict(action_id='play_strike',target_id='enemy')]}],[],entries,
                              dict(horizon=2,branches=8,time_ms=1000,draws=1),toy_scenario())
    result=report['candidates'][0]['scenarios'][0]['result']
    assert result['covered_probability']==1 and result['conditional_metrics']['finished']==1
    assert result['conditional_metrics']['hp_loss']==2

def test_missing_enemy_in_scenario_rejected(entries):
    with pytest.raises(ValueError,match='every living enemy'):
        evaluate_lookahead(snapshot([]),battle(),[],[],entries,{},[dict(name='missing',future_attacks={})])

def test_unknown_choice_unranked_and_baseline_scope_explicit(entries):
    deck=[raw('STRIKE_IRONCLAD'),raw('DEFEND_IRONCLAD')]
    unknown=raw('CUSTOM_UNKNOWN')
    report=cards_report(deck,[unknown],context(),entries)
    assert next(c for c in report['candidates'] if c['kind']=='add')['delta_utility'] is None
    value=deck_value(deck+[unknown],context(),entries)
    assert value['unknown_cards']==['CARD.CUSTOM_UNKNOWN'] and value['score_scope']=='known components only'

def test_low_hp_increases_block_value_and_upgrade_has_exact_variant(entries):
    deck=[raw('STRIKE_IRONCLAD','s'+str(i)) for i in range(5)]
    high=cards_report(deck,[raw('DEFEND_IRONCLAD')],context(),entries)
    low=cards_report(deck,[raw('DEFEND_IRONCLAD')],context(10),entries)
    delta=lambda r: next(c['delta_utility'] for c in r['candidates'] if c['kind']=='add')
    assert delta(low)>delta(high)
    report=cards_report(deck,[deck[0]],context(),entries,mode='upgrade')
    assert report['candidates'][0]['delta_utility']>0
    report=cards_report([raw('STRIKE_IRONCLAD','s',1)],[raw('STRIKE_IRONCLAD','s',1)],context(),entries,mode='upgrade')
    assert report['candidates'][0]['delta_utility'] is None

def test_shop_combinations_respect_gold_and_capacity(entries):
    deck=[raw('STRIKE_IRONCLAD','s'),raw('DEFEND_IRONCLAD','d')]
    items=[dict(item_id='p'+str(i),category='potion',entity_id='POTION.BLOCK_POTION',description='Gain 12 Block.',price=20) for i in range(3)]
    items+=[dict(item_id='unknown',category='card',card=raw('CUSTOM_UNKNOWN'),price=1)]
    report=evaluate_shop(deck,items,context(10,gold=30,slots=1),entries,RunPolicy())
    assert all(c['spent']<=30 and len(c['purchases'])<=1 for c in report['candidates'])
    assert any(c['purchases']==[] for c in report['candidates'])
    assert report['unvalued_items'][0]['item']['item_id']=='unknown'

@pytest.mark.parametrize('description',['50% chance to lose 10 HP.','Lose 10 HP if cursed.','获得20金币，然后战斗。'])
def test_event_residual_conditions_not_assigned_value(description,entries):
    assert parse_visible(description) is None
    report=evaluate_events([dict(description=description)], [raw('STRIKE_IRONCLAD')],context(),entries,RunPolicy())
    assert report['candidates'][0]['utility'] is None

def test_events_heal_clamps_and_fatal_probability_and_incomplete_hover(entries):
    options=[dict(option_id='heal',effects=[dict(kind='heal',amount=100)]),
             dict(option_id='risk',outcomes=[dict(probability=.2,effects=[dict(kind='lose_hp',amount=90)]),dict(probability=.8,effects=[])]),
             dict(option_id='hover',description='Gain 10 Gold.',hover_tips_complete=False),
             dict(option_id='bad',outcomes=[dict(probability=.8,effects=[])])]
    r=evaluate_events(options,[raw('STRIKE_IRONCLAD')],context(70),entries,RunPolicy())
    by={c['option_id']:c for c in r['candidates']}
    assert by['heal']['outcomes'][0]['hp_after']==80
    assert by['risk']['fatal_probability']==.2 and by['risk']['known_fatal']
    assert by['hover']['utility'] is None and by['bad']['utility'] is None

def test_bounded_cache_returns_isolated_values():
    cache=AnalysisCache(1); cache.put('a',{'nested':[1]}); a=cache.get('a'); a['nested'].append(2)
    assert cache.get('a')=={'nested':[1]}
    cache.put('b',{}); assert cache.get('a') is None

def test_cached_analysis_refreshes_live_bindings_and_changes_on_state():
    s=json.loads((ROOT/'data/replays/boss_turn4.json').read_text(encoding='utf-8-sig')); CACHE.clear()
    first=analyze_decision(s)
    other=deepcopy(s); other['session_epoch']='different-session'; other['state_version']=999
    second=analyze_decision(other)
    assert second['cache_hit'] and second['source']['state_version']==999
    assert second['source']['session_epoch']=='different-session' and second['analysis_id']!=first['analysis_id']
    other['state']['player']['creature']['hp']-=1
    assert not analyze_decision(other)['cache_hit']

def test_route_prefers_rest_over_elite_at_low_hp():
    report=evaluate_routes({'nodes':[dict(node_id='rest',type='RestSite',available=True,children=[]),dict(node_id='elite',type='Elite',available=True,children=[])]},context(10))
    assert report['candidates'][0]['path']==['rest']

def test_rest_heal_and_upgrade_comparison(entries):
    report=evaluate_rest([dict(option_id='Heal',available=True,description='Heal 24 HP.',action_id='h'),dict(option_id='Smith',available=True,description='Upgrade a card.',action_id='s')],
                         [raw('STRIKE_IRONCLAD')],context(10),entries,RunPolicy())
    assert report['candidates'][0]['action_id']=='h' and report['candidates'][0]['healed']==24
    assert all(c['utility'] is not None for c in report['candidates'])

def test_eternal_removal_blocked_even_without_live_keywords(entries):
    deck=[raw('STRIKE_IRONCLAD'),raw('ASCENDERS_BANE')]
    result=cards_report(deck,[deck[1]],context(),entries,mode='remove')
    assert result['candidates'][0]['delta_utility'] is None
    result=evaluate_shop(deck,[dict(item_id='removal',category='removal',price=0)],context(),entries,RunPolicy())
    assert all(p.get('remove_instance')!='ASCENDERS_BANE' for c in result['candidates'] for p in c['purchases'])

@pytest.mark.parametrize('phase', ['card_reward_selection','shop','event','rest_site'])
def test_unified_live_shapes_are_read_only(phase):
    s=json.loads((ROOT/'data/replays/boss_turn4.json').read_text(encoding='utf-8-sig'))
    s['state']['phase']=phase
    s['state']['pending_combat_action']=None
    s['state']['reward_screen']=dict(cards=[dict(card=raw('DEFEND_IRONCLAD','offer'),available=True,action_id='pick')])
    s['state']['shop']=dict(items=[dict(item_id='c',type='MerchantCardEntry',cost=0,available=True,details=dict(card=raw('DEFEND_IRONCLAD','offer')),action_id='buy')])
    s['state']['event_screen']=dict(options=[dict(option_id='heal',description='Heal 10 HP.',available=True,action_id='event')])
    s['state']['rest_site']=dict(options=[dict(option_id='Heal',description='Heal 24 HP.',available=True,action_id='rest')])
    report=analyze_decision(s,use_cache=False)
    key={'card_reward_selection':'choices','shop':'shop','event':'event','rest_site':'rest'}[phase]
    assert report['phase']==phase and report[key]['candidates']
    assert not report['execution_authorized'] and not report[key]['execution_authorized']

def test_resident_unified_and_comparison_endpoints(tmp_path,monkeypatch):
    import asyncio
    import httpx
    from types import SimpleNamespace
    from autoslay.execution import resident
    monkeypatch.setattr(resident,'append_record',lambda *args:None)
    monkeypatch.setattr(resident.calibration,'DIRECTORY',tmp_path/'calibration')
    monkeypatch.setattr(resident.decisions,'DIRECTORY',tmp_path/'decisions')
    token=tmp_path/'token'; token.write_text('integration-test-secret')
    monkeypatch.setattr(resident,'load_config',lambda:SimpleNamespace(token_file=str(token)))
    snapshot=json.loads((ROOT/'data/replays/boss_turn4.json').read_text(encoding='utf-8-sig'))
    snapshot['connected']=True
    async def fetch(): return deepcopy(snapshot)
    monkeypatch.setattr(resident,'fetch_state',fetch)
    async def check():
        ready=asyncio.get_running_loop().create_future(); task=asyncio.create_task(resident.serve(0,ready))
        port=await asyncio.wait_for(ready,2)
        try:
            async with httpx.AsyncClient(trust_env=False) as client:
                url=f'http://127.0.0.1:{port}'
                for route,body,key in [('/decision',{},'combat'),('/compare',dict(kind='add',options=[raw('DEFEND_IRONCLAD','offer')]),'candidates')]:
                    response=await client.post(url+route,json=body,headers={'Authorization':'Bearer integration-test-secret'})
                    assert response.status_code==200
                    result=response.json(); assert key in result and result['execution_authorized'] is False
                    if route=='/decision':
                        assert result['calibration_evidence']['archived']
                        chosen=await client.post(url+'/calibration/choice',json=dict(analysis_id=result['analysis_id'],group='shortlist',index=0,reason='test intent'),headers={'Authorization':'Bearer integration-test-secret'})
                        assert chosen.status_code==200 and not chosen.json()['execution_authorized']
                        observed=await client.post(url+'/calibration/observe',json=dict(decision_id=result['analysis_id'],horizon='action'),headers={'Authorization':'Bearer integration-test-secret'})
                        assert observed.status_code==200 and observed.json()['recorded']
                response=await client.post(url+'/compare',json={},headers={'Authorization':'Bearer integration-test-secret'})
                assert response.status_code==400
                snapshot.clear(); snapshot.update(connected=False,error='NOT_CONNECTED')
                response=await client.post(url+'/decision',json={},headers={'Authorization':'Bearer integration-test-secret'})
                assert response.status_code==200 and response.json()['error']=='NOT_CONNECTED'
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(check())
