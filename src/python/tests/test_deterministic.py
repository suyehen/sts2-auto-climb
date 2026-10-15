import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock
import pytest
from autoslay.decision.deterministic import assess,load_policy,CERTIFICATES,source
from autoslay.decision.outcomes import Outcome,compare
from autoslay.execution.deterministic import DeterministicExecutor
from test_execution import snapshot

def live():
    s=snapshot();cert=json.loads(CERTIFICATES.read_text(encoding='utf-8'))
    s.update(connected=True,game_assembly_sha256=cert['game_assembly_sha256'])
    s['state']['control_mode']='auto';s['state']['player']['relics']=[]
    s['state']['enemies'][0].update(entity_id='MONSTER.LEAF_SLIME_M',hp=2)
    for creature in [s['state']['player']['creature'],*s['state']['enemies']]: creature['hidden_power_ids']=[]
    for card in [*s['state']['player'].get('deck',[]),*s['state']['player']['hand']]: card.update(modifiers_complete=True,enchantment_id=None,affliction_id=None)
    s['state']['legal_actions'].append(dict(action_id='end_turn',type='end_turn',allows_no_target=True,allowed_target_ids=[]))
    return s

def test_lethal_cards_require_combat_analysis_instead_of_a_proof_shortcut():
    r=assess({},live())
    assert not r['eligible'] and r['selected_steps'] is None
    assert not any(c['rule_id']=='complete_outcome_comparison' for c in r['checks'])

@pytest.mark.parametrize('change',['unknown_relic','periodic','potion','enemy','power','draw','growth','blocked_damage','stale_game'])
def test_combat_alternatives_do_not_bypass_analysis(change):
    s=live();p=s['state']['player'];e=s['state']['enemies'][0]
    if change=='unknown_relic':p['relics']=[dict(entity_id='RELIC.NEW')]
    if change=='periodic':p['relics']=[dict(entity_id='RELIC.INK_BOTTLE',counter=9)]
    if change=='potion':p['potions']=[dict(entity_id='POTION.NEW')]
    if change=='enemy':e['entity_id']='MONSTER.REVIVING'
    if change=='power':e['powers']=[dict(entity_id='POWER.HARD_TO_KILL_POWER',amount=1)]
    if change in {'draw','growth'}:p['hand'][1].update(entity_id='CARD.SHRUG_IT_OFF' if change=='draw' else 'CARD.FEED',target_type='Self',description='Gain 8 Block. Draw 1 card.')
    if change=='blocked_damage':e['block']=100
    if change=='stale_game':s['game_assembly_sha256']='changed'
    assert not assess({},s)['eligible']

@pytest.mark.parametrize('change',['paused','busy','disconnected'])
def test_wait_conditions(change):
    s=live()
    if change=='paused':s['state']['control_mode']='paused'
    if change=='busy':s['busy']=True
    if change=='disconnected':s['connected']=False
    r=assess({},s);assert not r['eligible'] and r['mode']=='wait'

def test_disabled_rule_and_master_switch():
    p=load_policy();p['deterministic_enabled']=False;assert not assess({},live(),p)['eligible']
    p['deterministic_enabled']=True;p['enabled_rules']=[];assert not assess({},live(),p)['eligible']

def test_only_end_turn_needs_no_damage_or_future_threat_assumption():
    s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:]
    assert assess({},s)['rule_id']=='forced_end_turn'


def reward_to_open():
    s=live();s['state'].update(phase='rewards',room_type='CombatRoom')
    s['state']['reward_screen']=dict(screen_type='NRewardsScreen',rewards=[dict(
        reward_id='reward',type='CardReward',supported=True,available=True,action_id='open')],cards=[])
    s['state']['legal_actions']=[dict(action_id='open',type='claim_reward',allows_no_target=True),
                               dict(action_id='proceed',type='proceed_rewards',allows_no_target=True)]
    p=load_policy();p['enabled_rules']=['open_card_reward']
    return s,p


def test_open_card_reward_is_only_inspection_and_selection_requires_ai():
    s,p=reward_to_open();r=assess({},s,p)
    assert r['eligible'] and r['selected_steps']==[dict(action_id='open',target_id=None)]
    assert r['evidence']['effect']=='open_options_only' and r['evidence']['ai_choice_required_after_open']
    s['state']['phase']='card_reward_selection'
    s['state']['legal_actions']=[dict(action_id='choose',type='choose_reward_card',allows_no_target=True),
                               dict(action_id='skip',type='reward_alternative',allows_no_target=True)]
    assert not assess({},s,p)['eligible']


