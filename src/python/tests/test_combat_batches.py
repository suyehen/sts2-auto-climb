import asyncio
from copy import deepcopy
from dataclasses import replace
import pytest
from autoslay.planning.combat_batches import analyze
from autoslay.planning.search import SearchBudget
from autoslay.evaluation.combat import CombatPolicy
from autoslay.knowledge_base.templates import index
from autoslay.decision.deterministic import source,assess
from autoslay.decision.unified import analyze_decision
from autoslay.evaluation.combat_resources import DEFAULTS,capabilities,route_context,hp_value,resources_value
from test_deterministic import live


def combat(turn=1):
    s=live();p=s['state']['player'];s['state']['ascension']=10
    p['creature'].update(hp=64,max_hp=80,block=0,powers=[])
    p.update(energy=3,turn=turn,relics=[dict(entity_id='RELIC.BURNING_BLOOD'),dict(entity_id='RELIC.PRECISE_SCISSORS')],potions=[])
    def card(i,entity,name,cost,text,target='AnyEnemy'):
        return dict(instance_id=i,entity_id='CARD.'+entity,name=name,energy_cost=cost,base_energy_cost=cost,
            star_cost=-1,upgrade_level=0,type='Skill' if target=='Self' else 'Attack',target_type=target,
            description=text,modifiers_complete=True,enchantment_id=None,affliction_id=None)
    strikes=[card('s'+str(i),'STRIKE_IRONCLAD','打击',1,'Deal 6 damage.') for i in range(4)]
    defends=[card('d'+str(i),'DEFEND_IRONCLAD','防御',1,'Gain 5 Block.','Self') for i in range(4)]
    bash=card('b','BASH','痛击',2,'Deal 8 damage. Apply 2 Vulnerable.')
    p['deck']=strikes+defends+[bash];p['hand']=strikes[:3]+defends[:2]
    p.update(discard=[],exhaust=[],draw_composition=[dict(entity_id='CARD.STRIKE_IRONCLAD',count=1),
        dict(entity_id='CARD.DEFEND_IRONCLAD',count=2),dict(entity_id='CARD.BASH',count=1)])
    s['state']['enemies'][0].update(entity_id='MONSTER.FUZZY_WURM_CRAWLER',name='毛绒伏地虫',hp=58,max_hp=59,
        current_move_id='FIRST_ACID_GOOP',powers=[],intents=[dict(type='Attack',attack_damage=6,attack_repeats=1)])
    actions(s)
    return s


def actions(s):
    s['state']['legal_actions']=[dict(action_id='play_'+c['instance_id'],type='play_card',
        allowed_target_ids=['e'] if c['target_type']=='AnyEnemy' else [],allows_no_target=c['target_type']!='AnyEnemy')
        for c in s['state']['player']['hand'] if c['energy_cost']>=0]
    s['state']['legal_actions'].append(dict(action_id='end_turn',type='end_turn',allows_no_target=True,allowed_target_ids=[]))


def plan(s,**budget):
    return analyze(s,index()[0],CombatPolicy(),dict(horizon=2,draws=5,energy=3,branches=8),
        SearchBudget(time_ms=1000,max_nodes=1000,top_k=20,**budget))


def test_unknown_relic_does_not_block_batches_and_same_first_has_different_endpoints():
    s=combat();b=plan(s)
    assert b['nodes']>1 and b['selected_batch']
    assert 'UNMODELED_RELIC:RELIC.PRECISE_SCISSORS' in b['assumptions']
    by_first={}
    for row in b['top_batches']:
        by_first.setdefault(str(row['steps'][0]),set()).add(tuple(str(x) for x in row['steps']))
    assert any(len(v)>1 for v in by_first.values())
    assert any(len(r['steps'])>1 for r in b['top_batches'])
    assert b['decision_unit']=='operation_batch'


def test_generation_equivalent_orders_share_endpoints_without_first_grouping():
    b=plan(combat())
    rows=[r for r in b['top_batches'] if set(x['action_id'] for x in r['steps'])=={'play_s0','play_s1','end_turn'}]
    assert len(rows)<=1


