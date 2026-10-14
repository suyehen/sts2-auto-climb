import asyncio
from copy import deepcopy
import json
import threading
from pathlib import Path
import httpx
import pytest
from autoslay.paths import ROOT
from autoslay.knowledge_base.catalog import save_catalog
from autoslay.knowledge_base.updates import compare,rollback,CatalogSynchronizer
from autoslay.knowledge_base.rulebook import Rulebook,validate_rulebook,entry_fingerprint
from autoslay.knowledge_base.source_audit import recognize_card,recognize_potion
from autoslay.rules.registry import IroncladRules
from autoslay.decision.contracts import Card,CombatState,UnsupportedEffect
from autoslay.evaluation.event_sequences import evaluate_event_sequence
from autoslay.evaluation.run import RunContext,RunPolicy
from autoslay.knowledge_base.templates import index
from autoslay.telemetry import decisions
from autoslay.dashboard.server import make_server
from dataclasses import replace
from autoslay.simulation.engine import CombatSimulator
from autoslay.decision.contracts import Enemy,Operation,Potion
from autoslay.knowledge_base.templates import canonical

@pytest.mark.parametrize('identity',[i for i,r in Rulebook().data['entries'].items() if r.get('review_evidence') and r['category']=='card'])
def test_source_reviewed_rules_have_readable_base_and_upgrade_and_valid_flow(identity):
    entries=index()[0]; entry=entries[identity]
    for variant in entry['variants']:
        raw=dict(entity_id=identity,instance_id='source-fixture',upgrade_level=variant['upgrade_level'])
        card=canonical(raw,entries)[0]
        initial=CombatState(50,80,0,10,(card,),(Enemy('e','MONSTER.TEST',10000,0,0,None,()),),
                            (('CARD.STRIKE_IRONCLAD',10),),discard_known=True,stars=10)
        simulator=CombatSimulator(initial)
        assert not simulator.unsupported
        target='e' if card.target_type=='AnyEnemy' else None
        result=simulator.apply(initial,Operation('play_source-fixture',target))
        assert result.state.energy>=0 and result.state.stars==10-max(0,card.star_cost)
        assert all(c.instance_id!='source-fixture' for c in result.state.hand)
        if not result.boundary:
            destination=result.state.exhausted_composition if simulator.compiled[card.instance_id].exhaust else result.state.discard_composition
            assert dict(destination)[identity]==1


@pytest.mark.parametrize('identity',[i for i,r in Rulebook().data['entries'].items() if r.get('review_evidence') and r['category']=='potion'])
def test_source_reviewed_potions_parse_actual_reference_and_consume_once(identity):
    book=Rulebook(); rule=book.require(identity,'potion'); entry=book.references[identity]
    target='enemy' if rule['target_type']=='AnyEnemy' else 'player' if rule['target_type']=='AnyPlayer' else None
    potion=Potion('p',identity,entry['description'],'use_p',(() if target is None else (target,)))
    state=CombatState(50,80,8,3,(),(Enemy('enemy','MONSTER.TEST',100,0,0,None,()),),
                      (('CARD.STRIKE_IRONCLAD',7),),potions=(potion,),discard_known=True)
    simulator=CombatSimulator(state)
    assert not simulator.unsupported_potions
    result=simulator.apply(state,Operation('use_p',target))
    assert not result.state.potions and Operation('use_p',target) not in simulator.actions(result.state)
    assert not result.state.discard_composition  # potion never enters the card pile


def potion_state(identity,description,target='player',**changes):
    return CombatState(50,80,8,3,(),(Enemy('enemy','MONSTER.TEST',100,5,2,None,(12,)),),
                       (('CARD.STRIKE_IRONCLAD',7),),potions=(Potion('p',identity,description,'use_p',(target,)),),
                       discard_known=True,**changes)


def test_potion_unpowered_damage_ignores_strength_and_vulnerability_but_respects_cap():
    state=potion_state('POTION.FIRE_POTION','Deal 20 damage.','enemy',strength_delta=9)
    result=CombatSimulator(state).apply(state,Operation('use_p','enemy'))
    assert result.state.enemies[0].hp==85 and result.state.enemies[0].block==0
    state=replace(state,enemies=(replace(state.enemies[0],damage_cap=1),))
    assert CombatSimulator(state).apply(state,Operation('use_p','enemy')).state.enemies[0].block==4