@pytest.mark.parametrize('change',['unknown_relic','other_reward','unsupported','unavailable','unexpected_action','target','power','wrong_room','changed_game','disabled'])
def test_reward_inspection_does_not_bypass_unreviewed_choices(change):
    s,p=reward_to_open();view=s['state']['reward_screen']
    if change=='unknown_relic':s['state']['player']['relics']=[dict(entity_id='RELIC.UNKNOWN_REWARD_HOOK')]
    if change=='other_reward':view['rewards'].append(dict(type='RelicReward',available=True))
    if change=='unsupported':view['rewards'][0]['supported']=False
    if change=='unavailable':view['rewards'][0]['available']=False
    if change=='unexpected_action':s['state']['legal_actions'].append(dict(action_id='reroll',type='reroll',allows_no_target=True))
    if change=='target':s['state']['legal_actions'][0]['allowed_target_ids']=['other']
    if change=='power':s['state']['player']['creature']['powers']=[dict(entity_id='POWER.UNKNOWN')]
    if change=='wrong_room':s['state']['room_type']='EventRoom'
    if change=='changed_game':s['game_assembly_sha256']='changed'
    if change=='disabled':p['enabled_rules']=[]
    assert not assess({},s,p)['eligible']

def test_unknown_action_blocks_automatic_end_turn():
    s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:]+[dict(action_id='custom',type='custom',allows_no_target=True)]
    assert not assess({},s)['eligible']

def test_only_map_node_and_target_count():
    s=live();s['state']['phase']='map';s['state']['legal_actions']=[dict(action_id='map',type='choose_map_node',allows_no_target=True)]
    assert assess({},s)['rule_id']=='forced_action'
    s['state']['legal_actions'][0]['allowed_target_ids']=['a','b']
    assert not assess({},s)['eligible']

def event():
    s=live();s['state']['phase']='event';s['state']['player']['creature']['hp']=2
    s['state']['event_screen']=dict(entity_id='EVENT.ABYSSAL_BATHS',options=[
        dict(option_id='danger',action_id='danger',available=True,will_kill_player=True,text_key='ABYSSAL_BATHS.pages.ALL.options.LINGER'),
        dict(option_id='exit',action_id='exit',available=True,will_kill_player=False,text_key='ABYSSAL_BATHS.pages.ALL.options.EXIT_BATHS')])
    s['state']['legal_actions']=[dict(action_id=x,type='choose_event_option',allows_no_target=True) for x in ['danger','exit']]
    return s

def test_game_fatal_marker_and_source_checked_safe_exit():
    assert assess({},event())['selected_steps']==[dict(action_id='exit',target_id=None)]

def test_max_hp_heal_before_damage_does_not_infer_death():
    s=event();s['state']['event_screen']['options'][0]['will_kill_player']=False
    assert not assess({},s)['eligible']

def test_unknown_safe_effect_or_death_prevention_blocks():
    s=event();s['state']['event_screen']['options'][1]['text_key']='new_exit'
    assert not assess({},s)['eligible']
    s=event();s['state']['player']['relics']=[dict(entity_id='RELIC.UNKNOWN')]
    assert not assess({},s)['eligible']

def test_modified_combat_state_does_not_enable_a_preflight_shortcut():
    for change in ('enchantment','missing','hidden','deck_growth'):
        s=live();p=s['state']['player']
        if change=='enchantment':p['hand'][0]['enchantment_id']='ENCHANTMENT.NEW'
        if change=='missing':p['hand'][0].pop('modifiers_complete')
        if change=='hidden':p['creature']['hidden_power_ids']=['POWER.INVISIBLE']
        if change=='deck_growth':p.setdefault('deck',[]).append(dict(entity_id='CARD.FEED',modifiers_complete=True))
        assert not assess({},s)['eligible']