def test_actual_first_turn_and_buff_turn_are_chosen_locally_without_calibration():
    s=combat();r=analyze_decision(s,use_cache=False)
    assert r['autonomy']['eligible'] and r['autonomy']['decision_basis']=='local_combat_batch'
    assert not r['ai_required']
    s['state']['enemies'][0].update(hp=40,current_move_id='INHALE',intents=[dict(type='Buff',attack_damage=None,attack_repeats=None)])
    p=s['state']['player'];p['turn']=2;p['creature']['hp']=58
    p['hand']=[p['deck'][-1],p['deck'][0],p['deck'][4],p['deck'][5]];actions(s)
    b=plan(s);steps=[x['action_id'] for x in b['selected_batch']['steps']]
    assert 'play_b' in steps and 'play_s0' in steps
    assert steps.index('play_b')<steps.index('play_s0')


def test_draw_batch_stops_at_draw_but_scores_real_possible_continuations():
    s=combat();p=s['state']['player'];draw=deepcopy(p['hand'][3])
    draw.update(instance_id='draw',entity_id='CARD.SHRUG_IT_OFF',description='Gain 8 Block. Draw 1 card.')
    p['hand']=[draw,p['hand'][0]];p['deck'].append(draw);actions(s)
    b=plan(s)
    draws=[r for r in b['top_batches'] if any(x['action_id']=='play_draw' for x in r['steps'])]
    assert draws
    assert all(r['steps'][-1]['action_id']=='play_draw' and r['stop_reason']=='draw_requires_observation' for r in draws)
    assert all('scenario-' not in x['action_id'] for r in b['top_batches'] for x in r['steps'])


def test_budget_and_unknown_card_still_produce_local_batch():
    s=combat();s['state']['player']['hand'][0].update(entity_id='CARD.NEW',description='Deal 9 damage.')
    b=plan(s,max_depth=1)
    assert b['selected_batch']
    report=dict(source=source(s),combat=dict(batches=b))
    assert assess(report,s)['decision_basis']=='local_combat_batch'


def test_blood_heal_does_not_make_six_damage_free_and_hp_has_rest_opportunity():
    from autoslay.decision.normalize import normalize
    s=combat();initial=normalize(s);profile=capabilities(s,index()[0]);route=route_context(s,DEFAULTS)
    saved=resources_value(initial,initial,profile,DEFAULTS,route,True)
    damaged=resources_value(initial,replace(initial,hp=58),profile,DEFAULTS,route,True)
    assert saved['projected_hp_after_heal']==70 and damaged['projected_hp_after_heal']==64
    assert saved['run_hp_value']==0 and damaged['run_hp_value']<0
    high=hp_value(64,80,DEFAULTS,route)-hp_value(58,80,DEFAULTS,route)
    low=hp_value(28,80,DEFAULTS,route)-hp_value(22,80,DEFAULTS,route)
    assert low>high


def test_capability_proxy_values_sustainable_defense_and_growth():
    from autoslay.decision.normalize import normalize
    from autoslay.evaluation.combat_resources import future_pressure_value
    s=combat();state=normalize(s)
    state=replace(state,enemies=(replace(state.enemies[0],hp=20,attacks=(13,)),))
    p=capabilities(s,index()[0]);route=route_context(s,DEFAULTS)
    base=future_pressure_value(state,p,DEFAULTS,route)
    defense=future_pressure_value(state,p|{'block_per_draw':10},DEFAULTS,route)
    growth=future_pressure_value(replace(state,strength_delta=3),p,DEFAULTS,route)
    assert defense['estimated_future_hp_cost']>base['estimated_future_hp_cost']
    assert growth['estimated_turns_to_finish']<base['estimated_turns_to_finish']


def test_buff_at_horizon_keeps_its_followup_attack_pressure():
    b=plan(combat())
    assert b['selected_batch']['utility_components']['estimated_future_hp_cost']<0


def test_unknown_healing_estimate_caps_at_missing_hp():
    from autoslay.planning.combat_batches import visible_action_score
    from autoslay.decision.contracts import Operation
    s=combat();card=s['state']['player']['hand'][0];card['description']='Heal 30 HP.'
    wounded=visible_action_score(s,Operation('play_s0'),{},DEFAULTS)
    s['state']['player']['creature']['hp']=80
    full=visible_action_score(s,Operation('play_s0'),{},DEFAULTS)
    assert wounded>full and full==-1