def test_potion_block_keeps_legion_for_next_card_and_fortifier_adds_twice_current_block():
    state=potion_state('POTION.BLOCK_POTION','Gain 12 Block.',legion_ready=True,player_frail=True)
    result=CombatSimulator(state).apply(state,Operation('use_p','player'))
    assert result.state.block==20 and result.state.legion_ready and result.boundary is None
    state=potion_state('POTION.FORTIFIER','Triple Block.',legion_ready=True)
    result=CombatSimulator(state).apply(state,Operation('use_p','player'))
    assert result.state.block==24 and result.state.legion_ready


def test_compound_potion_pays_energy_before_draw_and_keeps_full_hand_when_draw_blocked():
    state=potion_state('POTION.CURE_ALL','Gain 2 Energy. Draw 2 cards.')
    result=CombatSimulator(state).apply(state,Operation('use_p','player'))
    assert result.state.energy==5 and result.draw_count==2 and result.boundary=='draw_requires_observation'
    blocked=replace(state,effect_draw_blocked=True)
    result=CombatSimulator(blocked).apply(blocked,Operation('use_p','player'))
    assert result.state.energy==5 and result.draw_count==0 and result.boundary is None
    full=replace(state,hand=tuple(Card(str(i),'CARD.STRIKE_IRONCLAD',1,'AnyEnemy','Deal 6 damage.') for i in range(10)))
    result=CombatSimulator(full).apply(full,Operation('use_p','player'))
    assert result.state.hand==full.hand and result.state.draw_composition==full.draw_composition
    assert result.draw_count==0 and result.boundary is None


def test_compound_draw_resume_preserves_order_play_pile_and_single_final_discard():
    from autoslay.rules.registry import CardRule,Effect
    from autoslay.planning.lookahead import draw_branches
    class CompoundRules(IroncladRules):
        def card(self,card,state):
            if card.instance_id=='compound':
                return CardRule((Effect('draw',1),Effect('energy',2),Effect('draw',1),Effect('damage',6)))
            return super().card(card,state)
    card=Card('compound','CARD.SHRUG_IT_OFF',1,'AnyEnemy','Draw, gain energy, draw, damage.')
    state=replace(potion_state('POTION.BLOCK_POTION','Gain 12 Block.'),hand=(card,),potions=(),
                  draw_composition=(('CARD.STRIKE_IRONCLAD',7),))
    sim=CombatSimulator(state,CompoundRules()); first=sim.apply(state,Operation('play_compound','enemy'))
    template=Card('template','CARD.STRIKE_IRONCLAD',1,'AnyEnemy','Deal 6 damage.')
    assert first.state.energy==2 and first.pending_card==card and not first.state.discard_composition
    branch=draw_branches(first.state,1,{'CARD.STRIKE_IRONCLAD':template},8,42)[0][0][1]
    second=sim.resume_draw(first,branch)
    assert second.state.energy==4 and second.boundary=='draw_requires_observation'
    assert not second.state.discard_composition and second.state.enemies==state.enemies
    branch=draw_branches(second.state,1,{'CARD.STRIKE_IRONCLAD':template},8,42)[0][0][1]
    final=sim.resume_draw(second,branch)
    assert final.boundary is None and final.state.energy==4 and len(final.state.hand)==2
    assert final.state.enemies[0].hp==96 # 6 * vulnerable 1.5 - existing 5 block
    assert final.state.draw_composition==(('CARD.STRIKE_IRONCLAD',5),)
    assert final.state.discard_composition==(('CARD.SHRUG_IT_OFF',1),)