def test_select_all_mandatory_stops_when_selection_is_optional():
    s=live();s['state']['phase']='card_selection'
    s['state']['card_selection']=dict(min_select=2,max_select=2,cancelable=False,cards=[
        dict(action_id=x,selected=False,available=True) for x in ('a','b')])
    s['state']['legal_actions']=[dict(action_id=x,type='toggle_selection_card',allows_no_target=True) for x in ('a','b')]
    assert assess({},s)['rule_id']=='mandatory_card_selection'
    s['state']['card_selection']['min_select']=1
    assert not assess({},s)['eligible']

def test_empty_rewards_proceed_without_skipping_existing_reward():
    s=live();s['state']['phase']='rewards';s['state']['reward_screen']=dict(rewards=[])
    s['state']['legal_actions']=[dict(action_id='proceed',type='proceed_rewards',allows_no_target=True)]
    assert assess({},s)['eligible']
    s['state']['reward_screen']['rewards']=[dict(type='Gold')]
    assert not assess({},s)['eligible']

def test_preflight_skips_heuristic_search(monkeypatch):
    from autoslay.decision import unified
    def forbidden(*args,**kwargs): raise AssertionError('unnecessary heuristic search')
    monkeypatch.setattr(unified,'analyze',forbidden)
    s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:]
    r=unified.analyze_decision(s)
    assert r['ai_required'] is False and r['autonomy']['eligible']

def test_exact_vector_comparison_rejects_tradeoff_missing_dimension_and_random():
    o=lambda hp,gold,**kw:Outcome('same_room',(('hp',hp),('gold',gold)),**kw)
    a=o(5,10,complete=True,deterministic=True);b=o(4,10,complete=True,deterministic=True)
    assert compare(a,b,{'hp':1,'gold':1})=='dominates'
    assert compare(b,a,{'hp':1,'gold':1})=='dominated'
    assert compare(a,a,{'hp':1,'gold':1})=='equal'
    assert compare(a,o(4,20,complete=True,deterministic=True),{'hp':1,'gold':1})=='tradeoff'
    assert compare(a,b,{'hp':1})=='unknown'
    assert compare(a,o(4,10),{'hp':1,'gold':1})=='unknown'

def test_durable_one_action_and_duplicate_request(tmp_path):
    async def run():
        s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:];calls=[]
        async def fetch():return deepcopy(s)
        async def submit(request):calls.append(request);return dict(status='completed',request_id=request.request_id)
        ex=DeterministicExecutor(tmp_path,fetch,submit,record=False)
        assert (await ex.execute('same'))['status']=='completed'
        await ex.execute('same');assert len(calls)==1
    asyncio.run(run())

def test_state_change_rejected_before_dispatch(tmp_path):
    async def run():
        s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:];reads=[];calls=[]
        async def fetch():
            reads.append(1);v=deepcopy(s)
            if len(reads)>1:v['state_version']+=1
            return v
        async def submit(request):calls.append(request)
        assert (await DeterministicExecutor(tmp_path,fetch,submit,record=False).execute('stale'))['reason']=='STATE_CHANGED'
        assert not calls
    asyncio.run(run())

def test_interrupted_dispatch_never_replayed(tmp_path):
    async def run():
        s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:];calls=[]
        async def fetch():return deepcopy(s)
        async def submit(request):calls.append(request);raise TimeoutError()
        ex=DeterministicExecutor(tmp_path,fetch,submit,record=False)
        with pytest.raises(TimeoutError):await ex.execute('timeout')
        assert (await ex.execute('timeout'))['status']=='dispatch_pending'
        assert len(calls)==1
    asyncio.run(run())

def test_new_id_blocked_by_unresolved_dispatch_then_status_recovery(tmp_path):
    async def run():
        s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:];calls=[];settled=False
        async def fetch():return deepcopy(s)
        async def submit(request):
            calls.append(request)
            if len(calls)==1:raise TimeoutError()
            return dict(status='completed')
        async def status(request_id):return dict(status='completed' if settled else 'unknown')
        ex=DeterministicExecutor(tmp_path,fetch,submit,record=False,status=status)
        with pytest.raises(TimeoutError):await ex.execute('first')
        assert (await ex.execute('next'))['reason']=='PREVIOUS_ACTION_UNRESOLVED' and len(calls)==1
        settled=True
        assert (await ex.execute('next'))['status']=='completed' and len(calls)==2
        assert (await ex.execute('first'))['recovered_by_status_query'] is True
    asyncio.run(run())

