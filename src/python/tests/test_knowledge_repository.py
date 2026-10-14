from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from autoslay.knowledge_base.repository import Repository, knowledge_scope, read_json
from autoslay.knowledge_base.service import KnowledgeService


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


@pytest.fixture
def knowledge(tmp_path):
    root = tmp_path / 'knowledge'
    write(root / 'manifest.json', dict(schema_version='knowledge-manifest/1.0', datasets={
        'catalog': 'catalog.json', 'mechanics': 'mechanics.json', 'aliases': 'aliases.json',
        'relations': 'relations.json', 'timing': 'timing.json', 'missing': 'missing.json',
        'behavior': 'behavior.json'}))
    write(root / 'catalog.json', dict(schema_version='1.0', status='complete',
        game_version='test', game_assembly_sha256='build-one', language='zhs',
        exported_at='2026-10-14', scope='fixture', entries=[
            dict(entity_id='CARD.STRIKE', category='card', name='打击',
                 variants=[dict(description='造成伤害', upgrade_level=0)]),
            dict(entity_id='RELIC.FLOWER', category='relic', name='花朵', description='回合开始获得能量')]))
    write(root / 'mechanics.json', dict(game_version='test', entries=[
        dict(id='capacity', title='手牌容量', rule='手牌满时生成牌进入弃牌堆'),
        dict(id='draw', title='抽牌限制', rule='满手时停止抽牌')]))
    write(root / 'aliases.json', dict(entries={'mechanics:capacity': ['手牌溢出'], 'CARD.STRIKE': ['基础攻击']}))
    write(root / 'relations.json', dict(entries=[dict(source='mechanics:capacity', target='mechanics:draw', kind='capacity')]))
    write(root / 'timing.json', dict(schema_version='knowledge-timing/1.0', entries=[dict(
        id='action', review_status='reviewed', title='动作稳定时间', game_version='test',
        reviewed_by='test reviewer', reviewed_at='2026-10-14T12:00:00+08:00', source_refs=['fixture/log'],
        samples=20, unit='ms', sample_window=dict(start='2026-10-13', end='2026-10-14'),
        measurement_scope='action_settlement', environment={'device': 'fixture'}, p50_ms=25, p95_ms=40)]))
    repository = Repository(root)
    return repository, KnowledgeService(repository, tmp_path / 'indexes')


def test_shared_cache_copies_and_parallel_reads(knowledge):
    repository, _ = knowledge
    path = repository.root / 'catalog.json'
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(repository.read, [path] * 12))
    results[0]['entries'][0]['name'] = 'changed by caller'
    assert repository.read(path)['entries'][0]['name'] == '打击'
    assert repository.parses == 2  # Service configuration + the catalog, once each.
    assert repository.hits >= 12


def test_snapshot_pins_updated_deleted_and_missing_documents(knowledge):
    repository, _ = knowledge
    before = repository.acquire_snapshot()
    path = repository.root / 'catalog.json'
    original = repository.read(path)
    changed = json.loads(json.dumps(original))
    changed['entries'][0]['name'] = '新打击'
    write(path, changed)
    write(repository.root / 'missing.json', dict(new=True))
    (repository.root / 'mechanics.json').unlink()
    after = repository.acquire_snapshot()
    assert before.revision != after.revision
    with knowledge_scope(before):
        assert read_json(path)['entries'][0]['name'] == '打击'
        assert read_json(repository.root / 'missing.json', {}) == {}
        assert read_json(repository.root / 'mechanics.json')['entries']
    assert after.read(path)['entries'][0]['name'] == '新打击'
    assert after.read(repository.root / 'missing.json')['new']
    with pytest.raises(FileNotFoundError):
        after.read(repository.root / 'mechanics.json')


def test_snapshot_payload_cannot_be_mutated(knowledge):
    repository, _ = knowledge
    snapshot = repository.acquire_snapshot()
    payload = snapshot.documents[str(repository.root / 'catalog.json')].data
    with pytest.raises(TypeError):
        payload['entries'].append({})
    with pytest.raises(TypeError):
        payload['entries'][0]['name'] = 'overwrite'