def test_strength_potion_changes_following_attack_not_unpowered_damage():
    strike=Card('s','CARD.STRIKE_IRONCLAD',1,'AnyEnemy','Deal 6 damage.')
    state=replace(potion_state('POTION.STRENGTH_POTION','Gain 2 Strength.'),hand=(strike,))
    sim=CombatSimulator(state); buff=sim.apply(state,Operation('use_p','player'))
    result=sim.apply(buff.state,Operation('play_s','enemy'))
    assert buff.state.strength_delta==2 and result.state.enemies[0].hp==93 # 8*1.5 - 5 block


def test_potion_recognizer_refuses_unknown_command_and_changed_target():
    from autoslay.knowledge_base.source_audit import SOURCE
    source=(SOURCE/'MegaCrit.Sts2.Core.Models.Potions/StrengthPotion.cs').read_text(encoding='utf-8-sig')
    assert recognize_potion(source)[0]
    assert recognize_potion(source.replace('TargetType.AnyPlayer','TargetType.AnyEnemy'))[0] is None
    assert recognize_potion(source.replace('await PowerCmd.Apply','await UnknownCmd.Apply'))[0] is None

def test_star_cost_is_checked_paid_and_preserved_in_projection():
    from autoslay.execution.sequence import projection
    entries=index()[0]; card=canonical(dict(entity_id='CARD.CLOAK_OF_STARS',instance_id='star'),entries)[0]
    initial=CombatState(40,80,0,3,(card,),(),(),stars=0)
    simulator=CombatSimulator(initial)
    assert not simulator.actions(initial)
    initial=replace(initial,stars=2); result=simulator.apply(initial,Operation('play_star'))
    assert result.state.stars==1 and result.state.block==7 and projection(result.state)['stars']==1

def database(tmp_path):
    data=json.loads((ROOT/'data/knowledge/catalog/encyclopedia.json').read_text(encoding='utf-8-sig'))
    rules=json.loads((ROOT/'data/knowledge/rules/rulebook.json').read_text(encoding='utf-8-sig'))
    catalog=tmp_path/'catalog.json'; rule_path=tmp_path/'rules.json'
    save_catalog(data,catalog); rule_path.write_text(json.dumps(rules),encoding='utf-8')
    return data,rules,catalog,rule_path

def test_semantic_update_invalidates_only_affected_rule_and_keeps_backup(tmp_path):
    data,rules,catalog,path=database(tmp_path)
    before=catalog.read_bytes()
    entry=next(e for e in data['entries'] if e['entity_id']=='CARD.STRIKE_IRONCLAD')
    entry['variants'][0]['energy_cost']=2
    result=save_catalog(data,catalog)
    assert result['update_report']['changes'][0]['rules_require_review']
    book=Rulebook(path,catalog)
    assert book.status('CARD.STRIKE_IRONCLAD')=='reference_changed'
    assert book.status('CARD.DEFEND_IRONCLAD')=='compatible'
    with pytest.raises(UnsupportedEffect):
        IroncladRules(book).card(Card('s','CARD.STRIKE_IRONCLAD',1,'AnyEnemy','Deal 6 damage.'),CombatState(20,80,0,3,(),(),()))
    assert next((tmp_path/'history').glob('*.json')).read_bytes()==before

def test_version_change_and_same_version_fingerprint_detection(tmp_path):
    data,rules,catalog,path=database(tmp_path)
    assert not save_catalog(data,catalog)['changed']
    data['game_version']='0.112.0'; save_catalog(data,catalog)
    assert Rulebook(path,catalog).status('CARD.DEFEND_IRONCLAD')=='rule_version_mismatch'

def test_cosmetic_rename_keeps_rule_and_rollback_checks_filename(tmp_path):
    data,rules,catalog,path=database(tmp_path)
    entry=next(e for e in data['entries'] if e['entity_id']=='CARD.STRIKE_IRONCLAD'); entry['name']='renamed'
    result=save_catalog(data,catalog)
    assert result['update_report']['changes'][0]['kind']=='cosmetic_changed'
    assert Rulebook(path,catalog).status(entry['entity_id'])=='compatible'
    with pytest.raises(ValueError): rollback('../catalog.json',catalog)
    result=rollback(next((tmp_path/'history').glob('*.json')).name,catalog)
    assert result['changed']