@pytest.mark.parametrize('changed', ['session_epoch', 'run_id', 'same', 'unknown'])
def test_historical_pending_dispatch_only_blocks_its_own_context(tmp_path, changed):
    async def run():
        s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:]
        old_request=dict(request_id='old-action', session_epoch=s['session_epoch'],
                         run_id=source(s)['run_id'], action_id='end_turn')
        if changed in {'session_epoch','run_id'}:old_request[changed]='old-context'
        old=dict(status='dispatch_pending',request=old_request)
        path=tmp_path/'old.json';path.write_text(json.dumps(old),encoding='utf-8')
        if changed=='unknown':s['session_epoch']=None
        async def fetch():return deepcopy(s)
        submit=AsyncMock(return_value=dict(status='completed'))
        status=AsyncMock(return_value=dict(status='unknown'))
        executor=DeterministicExecutor(tmp_path,fetch,submit,record=False,status=status)
        result=await executor.execute('new')
        if changed in {'session_epoch','run_id'}:
            assert result['status']=='completed'
            assert submit.await_count==1 and status.await_count==0
        else:
            assert result['reason']=='PREVIOUS_ACTION_UNRESOLVED'
            assert submit.await_count==0 and status.await_count==1
        assert json.loads(path.read_text(encoding='utf-8'))==old
        assert (await executor.execute('old'))==old
    asyncio.run(run())


def test_local_proof_archived_as_local_choice_not_ai(tmp_path):
    from autoslay.telemetry import decisions
    from autoslay.archive.repository import for_legacy
    async def run():
        s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:]
        async def fetch():return deepcopy(s)
        async def submit(request):return dict(status='completed')
        result=await DeterministicExecutor(tmp_path,fetch,submit).execute('archive')
        repo=for_legacy(decisions.DIRECTORY)
        report=repo.load_event(result['analysis_id'])
        assert report['ai_delivery']=='not sent to AI; local deterministic proof'
        records=repo.catalog()['records'];assert len(records)==1
        assert records[0]['last_choice']['group']=='local_deterministic'
        assert repo.entries(records[0]['archive_id'],kind='local_choice')
        assert repo.entries(records[0]['archive_id'],kind='local_outcome')
    asyncio.run(run())

def test_resident_endpoint_auth_dry_run_and_explicit_execution(tmp_path,monkeypatch):
    import httpx
    from types import SimpleNamespace
    from autoslay.execution import resident
    token=tmp_path/'token';token.write_text('test-secret');s=live();s['state']['legal_actions']=s['state']['legal_actions'][-1:];calls=[]
    monkeypatch.setattr(resident,'load_config',lambda:SimpleNamespace(token_file=str(token)))
    monkeypatch.setattr(resident,'append_record',lambda *args:None)
    async def fetch():return deepcopy(s)
    async def submit(request):calls.append(request);return dict(status='completed')
    monkeypatch.setattr(resident,'fetch_state',fetch)
    monkeypatch.setattr(resident,'DeterministicExecutor',lambda:DeterministicExecutor(tmp_path/'execution',fetch,submit,record=False))
    async def run():
        ready=asyncio.get_running_loop().create_future();task=asyncio.create_task(resident.serve(0,ready))
        port=await ready
        try:
            async with httpx.AsyncClient(trust_env=False) as c:
                url=f'http://127.0.0.1:{port}/autonomy';headers={'Authorization':'Bearer test-secret'}
                assert (await c.post(url,json={'execute':True,'request_id':'unauthorized'})).status_code==401
                assert (await c.post(url,json={},headers=headers)).json()['eligible'] and not calls
                assert (await c.post(url,json={'execute':True},headers=headers)).status_code==400 and not calls
                body={'execute':True,'request_id':'once'}
                assert (await c.post(url,json=body,headers=headers)).json()['status']=='completed'
                await c.post(url,json=body,headers=headers);assert len(calls)==1
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
    asyncio.run(run())

def test_victory_heal_is_compared_by_combat_batches():
    from autoslay.decision.unified import analyze_decision
    s=live();p=s['state']['player'];p['creature'].update(hp=79,max_hp=80)
    p['relics']=[dict(entity_id='RELIC.BURNING_BLOOD')]
    report=analyze_decision(s,use_cache=False)
    assert report['autonomy']['decision_basis']=='local_combat_batch'
    assert report['combat']['batches']['selected_batch']['resource_forecast']['projected_hp_after_heal']==80