def test_manifest_rejects_escape(tmp_path):
    write(tmp_path / 'manifest.json', dict(schema_version='knowledge-manifest/1.0', datasets={'bad': '../outside.json'}))
    with pytest.raises(ValueError, match='escapes root'):
        Repository(tmp_path).acquire_snapshot()


def test_exact_alias_chinese_keyword_and_strict_filters(knowledge):
    _, service = knowledge
    assert service.search('card.strike')['entries'][0]['knowledge_id'] == 'CARD.STRIKE'
    assert service.search('基础攻击')['entries'][0]['match'] == 'exact'
    result = service.search('手牌溢出', include_related=True)
    assert result['backend'] == 'sqlite_fts5' and result['model_calls'] == 0
    assert result['entries'][0]['knowledge_id'] == 'mechanics:capacity'
    assert service.search('生成牌')['entries'][0]['knowledge_id'] == 'mechanics:capacity'
    assert service.search('手牌溢出', dataset='catalog')['entries'] == []
    assert service.search('打击', game_version='wrong')['entries'] == []
    assert service.search('打击', assembly_hash='wrong')['entries'] == []
    assert service.search('打击', language='wrong')['entries'] == []
    assert service.search('完全不存在abcdef')['entries'] == []
    assert result['entries'][0]['source_ref']['path'].endswith('mechanics.json')


def test_related_results_obey_filters(knowledge):
    _, service = knowledge
    result = service.search('手牌溢出', limit=1, include_related=True, dataset='mechanics')
    assert [e['knowledge_id'] for e in result['related_entries']] == ['mechanics:draw']
    assert service.search('手牌溢出', game_version='wrong', include_related=True)['related_entries'] == []


def test_monster_build_ascension_and_source_program_exclusion(knowledge):
    repository, service = knowledge
    write(repository.root / 'behavior.json', dict(schema_version='behavior/1.0', game_version='test',
        game_assembly_sha256='build-one', entries=[dict(entity_id='MONSTER.TEST', name='测试怪物',
            category='monster', provenance=dict(source='fixture'), calculation_status='source_extracted_unreviewed',
            health_variants=[dict(ascension_min=0, ascension_max=7, min_hp=10, max_hp=12),
                             dict(ascension_min=8, ascension_max=10, min_hp=14, max_hp=16)],
            moves=[dict(title='尖牙攻击', execution_evidence='private_source_program_marker',
                        numeric_variants=[dict(ascension_min=0, ascension_max=7, damage=4),
                                          dict(ascension_min=8, ascension_max=10, damage=6)])])]))
    assert service.search('尖牙攻击')['entries'][0]['knowledge_id'] == 'MONSTER.TEST'
    assert not service.search('private_source_program_marker')['entries']
    monster = service.get_monster('MONSTER.TEST', ascension=8, game_version='test', assembly_hash='build-one')
    assert monster['entry']['health_variants'][0]['min_hp'] == 14
    assert monster['entry']['moves'][0]['numeric_variants'][0]['damage'] == 6
    assert monster['rules_enabled'] is False
    assert service.get_monster('MONSTER.TEST', ascension=8, game_version='test', assembly_hash='wrong')['error']


def test_index_revision_update_and_old_snapshot_stay_independent(knowledge):
    repository, service = knowledge
    old = repository.acquire_snapshot()
    service.search('打击', snapshot=old)
    payload = repository.read(repository.root / 'catalog.json')
    payload['entries'] = [payload['entries'][1]]
    write(repository.root / 'catalog.json', payload)
    current = repository.acquire_snapshot()
    assert service.search('打击', snapshot=current)['entries'] == []
    assert service.search('打击', snapshot=old)['entries'][0]['knowledge_id'] == 'CARD.STRIKE'
    assert (service.cache_root / current.revision / 'lexical.sqlite').is_file()


def test_corrupted_index_rebuild_and_fts_unavailable_fallback(knowledge, monkeypatch):
    repository, service = knowledge
    snapshot = repository.acquire_snapshot()
    service.search('手牌溢出', snapshot=snapshot)
    (service.cache_root / snapshot.revision / 'lexical.sqlite').write_bytes(b'corrupted')
    assert service.search('手牌溢出', snapshot=snapshot)['backend'] == 'sqlite_fts5'
    def unavailable(*args):
        raise sqlite3.OperationalError('no such module: fts5')
    monkeypatch.setattr(service, '_index', unavailable)
    result = service.search('生成牌', snapshot=snapshot)
    assert result['backend'] == 'memory_keywords'
    assert result['entries'][0]['knowledge_id'] == 'mechanics:capacity'