def test_score_components_sum_to_batch_score():
    b=plan(combat())
    assert all(abs(sum(r['utility_components'].values())-r['score'])<1e-8 for r in b['top_batches'])


def test_model_downgrade_invalidates_cached_batch_and_keeps_local_selection(monkeypatch):
    from autoslay.execution import batch_models
    from autoslay.decision.unified import CACHE
    records={'entities':{}}
    monkeypatch.setattr(batch_models,'load',lambda root=None:deepcopy(records))
    CACHE.clear()
    s=combat();first=analyze_decision(s);assert not first['cache_hit']
    assert analyze_decision(s)['cache_hit']
    records['entities']['CARD.STRIKE_IRONCLAD']={'reason':'test mismatch'}
    second=analyze_decision(s)
    assert not second['cache_hit'] and second['autonomy']['eligible']
    assert 'downgraded_model:CARD.STRIKE_IRONCLAD' in second['combat']['batches']['assumptions']


def test_no_known_surviving_plan_still_selects_locally():
    s=combat();s['state']['player']['creature']['hp']=1
    s['state']['enemies'][0]['hp']=100
    s['state']['enemies'][0]['intents']=[dict(type='Attack',attack_damage=13,attack_repeats=1)]
    b=plan(s)
    assert b['selected_batch'] and b['selected_batch']['estimated_death_probability']==1
    assert assess(dict(source=source(s),combat=dict(batches=b)),s)['eligible']


def test_nested_combat_selection_is_local():
    s=combat();s['state'].update(phase='card_selection',pending_combat_action=dict(request_id='parent'))
    s['state']['card_selection']=dict(decision_ready=True,prompt='Choose a card',min_select=1,max_select=1,
        cards=[dict(action_id='choose_a',available=True,selected=False,card=s['state']['player']['hand'][0])])
    s['state']['legal_actions']=[dict(action_id='choose_a',type='select_combat_card',allows_no_target=True,allowed_target_ids=[])]
    r=assess({},s)
    assert r['eligible'] and r['decision_basis']=='local_combat_selection'


def test_agent_uses_real_batch_analysis_without_a_model_or_credentials(tmp_path):
    from autoslay.agent.runtime import Agent
    from test_agent import store
    from autoslay.execution.deterministic import DeterministicExecutor
    from unittest.mock import AsyncMock
    async def run():
        s=combat();calls=[]
        st=store(tmp_path);st.active='deepseek';st.set_key('deepseek','');st.profiles['deepseek'].model=''
        st.decision={'autonomy':{'combat_batches_enabled':True}}
        class Local:
            async def execute(self,request_id,**kwargs):
                calls.append(kwargs);s['state']['phase']='game_over'
                return dict(status='completed')
        agent=Agent(st,fetch=AsyncMock(side_effect=lambda:deepcopy(s)),control=AsyncMock(return_value={}),
            factory=lambda *a:pytest.fail('local combat must not construct a provider'),deterministic=Local(),
            journal=tmp_path/'agent',record=False)
        await agent.command('/start',{})
        await asyncio.wait_for(agent.task,10)
        assert calls==[{'bounded':True}] and agent.error is None
    asyncio.run(run())


def test_executor_stops_after_draw_even_if_given_a_longer_batch(tmp_path,monkeypatch):
    from autoslay.execution import deterministic
    s=combat();p=s['state']['player'];draw=deepcopy(p['hand'][3])
    draw.update(instance_id='draw',entity_id='CARD.SHRUG_IT_OFF',description='Gain 8 Block. Draw 1 card.')
    p['hand']=[draw,p['hand'][0]];actions(s);calls=[]
    steps=[dict(action_id='play_draw',target_id=None),dict(action_id='play_s0',target_id='e')]
    def qualify(snapshot,bounded):
        return dict(eligible=True,source=source(snapshot),selected_steps=steps,rule_id='local_combat_batch',
            reason='draw',decision_basis='local_combat_batch',evidence=dict(selected_batch=dict(steps=steps),stop_reason='draw_requires_observation'))
    monkeypatch.setattr(deterministic,'qualify',qualify)
    async def run():
        async def fetch():return deepcopy(s)
        async def submit(request):calls.append(request);return dict(status='completed')
        result=await deterministic.DeterministicExecutor(tmp_path/'executor',fetch,submit,record=False).execute('draw',bounded=True)
        assert len(calls)==1 and result['stop_reason']=='draw_requires_observation'
    asyncio.run(run())


