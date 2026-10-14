import asyncio
from copy import deepcopy
import json
from unittest.mock import AsyncMock
import pytest
from autoslay.decision.local_combat import select
from autoslay.decision.deterministic import source, assess, load_policy
from autoslay.decision.normalize import normalize
from autoslay.simulation.engine import CombatSimulator
from autoslay.planning.search import search, SearchBudget
from autoslay.evaluation.combat import CombatEvaluator
from autoslay.agent.runtime import validation_issues
from autoslay.agent.providers import ProviderError
from test_deterministic import live
from test_agent import make_agent


def analyzed():
    s=live();s['state']['enemies'][0]['hp']=100
    initial=normalize(s);sim=CombatSimulator(initial)
    combat=search(initial,sim,CombatEvaluator(),SearchBudget(time_ms=1000))
    combat['coverage']=dict(unsupported_cards=sim.unsupported,unsupported_potions=[],interaction_warnings=list(initial.warnings))
    return s,dict(source=source(s),combat=combat)


def test_local_policy_selects_one_step_without_claiming_global_proof():
    s,r=analyzed();proof,why=select(r,s)
    assert proof and not why and len(proof['steps'])==1
    assert proof['evidence']['optimality_proven'] is False
    assert proof['evidence']['long_term_value_proven'] is False
    p=load_policy();p['enabled_rules']=['bounded_combat_search']
    assert assess(r,s,p)['decision_basis']=='bounded_combat_strategy'
    assert not assess({},s,p)['eligible']


@pytest.mark.parametrize('fault',['hash','truncated','warnings','playable_unknown','hidden','modifier','fatal'])
def test_local_policy_preserves_coverage_and_freshness_guards(fault):
    s,r=analyzed();c=r['combat']
    if fault=='hash':r['source']['state_sha256']='stale'
    if fault=='truncated':c['search_truncated']=True
    if fault=='warnings':c['coverage']['interaction_warnings']=['UNKNOWN_HOOK']
    if fault=='playable_unknown':c['coverage']['unsupported_cards']={'a':'unknown'}
    if fault=='hidden':s['state']['enemies'][0]['hidden_power_ids']=['POWER.UNKNOWN'];r['source']=source(s)
    if fault=='modifier':s['state']['player']['hand'][0]['enchantment_id']='ENCHANTMENT.UNKNOWN';r['source']=source(s)
    if fault=='fatal':
        for x in c['decision_candidates']:x['metrics']['projected_death']=True
    assert select(r,s)[0] is None


def test_mango_pickup_does_not_disable_all_combat_transitions():
    s,r=analyzed();s['state']['player']['relics']=[dict(entity_id='RELIC.MANGO')]
    model=normalize(s)
    assert not any('MANGO' in w for w in model.warnings)
    assert CombatSimulator(model).apply(model, next(a for a in CombatSimulator(model).actions(model) if a.action_id=='play_a')).state.enemies[0].hp==94


def test_visible_sharp_bash_damage_is_used_once():
    s,_=analyzed();s['state']['player']['hand'][0].update(entity_id='CARD.BASH',energy_cost=2,
        description='Deal 10 damage. Apply 2 Vulnerable.',enchantment_id='ENCHANTMENT.SHARP')
    initial=normalize(s);sim=CombatSimulator(initial)
    result=sim.apply(initial,next(a for a in sim.actions(initial) if a.action_id=='play_a'))
    assert result.state.enemies[0].hp==90
    c=search(initial,sim,CombatEvaluator(),SearchBudget(time_ms=1000))
    c['coverage']=dict(unsupported_cards=sim.unsupported,unsupported_potions=[],interaction_warnings=list(initial.warnings))
    assert select(dict(source=source(s),combat=c),s)[0]