def test_context_budget_and_timing_environment(knowledge):
    _, service = knowledge
    context = service.build_context(['CARD.STRIKE', 'RELIC.FLOWER', 'CARD.UNKNOWN'], max_bytes=2)
    assert context['entries'] == []
    assert context['omitted_ids'] == ['CARD.STRIKE', 'RELIC.FLOWER']
    assert context['missing_ids'] == ['CARD.UNKNOWN']
    assert service.get_timing_profile('action_settlement', {'device': 'fixture'})['entries'][0]['samples'] == 20
    assert service.get_timing_profile('action_settlement', {'device': 'other'})['gap']


def test_agent_context_reuses_report_revision_after_refresh(knowledge, monkeypatch):
    repository, service = knowledge
    from autoslay.agent import context
    from autoslay.knowledge_base import repository as repository_module
    monkeypatch.setattr(repository_module, 'REPOSITORY', repository)
    monkeypatch.setattr(context, 'SERVICE', service)
    old = repository.acquire_snapshot()
    payload = repository.read(repository.root / 'catalog.json')
    payload['entries'][0]['name'] = '新打击'
    write(repository.root / 'catalog.json', payload)
    repository.acquire_snapshot()
    snapshot = dict(game_version='test', state=dict(player=dict(hand=[dict(entity_id='CARD.STRIKE')])))
    report = dict(knowledge_revision=old.revision, source={}, phase='combat')
    packet = context.build(snapshot, report)
    assert packet['knowledge_revision'] == old.revision
    assert packet['reference_entries'][0]['name'] == '打击'


def test_experience_publish_validation_history_and_sample_gap(knowledge):
    repository, service = knowledge
    from autoslay.knowledge_base.experience import publish
    payload = repository.read(repository.root / 'timing.json')
    invalid = json.loads(json.dumps(payload))
    invalid['entries'][0]['p95_ms'] = float('nan')
    with pytest.raises(ValueError):
        publish(invalid, 'timing', repository)
    assert repository.read(repository.root / 'timing.json')['entries'][0]['p95_ms'] == 40
    pending = json.loads(json.dumps(payload))
    pending['entries'][0]['review_status'] = 'pending_review'
    with pytest.raises(ValueError, match='staging'):
        publish(pending, 'timing', repository)
    payload['entries'][0]['samples'] = 1
    result = publish(payload, 'timing', repository)
    assert result['changed'] and not result['rules_enabled']
    assert list((repository.root / 'history').glob('*.json'))
    assert service.get_timing_profile('action_settlement', {'device': 'fixture'})['gap']


def test_dashboard_knowledge_endpoints_and_origin_guard(knowledge, monkeypatch, tmp_path):
    _, service = knowledge
    from autoslay.dashboard import server
    monkeypatch.setattr(server, 'knowledge_service', service)
    http = server.make_server(0, tmp_path / 'archive')
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(http.server_port)
    try:
        with pytest.raises(HTTPError) as exc:
            urlopen(base + '/api/knowledge/status')
        assert exc.value.code == 403
        headers = {'X-Autoslay-ReadOnly': '1'}
        with urlopen(Request(base + '/api/knowledge/status', headers=headers)) as response:
            status = json.load(response)
        assert status['model_calls'] == 0
        with urlopen(Request(base + '/api/knowledge/search?q=card.strike', headers=headers)) as response:
            result = json.load(response)
        assert result['entries'][0]['knowledge_id'] == 'CARD.STRIKE'
        assert result['knowledge_revision'] == status['knowledge_revision']
    finally:
        http.shutdown()
        thread.join(timeout=3)
        http.server_close()


@pytest.mark.parametrize('query,limit', [('', 10), (' ', 10), ('x' * 501, 10), ('打击', 0), ('打击', 51), ('打击', True)])
def test_search_rejects_invalid_input(knowledge, query, limit):
    _, service = knowledge
    with pytest.raises(ValueError):
        service.search(query, limit=limit)
