from copy import deepcopy
import hashlib
import json
import threading
from urllib.request import Request, urlopen

import pytest
from autoslay import actions
from autoslay.agent.contracts import validate
from autoslay.decision.deterministic import source, ADMIN
from autoslay.knowledge_base.repository import dataset_path, document_sha256, Repository, knowledge_scope
from test_deterministic import live


def test_global_directory_is_complete_and_shared_policy_is_queryable():
    catalog=actions.catalog()
    assert len(catalog['actions'])==42
    assert {r['group'] for r in catalog['actions']}=={
        'combat','inventory','rewards','menu','map','event','selection','combat_selection','shop','rest','treasure','lifecycle'}
    assert actions.action_types(group='combat')=={'play_card','use_potion','end_turn'}
    assert ADMIN==actions.action_types(agent_allowed=False)=={'return_to_main_menu','quit_game'}
    assert catalog['sha256']==hashlib.sha256(dataset_path('actions').read_bytes()).hexdigest()
    actions.definition('play_card')['source_kinds'].append('changed')
    assert actions.definition('play_card')['source_kinds']==['card']


def test_entity_capabilities_preserve_effects_triggers_and_autonomous_moves():
    card=actions.entity_capabilities('CARD.STRIKE_IRONCLAD')
    assert any(c['type']=='play_card' for c in card['commands'])
    assert card['effects']['declared_effects']
    curse=actions.entity_capabilities('CARD.ASCENDERS_BANE')
    assert next(c for c in curse['commands'] if c['type']=='play_card')['blocked_by_static_rule']
    relic=actions.entity_capabilities('RELIC.HAPPY_FLOWER')
    assert relic['commands']==[] and relic['effects']['periodic']['energy']==1
    assert any(c['type']=='take_treasure_relic' for c in relic['contextual_commands'])
    assert 'RELIC.HAPPY_FLOWER' in actions.entities_for_action('take_treasure_relic')['entity_ids']
    mango=actions.entity_capabilities('RELIC.MANGO')
    assert mango['effects']['passive']=='pickup'
    monster=actions.entity_capabilities('MONSTER.LEAF_SLIME_M')
    assert monster['commands']==[] and monster['state_graph'] and monster['moves']
    unknown=actions.entity_capabilities('CARD.NEW')
    assert unknown['knowledge_status']=='unknown' and unknown['effects']['status']=='missing_mechanic'
    assert all(c['availability']=='conditional' for c in unknown['commands'])
    assert 'CARD.STRIKE_IRONCLAD' in actions.entities_for_action('play_card')['entity_ids']


def test_snapshot_binds_cards_potions_and_ui_to_shared_definitions():
    snapshot=live();p=snapshot['state']['player']
    p['potions']=[dict(entity_id='POTION.FIRE_POTION',instance_id='p',usable=False,discardable=True,
                       use_action_id=None,discard_action_id='discard_p')]
    snapshot['state']['legal_actions'].append(dict(action_id='discard_p',type='discard_potion',allows_no_target=True))
    result=actions.describe(snapshot)
    attack=next(b for b in result['bindings'] if b['action_id']=='play_a')
    assert attack['source']['entity_id']=='CARD.STRIKE_IRONCLAD'
    assert attack['source']['instance_id']=='a' and attack['allowed_target_ids']==['e']
    assert attack['definition_id']=='ACTION.play_card'
    potion=next(b for b in result['bindings'] if b['action_id']=='discard_p')
    assert potion['source']['instance_id']=='p' and potion['source']['entity_id']=='POTION.FIRE_POTION'
    assert next(o for o in result['objects'] if o['instance_id']=='p')['observed_flags']['usable'] is False
    snapshot['state']['phase']='card_selection'
    snapshot['state']['card_selection']=dict(screen_id='screen',cards=[dict(card=p['hand'][0],action_id='select')])
    snapshot['state']['legal_actions']=[dict(action_id='select',type='select_combat_card',allows_no_target=True,
        source=dict(kind='card',entity_id=None,instance_id='a'))]
    selected=actions.describe(snapshot)['bindings'][0]
    assert selected['source']['entity_id']=='CARD.STRIKE_IRONCLAD' and selected['source']['instance_id']=='a'
    assert snapshot['state']['card_selection']['cards'][0]['card']==p['hand'][0]


def test_absence_is_not_reported_as_no_capability_or_an_invented_reason():
    snapshot=live();snapshot['state']['legal_actions']=[snapshot['state']['legal_actions'][-1]]
    result=actions.describe(snapshot)
    card=next(o for o in result['objects'] if o['instance_id']=='a')
    assert card['availability']=='not_exposed_currently' and card['action_ids']==[]
    assert 'reason' not in card
    assert any(c['type']=='play_card' for c in result['entities'][0]['commands'])


@pytest.mark.parametrize('change,code',[
    ('unknown','UNKNOWN_ACTION_TYPE'),('duplicate','DUPLICATE_ACTION_ID'),
    ('definition','ACTION_DEFINITION_MISMATCH'),('target','ACTION_TARGET_RULE_MISMATCH'),
    ('catalog','ACTION_CATALOG_MISMATCH')])