def test_rule_format_rejects_code_and_unknown_mechanisms():
    data=deepcopy(Rulebook().data)
    rule=data['entries']['CARD.STRIKE_IRONCLAD']
    rule['effects'][0]['amount']={'python':'import os'}
    with pytest.raises(ValueError): validate_rulebook(data)
    rule['effects'][0]=dict(operation='invented_operation',amount=1)
    with pytest.raises(ValueError): validate_rulebook(data)

def test_auto_refresh_is_menu_only_and_checks_live_version(tmp_path,monkeypatch):
    from autoslay.knowledge_base import catalog
    calls=[]
    async def export(**kwargs): calls.append(kwargs); return dict(game_version='0.111.0',changed=False)
    monkeypatch.setattr(catalog,'export_catalog',export)
    async def check():
        sync=CatalogSynchronizer(tmp_path/'catalog.json',interval=0)
        assert (await sync.observe(dict(connected=True,game_version='0.111.0',state=dict(phase='combat'))))['status']=='deferred_until_main_menu'
        assert not calls
        await sync.observe(dict(connected=True,game_version='0.111.0',state=dict(phase='main_menu')))
        await sync.task
        assert calls[0]['expected_game_version']=='0.111.0' and calls[0]['refresh']
        await sync.close()
    asyncio.run(check())

def test_continuous_event_accounts_for_max_hp_heal_and_growing_damage():
    entries=index()[0]; deck=[dict(entity_id='CARD.STRIKE_IRONCLAD',instance_id='s',upgrade_level=0)]
    event=dict(entity_id='EVENT.ABYSSAL_BATHS',options=[dict(text_key='ABYSSAL_BATHS.pages.INITIAL.options.IMMERSE',action_id='continue'),dict(text_key='ABYSSAL_BATHS.pages.INITIAL.options.ABSTAIN',action_id='exit')])
    result=evaluate_event_sequence(event,deck,RunContext(40,80,0),entries,RunPolicy(),'0.111.0',max_steps=3)
    rows={r['repeats']:r for r in result['candidates']}
    assert rows[0]['final_context']['hp']==50 and rows[0]['first_action_id']=='exit'
    assert rows[1]['final_context']['max_hp']==82 and rows[1]['final_context']['hp']==39
    assert rows[2]['final_context']['max_hp']==84 and rows[2]['final_context']['hp']==37
    assert rows[3]['final_context']['hp']==34
    assert not result['execution_authorized']
    event['options']=[dict(text_key='ABYSSAL_BATHS.pages.ALL.options.LINGER')]
    assert not evaluate_event_sequence(event,deck,RunContext(40,80,0),entries,RunPolicy(),'0.111.0')['supported']

def test_event_excludes_lethal_continuation_and_unmodeled_hooks():
    entries=index()[0]; deck=[dict(entity_id='CARD.STRIKE_IRONCLAD')]
    event=dict(entity_id='EVENT.ABYSSAL_BATHS',dynamic_variables=[dict(name='Damage',value=9)],options=[dict(text_key='X.LINGER'),dict(text_key='X.EXIT_BATHS')])
    result=evaluate_event_sequence(event,deck,RunContext(3,80,0),entries,RunPolicy(),'0.111.0')
    assert result['avoided_lethal_continuation'] and len(result['candidates'])==1
    event['interaction_warnings']=['damage_hook']
    assert not evaluate_event_sequence(event,deck,RunContext(30,80,0),entries,RunPolicy(),'0.111.0')['supported']

def test_straight_line_recognizer_rejects_conditions_and_trigger_hooks():
    text='protected override async Task OnPlay(PlayerChoiceContext c, CardPlay cardPlay) { await CreatureCmd.GainBlock(base.Owner.Creature, base.DynamicVars.Block, cardPlay); }'
    assert recognize_card(text)[0]['effects'][0]['operation']=='block'
    assert recognize_card(text.replace('await CreatureCmd','if (x) await CreatureCmd'))[0] is None
    assert recognize_card(text+' public override Task AfterCardPlayed() {}')[0] is None
    assert recognize_card(text+' protected override bool IsPlayable => false;')[0] is None