def test_draw_prefix_is_one_step_and_never_plans_unseen_cards():
    s,_=analyzed();s['state']['player']['hand'][0].update(entity_id='CARD.POMMEL_STRIKE',description='Deal 9 damage. Draw 1 card.')
    initial=normalize(s);sim=CombatSimulator(initial)
    c=search(initial,sim,CombatEvaluator(),SearchBudget(time_ms=1000))
    assert c['observation_boundaries']
    c['coverage']=dict(unsupported_cards=sim.unsupported,unsupported_potions=[],interaction_warnings=list(initial.warnings))
    proof,why=select(dict(source=source(s),combat=c),s)
    assert proof and len(proof['steps'])==1 and proof['evidence']['optimality_proven'] is False


def test_potion_that_prevents_damage_requires_ai_instead_of_blind_conservation():
    s,_=analyzed();s['state']['player']['potions']=[dict(instance_id='block',entity_id='POTION.BLOCK_POTION',
        use_action_id='use_potion_block',description='Gain 12 Block.',usable=True)]
    s['state']['legal_actions'].append(dict(action_id='use_potion_block',type='use_potion',allows_no_target=True,allowed_target_ids=[]))
    initial=normalize(s);sim=CombatSimulator(initial)
    c=search(initial,sim,CombatEvaluator(),SearchBudget(time_ms=1000,top_k=20))
    c['coverage']=dict(unsupported_cards=sim.unsupported,unsupported_potions=list(sim.unsupported_potions),interaction_warnings=list(initial.warnings))
    proof,why=select(dict(source=source(s),combat=c),s)
    assert proof is None and why==['local_combat_potion_survival_or_finish_tradeoff']


@pytest.mark.parametrize('outcome',['fixed','invalid_again','state_changed'])
def test_malformed_model_reply_gets_one_fresh_bounded_correction(tmp_path,outcome):
    async def run():
        agent,s,_,submit=make_agent(tmp_path);agent.running=True;agent.generation=1;origin=deepcopy(s);calls=[]
        class Provider:
            async def decide(self,packet):
                calls.append(packet)
                if len(calls)==1 and outcome=='state_changed':s['state']['player']['energy']+=1
                if len(calls)==1 or outcome=='invalid_again':return '{"private-response":"never log"}',{'total_tokens':10}
                return json.dumps(dict(state_token=source(origin)['state_sha256'],reason='attack',steps=[dict(action_id='play_a',target_id='e')])),{'total_tokens':20}
        if outcome=='invalid_again':
            with pytest.raises(ProviderError) as exc:
                await agent.decide_plan(Provider(),{},origin,1,'id',{},agent.store.profiles['codex'])
            assert exc.value.details['validation_attempts']==2
            assert 'never log' not in str(exc.value.details) and 'private-response' not in str(exc.value.details)
        else:
            plan,usage=await agent.decide_plan(Provider(),{},origin,1,'id',{},agent.store.profiles['codex'])
            if outcome=='fixed':
                assert plan.steps[0].action_id=='play_a' and usage['total_tokens']==30
                assert calls[1]['validation_feedback']['code']=='MODEL_INVALID_DECISION'
            else:assert plan is None
        assert len(calls)==(1 if outcome=='state_changed' else 2)
        assert submit.await_count==0
    asyncio.run(run())


def test_post_analysis_local_strategy_does_not_request_model(tmp_path,monkeypatch):
    async def run():
        import autoslay.agent.runtime as runtime
        agent,s,_,_=make_agent(tmp_path,factory=lambda *_:pytest.fail('AI must be skipped'))
        original=runtime.assess
        def gate(report,snapshot):
            value=original({},snapshot)
            if report:value.update(eligible=True,decision_basis='bounded_combat_strategy',rule_id='bounded_combat_search')
            return value
        monkeypatch.setattr(runtime,'assess',gate)
        class Executor:
            async def execute(self,identity,*,bounded=False):
                assert bounded;s['state']['phase']='game_over';s['state_version']+=1
                return dict(status='completed')
        agent.deterministic=Executor()
        await agent.command('/start',{});await asyncio.wait_for(agent.task,3)
        assert agent.phase=='finished' and agent.last['decision_basis']=='bounded_combat_strategy'
    asyncio.run(run())