def test_registry_drift_and_bad_bindings_are_recorded(change,code):
    snapshot=live();state=snapshot['state']
    if change=='unknown':state['legal_actions'][0]['type']='new_command'
    if change=='duplicate':state['legal_actions'].append(deepcopy(state['legal_actions'][0]))
    if change=='definition':state['legal_actions'][0]['definition_id']='ACTION.wrong'
    if change=='target':state['legal_actions'][-1]['allowed_target_ids']=['e']
    if change=='catalog':state['action_catalog']=dict(version='1.0.0',sha256='changed')
    result=actions.describe(snapshot)
    assert code in {i['code'] for i in result['issues']}
    assert result['source']['state_version']==snapshot['state_version']


def test_submission_uses_registry_and_fresh_targets_instead_of_entity_capability():
    snapshot=live();payload=dict(state_token=source(snapshot)['state_sha256'],action_id='play_a',target_id='e',reason='attack')
    assert validate(json.dumps(payload),snapshot)
    snapshot['state']['legal_actions'][0]['allowed_target_ids']=[]
    payload['state_token']=source(snapshot)['state_sha256']
    with pytest.raises(ValueError,match='ILLEGAL_TARGET'):validate(json.dumps(payload),snapshot)
    snapshot=live();snapshot['state']['action_catalog']=dict(version='1.0.0',sha256='different')
    payload['state_token']=source(snapshot)['state_sha256']
    with pytest.raises(ValueError,match='ACTION_CATALOG_MISMATCH'):validate(json.dumps(payload),snapshot)
    snapshot=live();snapshot['state']['legal_actions'][0]['type']='unknown'
    payload['state_token']=source(snapshot)['state_sha256']
    with pytest.raises(ValueError,match='UNKNOWN_ACTION_TYPE'):validate(json.dumps(payload),snapshot)
    # Older snapshots explicitly lack registry/type metadata but retain live binding checks.
    snapshot['state']['legal_actions'][0].pop('type')
    payload['state_token']=source(snapshot)['state_sha256']
    assert validate(json.dumps(payload),snapshot)


def test_catalog_is_pinned_to_knowledge_revision(tmp_path,monkeypatch):
    from autoslay.knowledge_base import repository
    raw=actions.catalog();raw.pop('sha256');raw.pop('knowledge_revision',None)
    (tmp_path/'actions.json').write_text(json.dumps(raw),encoding='utf-8')
    (tmp_path/'manifest.json').write_text(json.dumps(dict(schema_version='knowledge-manifest/1.0',datasets={'actions':'actions.json'})),encoding='utf-8')
    repo=Repository(tmp_path);monkeypatch.setattr(repository,'REPOSITORY',repo)
    with knowledge_scope() as pinned:
        old=document_sha256(dataset_path('actions'))
        raw['scope']='updated'
        (tmp_path/'actions.json').write_text(json.dumps(raw),encoding='utf-8')
        assert document_sha256(dataset_path('actions'))==old
        assert actions.catalog()['knowledge_revision']==pinned.revision
    assert actions.catalog()['sha256']!=old


def test_analysis_records_full_bindings_and_effect_references():
    from autoslay.decision.unified import analyze_decision
    report=analyze_decision(live(),use_cache=False)
    assert report['action_space']['bindings'][0]['definition_id']=='ACTION.play_card'
    assert report['action_space']['catalog_sha256']==actions.catalog()['sha256']
    assert report['ai_packet']['action_space']['catalog_sha256']==actions.catalog()['sha256']


def test_execution_archive_keeps_action_bindings_and_entity_records(tmp_path):
    import asyncio
    from autoslay.execution.deterministic import DeterministicExecutor
    from autoslay.telemetry import decisions
    from autoslay.archive.repository import for_legacy
    async def run():
        snapshot=live();snapshot['state']['legal_actions']=snapshot['state']['legal_actions'][-1:]
        async def fetch():return deepcopy(snapshot)
        async def submit(request):return dict(status='completed')
        result=await DeterministicExecutor(tmp_path/'execution',fetch,submit).execute('catalog-record')
        event=for_legacy(decisions.DIRECTORY).load_event(result['analysis_id'])
        record=event['report']['action_space']
        assert record['catalog_sha256']==actions.catalog()['sha256']
        assert record['bindings'][0]['definition_id']=='ACTION.end_turn'
        assert any(e['entity_id']=='CARD.STRIKE_IRONCLAD' for e in record['entities'])
    asyncio.run(run())


def test_read_only_api_exposes_catalog_providers_and_live_records(monkeypatch,tmp_path):
    from autoslay.dashboard import server
    snapshot=live();monkeypatch.setattr(server,'resident',lambda route:snapshot)
    http=server.make_server(0,tmp_path/'records')
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        def get(path):
            request=Request('http://127.0.0.1:'+str(http.server_port)+path,headers={'X-Autoslay-ReadOnly':'1'})
            with urlopen(request,timeout=10) as response:return json.load(response)
        assert len(get('/api/actions')['actions'])==42
        assert get('/api/actions?entity_id=RELIC.HAPPY_FLOWER')['effects']['periodic']['energy']==1
        assert 'CARD.STRIKE_IRONCLAD' in get('/api/actions?type=play_card')['providers']['entity_ids']
        assert get('/api/actions/live')['bindings'][0]['source']['instance_id']=='a'
    finally:
        http.shutdown();http.server_close();thread.join(timeout=5)