def test_pending_parent_allows_only_its_bound_child_choice(tmp_path):
    import json
    from autoslay.execution import deterministic
    s=combat();s['state'].update(phase='card_selection',pending_combat_action=dict(request_id='parent'))
    s['state']['card_selection']=dict(decision_ready=True,prompt='Choose',min_select=1,max_select=1,
        cards=[dict(action_id='choose',available=True,selected=False,card=s['state']['player']['hand'][0])])
    s['state']['legal_actions']=[dict(action_id='choose',type='select_combat_card',allows_no_target=True,allowed_target_ids=[])]
    directory=tmp_path/'executor';directory.mkdir();(directory/'parent.json').write_text(json.dumps(dict(status='awaiting_choice',request=dict(request_id='parent'))))
    calls=[]
    async def run():
        async def fetch():return deepcopy(s)
        async def submit(request):calls.append(request);return dict(status='completed')
        async def status(request_id):return dict(status='awaiting_choice')
        result=await deterministic.DeterministicExecutor(directory,fetch,submit,record=False,status=status).execute('child')
        assert result['status']=='completed' and calls[0].action_id=='choose' and len(calls)==1
    asyncio.run(run())


def test_changed_card_information_redecides_without_disabling_local_selection(tmp_path,monkeypatch):
    from autoslay.execution import deterministic
    from autoslay.execution.batch_models import load
    from test_execution import FakeBridge
    s=live();s['state']['enemies'][0]['hp']=100;bridge=FakeBridge(s)
    steps=[dict(action_id='play_a',target_id='e'),dict(action_id='play_b',target_id='e')]
    monkeypatch.setattr(deterministic,'qualify',lambda snapshot,bounded:dict(eligible=True,source=source(snapshot),
        selected_steps=steps,rule_id='local_combat_batch',reason='test',decision_basis='local_combat_batch',
        evidence=dict(selected_batch=dict(steps=steps),stop_reason='batch_goal_completed')))
    async def run():
        async def submit(request):
            result=await bridge.submit(request)
            bridge.value['state']['player']['hand'][0]['description']='Deal 9 damage.'
            return result
        result=await deterministic.DeterministicExecutor(tmp_path/'executor',bridge.fetch,submit,record=False).execute('updated',bounded=True)
        assert result['stop_reason']=='hand_or_persistent_resources_changed' and len(bridge.calls)==1
        assert not load(deterministic.ROOT)['entities']
    asyncio.run(run())


@pytest.mark.parametrize('mismatch',[False,True])
def test_executor_runs_batch_and_mismatch_only_downgrades_action_model(tmp_path,monkeypatch,mismatch):
    from autoslay.execution import deterministic
    from autoslay.execution.batch_models import load
    from test_execution import FakeBridge
    s=live();s['state']['enemies'][0]['hp']=100
    bridge=FakeBridge(s,mismatch=mismatch)
    steps=[dict(action_id='play_a',target_id='e'),dict(action_id='play_b',target_id='e')]
    batch=dict(steps=steps,stop_reason='batch_goal_completed')
    def qualify(snapshot,bounded):
        return dict(eligible=True,source=source(snapshot),selected_steps=steps,rule_id='local_combat_batch',
            reason='test batch',decision_basis='local_combat_batch',evidence=dict(selected_batch=batch,stop_reason=batch['stop_reason']))
    monkeypatch.setattr(deterministic,'qualify',qualify)
    async def run():
        ex=deterministic.DeterministicExecutor(tmp_path/'executor',bridge.fetch,bridge.submit,record=False)
        result=await ex.execute('batch',bounded=True)
        assert result['status']=='completed'
        assert len(bridge.calls)==(1 if mismatch else 2)
        if mismatch:
            assert result['stop_reason']=='prediction_mismatch'
            assert 'CARD.STRIKE_IRONCLAD' in load(deterministic.ROOT)['entities']
        else:assert result['stop_reason']=='batch_goal_completed'
        await ex.execute('batch',bounded=True)
        assert len(bridge.calls)==(1 if mismatch else 2)
    asyncio.run(run())