def test_general_event_graph_repeats_with_explicit_costs_and_keeps_probability():
    from autoslay.evaluation.event_sequences import evaluate_graph
    entries=index()[0]; deck=[dict(entity_id='CARD.STRIKE_IRONCLAD')]; context=RunContext(40,80,0)
    graph={'first':[dict(option_id='exit',effects=[dict(kind='heal',amount=10)]),
                    dict(option_id='continue',effects=[dict(kind='gain_max_hp',amount=2,also_heals=True),dict(kind='lose_hp',amount=3)],next_state='second')],
           'second':[dict(option_id='exit',effects=[]),dict(option_id='gamble',outcomes=[dict(probability=.25,effects=[dict(kind='gain_gold',amount=40)]),dict(probability=.75,effects=[])])]}
    result=evaluate_graph(graph,'first',deck,context,entries,RunPolicy())
    candidate=next(c for c in result['candidates'] if c['option_id']=='continue')
    immediate=candidate['branches'][0]['immediate']
    assert immediate['hp_after']==39 and immediate['max_hp_after']==82
    later=candidate['branches'][0]['continuation']
    assert sum(b['probability'] for b in next(c for c in later['candidates'] if c['option_id']=='gamble')['branches'])==1
    assert not result['execution_authorized']

def test_event_graph_cycles_are_bounded_and_unknown_not_zero():
    from autoslay.evaluation.event_sequences import evaluate_graph
    result=evaluate_graph({'loop':[dict(option_id='again',effects=[],next_state='loop')]},'loop',
                          [dict(entity_id='CARD.STRIKE_IRONCLAD')],RunContext(40,80,0),index()[0],RunPolicy(),max_depth=3)
    assert result['search_truncated'] and not result['known']
    assert result['candidates'][0]['utility'] is None

def test_normal_decision_only_contains_relevant_knowledge():
    from autoslay.decision.unified import analyze_decision
    data=json.loads((ROOT/'data/replays/boss_turn4.json').read_text(encoding='utf-8-sig'))
    report=analyze_decision(data,use_cache=False)
    assert report['knowledge_compatibility']['entry_scope']=='current_decision_entities_only'
    assert len(report['knowledge_compatibility']['entries'])<100
    assert len(Rulebook().audit()['entries'])>900

def test_decision_archive_whitelists_public_state_and_separates_replays(tmp_path):
    report=dict(analysis_id='fixture',source=dict(state_version=1),phase='combat')
    snapshot=dict(game_version='0.111.0',token='must-not-appear',state=dict(phase='combat',transport_token='must-not-appear',player=dict(energy=3)))
    decisions.archive(report,snapshot,'replay',tmp_path)
    data=decisions.load('fixture','replay',tmp_path)
    assert 'must-not-appear' not in json.dumps(data)
    assert decisions.list_records(tmp_path)[0]['origin']=='replay'
    with pytest.raises(ValueError): decisions.load('../fixture','replay',tmp_path)

def test_ai_payload_keeps_metrics_without_repeating_full_state_traces():
    report=dict(analysis_id='fixture',source={},phase='combat',shortlist=[dict(steps=[],metrics=dict(projected_hp_loss=2),state_trace=[dict(secret_size='large_state')])],
                combat=dict(candidates=[],normalized_input={'hand':['large']},coverage={}),ai_packet={})
    payload=decisions.compact(report)
    assert payload['shortlist'][0]['metrics']['projected_hp_loss']==2
    assert 'state_trace' not in json.dumps(payload) and 'normalized_input' not in payload['combat']
    assert report['shortlist'][0]['state_trace']


def test_encyclopedia_tool_response_keeps_diff_local():
    from autoslay.knowledge_base.updates import compact_update
    result=dict(path='catalog.json',update_report=dict(changes=[dict(kind='semantic_changed',entity_id='CARD.A')],all_rules_require_review=False))
    compact=compact_update(result)
    assert 'changes' not in compact['update_report']
    assert compact['update_report']['changed_entity_count']==1
    assert compact['update_report']['change_counts']==dict(semantic_changed=1)
    assert compact['update_report']['full_diff']['json_pointer']=='/update_report'
    assert len(result['update_report']['changes'])==1

