import asyncio
import json
from copy import deepcopy
from unittest.mock import AsyncMock

import pytest
from autoslay import configuration as c
from autoslay.agent.config import Store
from autoslay.agent.runtime import Agent


def cipher(value, decrypt=False):
    return value[7:] if decrypt else b'sealed:'+value


@pytest.fixture
def store(tmp_path, monkeypatch):
    for name in ('DEEPSEEK_API_KEY', 'OPENAI_API_KEY'):
        monkeypatch.delenv(name, raising=False)
    s=Store(tmp_path/'agent', cipher)
    s.set_key('deepseek', 'test-private-key')
    s.save()
    return s


def test_standard_scope_and_preview(store):
    p=c.export_package(store)
    text=json.dumps(p)
    assert 'test-private-key' not in text
    assert set(p['settings'])=={'agent','decision','ui'}
    assert set(p['settings']['decision'])==set(c.POLICIES)
    before=store.path.read_bytes()
    assert c.preview(store,p)['credential_count']==0
    assert store.path.read_bytes()==before


def test_complete_portable_encryption_and_restore(store,tmp_path):
    p=c.export_package(store, mode='complete',password='test-password')
    assert 'test-private-key' not in json.dumps(p) and 'test-password' not in json.dumps(p)
    assert p!=c.export_package(store,mode='complete',password='test-password')
    view=c.preview(store,p,'test-password')
    assert view['credentials_included'] and view['credential_count']==1
    assert 'test-private-key' not in json.dumps(view)
    other=Store(tmp_path/'other',cipher)
    c.install(other,p,'test-password')
    loaded=Store(other.directory,cipher)
    assert loaded.key(loaded.profiles['deepseek'])=='test-private-key'


def test_wrong_password_and_tamper_leave_files_unchanged(store):
    p=c.export_package(store,mode='complete',password='test-password')
    before=store.path.read_bytes(),store.secret_path.read_bytes()
    for altered,password in [(p,'wrong-password'),(p|{'nonce':'AAAAAAAAAAAAAAAA'},'test-password')]:
        with pytest.raises(ValueError): c.install(store,altered,password)
        assert (store.path.read_bytes(),store.secret_path.read_bytes())==before


def test_legacy_migration_and_future_rejection(store):
    legacy=dict(version=1,active=store.active,profiles=[p.model_dump() for p in store.profiles.values()])
    migrated,info=c.migrate_export(legacy)
    assert info['migrated'] and migrated['decision']['run']==c.baseline('run')
    store.path.write_text(json.dumps(legacy))
    before=store.path.read_bytes()
    assert Store(store.directory,cipher).active==store.active
    assert store.path.read_bytes()==before
    p=c.export_package(store)
    for bad in [p|{'schema_version':999},p|{'settings':p['settings']|{'runs':[]}},p|{'api_key':'plain'}]:
        with pytest.raises(ValueError):c.install(store,bad)
        assert store.path.read_bytes()==before


def test_identity_binding_and_backup_restores_credentials(store,monkeypatch):
    p=c.export_package(store)
    c.install(store,p)
    assert store.key(store.profiles['deepseek'])=='test-private-key'
    changed=deepcopy(p)
    next(v for v in changed['settings']['agent']['profiles'] if v['id']=='deepseek')['base_url']='https://different.example/v1'
    monkeypatch.setenv('DEEPSEEK_API_KEY','old-environment-key')
    result=c.install(store,changed)
    assert 'deepseek' in result['credentials_need_reentry']
    assert store.key(store.profiles['deepseek'])==''
    monkeypatch.delenv('DEEPSEEK_API_KEY')
    store.set_key('deepseek','new-key')
    c.restore(store,result['backup_id'])
    assert store.key(store.profiles['deepseek'])=='test-private-key'
    assert len(c.backups(store))==3
    with pytest.raises(ValueError):c.restore(store,'../credentials.dpapi')


@pytest.mark.parametrize('mutate',[
    lambda p:p['settings']['decision']['run'].update(hp_weight=float('nan')),
    lambda p:p['settings']['decision']['autonomy'].update(complex_strategy_enabled=True),
    lambda p:p['settings']['decision']['analysis']['normal'].update(time_ms=99999),
    lambda p:p['settings']['agent']['profiles'].append(p['settings']['agent']['profiles'][0]),
])
def test_invalid_values_are_rejected_before_write(store,mutate):
    p=c.export_package(store);mutate(p);before=store.path.read_bytes()
    with pytest.raises(ValueError):c.install(store,p)
    assert store.path.read_bytes()==before


def test_policy_override_is_local_and_used_by_reader(store,tmp_path,monkeypatch):
    root=tmp_path/'root'
    for name in c.POLICIES:
        target=root/'config/decision'/f'{name}.default.json'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(c.baseline(name)))
    monkeypatch.setattr(c,'ROOT',root)
    local=Store(root/'.runtime/local/agent',cipher)
    p=c.export_package(local)
    p['settings']['decision']['run']['hp_weight']=7
    c.install(local,p)
    path=root/'config/decision/run.default.json'
    assert c.decision_config(path)['hp_weight']==7
    assert json.loads(path.read_text())['hp_weight']==2


def test_failed_config_write_rolls_back_credentials(store,monkeypatch):
    from autoslay.agent import config
    other=Store(store.directory/'other',cipher);other.set_key('deepseek','replacement')
    p=c.export_package(other,mode='complete',password='test-password')
    original=config.atomic
    def fail(path,value):
        if path==store.path: raise OSError('simulated write failure')
        return original(path,value)
    monkeypatch.setattr(config,'atomic',fail)
    before=store.path.read_bytes(),store.secret_path.read_bytes()
    with pytest.raises(OSError):c.install(store,p,'test-password')
    assert (store.path.read_bytes(),store.secret_path.read_bytes())==before
    assert store.key(store.profiles['deepseek'])=='test-private-key'


def test_agent_import_pauses_without_provider_or_game_action(store):
    async def run():
        control=AsyncMock();factory=AsyncMock();submit=AsyncMock()
        agent=Agent(store,control=control,factory=factory,submit=submit,record=False)
        invalid=c.export_package(store)|{'schema_version':999}
        with pytest.raises(ValueError):await agent.command('/config/import',{'package':invalid})
        control.assert_not_called()
        await agent.command('/config/import',{'package':c.export_package(store)})
        control.assert_awaited_once_with('paused')
        factory.assert_not_called();submit.assert_not_called()
        assert not agent.running
    asyncio.run(run())