def test_same_version_implementation_hash_change_invalidates_rules(tmp_path):
    data,rules,catalog,path=database(tmp_path)
    data['game_assembly_sha256']='different-implementation';save_catalog(data,catalog)
    assert Rulebook(path,catalog).status('CARD.DEFEND_IRONCLAD')=='game_implementation_changed'


def test_export_metadata_rebind_preserves_validation_and_rejects_effect_or_binary_changes():
    from autoslay.knowledge_base.updates import rebind_added_export_metadata
    book=Rulebook(); old=deepcopy(book.catalog); new=deepcopy(old)
    entry=next(e for e in new['entries'] if e['entity_id']=='CARD.STRIKE_IRONCLAD')
    entry['dynamic_variables']=[dict(name='Damage',base_value=6)]
    result,report=rebind_added_export_metadata(book.data,old,new)
    assert 'CARD.STRIKE_IRONCLAD' in report['rebound'] and result['entries']['CARD.STRIKE_IRONCLAD']['validation']=='verified_adapter'
    entry['variants'][0]['energy_cost']=99
    result,report=rebind_added_export_metadata(book.data,old,new)
    assert 'CARD.STRIKE_IRONCLAD' in report['rejected']
    assert result['entries']['CARD.STRIKE_IRONCLAD']['reference_sha256']==book.data['entries']['CARD.STRIKE_IRONCLAD']['reference_sha256']
    new['game_assembly_sha256']='changed'
    with pytest.raises(ValueError): rebind_added_export_metadata(book.data,old,new)


def test_catalog_rejects_stale_generation_but_explicit_rollback_can_restore(tmp_path):
    data=deepcopy(Rulebook().catalog); data.update(session_epoch='same',generation=5)
    path=tmp_path/'catalog.json'; save_catalog(data,path); before=path.read_bytes()
    data['generation']=4; data['entries'][0]['name']='older export'
    with pytest.raises(ValueError): save_catalog(data,path)
    assert path.read_bytes()==before
    assert save_catalog(data,path,allow_rollback=True)['changed']


def test_catalog_cross_process_busy_preserves_file(tmp_path):
    from autoslay.execution.lock import dispatch_lock
    data=deepcopy(Rulebook().catalog); path=tmp_path/'catalog.json';save_catalog(data,path)
    before=path.read_bytes(); data['entries'][0]['name']='update'
    with dispatch_lock(tmp_path/'.catalog-lock') as acquired:
        assert acquired
        with pytest.raises(OSError): save_catalog(data,path)
    assert path.read_bytes()==before

def test_dashboard_is_read_only_and_blocks_cross_origin(tmp_path):
    server=make_server(0,tmp_path); task=threading.Thread(target=server.serve_forever,daemon=True);task.start()
    url=f'http://127.0.0.1:{server.server_port}'
    try:
        with httpx.Client(trust_env=False) as client:
            assert client.get(url+'/').status_code==200
            assert client.get(url+'/api/history').status_code==403
            headers={'X-Autoslay-ReadOnly':'1'}
            assert client.get(url+'/api/history',headers=headers).json()==dict(records=[])
            assert 'boss_turn4.json' in client.get(url+'/api/replays',headers=headers).json()['files']
            assert 'baseline-0.3.0.json' not in client.get(url+'/api/replays',headers=headers).json()['files']
            replay=client.post(url+'/api/replay',json=dict(file='boss_turn4.json'),headers=headers)
            assert replay.status_code==200 and replay.json()['origin']=='replay'
            assert replay.json()['report']['shortlist'][0]['state_trace']
            assert client.get(url+'/api/history',headers=headers|{'Origin':'https://unrelated.example'}).status_code==403
            assert client.post(url+'/api/act',json={},headers=headers).status_code==404
            assert client.get(url+'/api/record?id=..%2Fsecret',headers=headers).status_code==400
            assert client.get(url+'/api/history',headers=headers|{'Host':'evil.example'}).status_code==403
    finally: server.shutdown();server.server_close();task.join(2)
