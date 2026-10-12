"""Portable, versioned non-secret configuration and local policy overrides."""
import json
import math
import base64
import os
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
from autoslay.paths import ROOT

FORMAT='autoslay-config'
VERSION=2
POLICIES=('run','combat','context','analysis','autonomy','relic-timing')
SECRETS={'api_key','apikey','token','password','credentials','secret','authorization'}


def no_secrets(value):
    if isinstance(value,dict):
        if any(str(k).lower() in SECRETS for k in value): raise ValueError('CONFIG_CONTAINS_CREDENTIALS')
        for v in value.values(): no_secrets(v)
    elif isinstance(value,list):
        for v in value: no_secrets(v)


def merge(default,value):
    if not isinstance(value,dict) or value.keys()-default.keys(): raise ValueError('CONFIG_UNKNOWN_FIELDS')
    result=deepcopy(default)
    for key,v in value.items():
        d=default[key]
        if isinstance(d,dict): result[key]=merge(d,v)
        elif isinstance(d,list):
            if not isinstance(v,list) or any(x not in d for x in v) or len(v)!=len(set(v)): raise ValueError('CONFIG_INVALID_LIST')
            result[key]=v
        elif type(d) is bool:
            if type(v) is not bool: raise ValueError('CONFIG_INVALID_TYPE')
            result[key]=v
        elif type(d) in (float,int):
            if type(v) not in (float,int) or not math.isfinite(v) or v<0 or (type(d) is int and type(v) is not int): raise ValueError('CONFIG_INVALID_NUMBER')
            result[key]=v
        elif isinstance(d,str):
            if not isinstance(v,str) or v!=d: raise ValueError('CONFIG_POLICY_VERSION_UNSUPPORTED')
            result[key]=v
    return result


def baseline(name):
    if name not in POLICIES: raise ValueError('CONFIG_UNKNOWN_POLICY')
    return json.loads((ROOT/'config/decision'/f'{name}.default.json').read_text(encoding='utf-8-sig'))


def validate_policies(values):
    if not isinstance(values,dict) or values.keys()-set(POLICIES): raise ValueError('CONFIG_UNKNOWN_POLICY')
    result={name:merge(baseline(name),values.get(name,{})) for name in POLICIES}
    from autoslay.evaluation.run import RunPolicy
    from autoslay.evaluation.combat import CombatPolicy
    from autoslay.evaluation.contextual import InteractionPolicy
    from autoslay.evaluation.relic_timing import TimingPolicy
    from autoslay.planning.search import SearchBudget
    RunPolicy(**result['run']);CombatPolicy(**result['combat']);InteractionPolicy(**result['context']);TimingPolicy(**result['relic-timing'])
    a=result['analysis'];SearchBudget(**a['normal']);SearchBudget(**a['danger'])
    l=a['lookahead']
    if not 0<a['low_hp_fraction']<=1 or not 1<=l['horizon']<=3 or not 1<=l['branches']<=32 or not 1<=l['time_ms']<=3000 or not 0<=l['draws']<=10 or not 0<=l['energy']<=10:
        raise ValueError('CONFIG_INVALID_BUDGET')
    gate=result['autonomy']
    if gate['minimum_probability_coverage']!=1 or not gate['require_fresh_action_validation'] or not gate['require_future_threat_coverage'] or gate['complex_strategy_enabled']:
        raise ValueError('CONFIG_PROOF_REQUIREMENTS_FIXED')
    return result


def migrate_store(value):
    """Legacy profiles v1 remain readable; absent new settings use current defaults."""
    from autoslay.agent.config import Profile
    if not isinstance(value,dict) or type(value.get('version')) is not int or value['version'] not in (1,2): raise ValueError('CONFIG_VERSION_UNSUPPORTED')
    if value.keys()-{'version','active','profiles','decision','ui','credential_invalidated_ids'}: raise ValueError('CONFIG_UNKNOWN_FIELDS')
    profiles=[Profile.model_validate(p).model_dump() for p in value['profiles']]
    ids=[p['id'] for p in profiles]
    if not 1<=len(ids)<=40 or len(set(ids))!=len(ids) or value['active'] not in ids: raise ValueError('CONFIG_INVALID_ACTIVE_PROFILE')
    ui=value.get('ui',{})
    if not isinstance(ui,dict) or ui.keys()-{'display_mode'} or ui.get('display_mode','human') not in ('human','engineering'): raise ValueError('CONFIG_INVALID_UI')
    invalid=value.get('credential_invalidated_ids',[])
    if not isinstance(invalid,list) or any(not isinstance(x,str) for x in invalid): raise ValueError('CONFIG_INVALID_CREDENTIAL_BINDING')
    return dict(version=2,active=value['active'],profiles=profiles,decision=validate_policies(value.get('decision',{})),
                ui=dict(display_mode=ui.get('display_mode','human')),credential_invalidated_ids=invalid)


def migrate_export(value):
    no_secrets(value)
    if not isinstance(value,dict): raise ValueError('CONFIG_INVALID_FORMAT')
    legacy='format' not in value and value.get('version')==1
    if legacy: current=migrate_store(value);old=1
    else:
        if value.get('format')!=FORMAT or type(value.get('schema_version')) is not int or value['schema_version'] not in (1,2): raise ValueError('CONFIG_VERSION_UNSUPPORTED')
        if value.keys()-{'format','schema_version','exported_at','producer','settings'}: raise ValueError('CONFIG_UNKNOWN_FIELDS')
        old=value['schema_version'];settings=value['settings']
        if not isinstance(settings,dict) or settings.keys()-{'agent','decision','ui'}: raise ValueError('CONFIG_UNKNOWN_FIELDS')
        current=migrate_store(dict(version=2,**settings['agent'],decision=settings.get('decision',{}),ui=settings.get('ui',{})))
    # Portable packages cannot install an executable path from another machine.
    reset=[]
    for p in current['profiles']:
        if p['codex_executable']!='codex': reset.append(p['id'])
        p['codex_executable']='codex'
    return current,dict(from_version=old,to_version=VERSION,migrated=old!=VERSION,reset_executable_profiles=reset)


def export_store(store,ui=None):
    value=migrate_store(store.configuration())
    profiles=deepcopy(value['profiles'])
    for p in profiles:p['codex_executable']='codex'
    selected_ui=ui if ui is not None else value['ui']
    if selected_ui.keys()-{'display_mode'} or selected_ui.get('display_mode') not in ('human','engineering'): raise ValueError('CONFIG_INVALID_UI')
    return dict(format=FORMAT,schema_version=VERSION,exported_at=datetime.now(timezone.utc).isoformat(),
                producer='AutoSlayTheSpire',settings=dict(agent=dict(active=value['active'],profiles=profiles),decision=value['decision'],ui=selected_ui))


def password_key(password,salt):
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    if not isinstance(password,str) or not 8<=len(password)<=1024: raise ValueError('CONFIG_PASSWORD_REQUIRED')
    return Scrypt(salt=salt,length=32,n=32768,r=8,p=1).derive(password.encode('utf-8'))


def export_package(store,ui=None,mode='standard',password=None):
    package=export_store(store,ui)
    if mode=='standard': return package
    if mode!='complete': raise ValueError('CONFIG_INVALID_EXPORT_MODE')
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt=os.urandom(16);nonce=os.urandom(12);key=password_key(password,salt)
    credentials={p.id:store.key(p) for p in store.profiles.values() if store.key(p)}
    payload=json.dumps(dict(config=package,credentials=credentials),ensure_ascii=False).encode('utf-8')
    encrypted=AESGCM(key).encrypt(nonce,payload,b'autoslay-config-encrypted/1')
    return dict(format='autoslay-config-encrypted',schema_version=1,cipher='AES-256-GCM',kdf='scrypt-32768-8-1',
                salt=base64.b64encode(salt).decode(),nonce=base64.b64encode(nonce).decode(),ciphertext=base64.b64encode(encrypted).decode())


def unpack_package(package,password=None):
    if not isinstance(package,dict): raise ValueError('CONFIG_INVALID_FORMAT')
    if package.get('format')!='autoslay-config-encrypted': return package,None
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.exceptions import InvalidTag
    if package.keys()!={'format','schema_version','cipher','kdf','salt','nonce','ciphertext'} or package['schema_version']!=1 or package['cipher']!='AES-256-GCM' or package['kdf']!='scrypt-32768-8-1': raise ValueError('CONFIG_ENCRYPTION_UNSUPPORTED')
    try:
        salt=base64.b64decode(package['salt'],validate=True);nonce=base64.b64decode(package['nonce'],validate=True)
        ciphertext=base64.b64decode(package['ciphertext'],validate=True)
        if len(salt)!=16 or len(nonce)!=12 or not 16<=len(ciphertext)<=180000: raise ValueError('CONFIG_INVALID_FORMAT')
        plain=AESGCM(password_key(password,salt)).decrypt(nonce,ciphertext,b'autoslay-config-encrypted/1')
        value=json.loads(plain)
        if not isinstance(value,dict) or value.keys()!={'config','credentials'} or not isinstance(value['credentials'],dict): raise ValueError('CONFIG_INVALID_FORMAT')
        config,_=migrate_export(value['config']);ids={p['id'] for p in config['profiles']}
        if value['credentials'].keys()-ids or any(not isinstance(v,str) or not 0<len(v)<=8192 or '\n' in v or '\r' in v for v in value['credentials'].values()): raise ValueError('CONFIG_INVALID_CREDENTIALS')
        return value['config'],value['credentials']
    except InvalidTag: raise ValueError('CONFIG_PASSWORD_OR_FILE_INVALID') from None
    except (TypeError,KeyError,UnicodeError,json.JSONDecodeError): raise ValueError('CONFIG_INVALID_FORMAT') from None


def preview(store,package,password=None):
    package,credentials=unpack_package(package,password)
    new,migration=migrate_export(package);old=migrate_store(store.configuration())
    changed=[name for name in POLICIES if new['decision'][name]!=old['decision'][name]]
    return dict(migration=migration,profiles=[dict(id=p['id'],label=p['label'],kind=p['kind'],base_url=p['base_url'],model=p['model']) for p in new['profiles']],
                active=new['active'],changed_policies=changed,display_mode=new['ui']['display_mode'],
                credentials_included=credentials is not None,credential_count=len(credentials or {}),agent_will_pause=True)


def install(store,package,password=None,*,restored_secrets=None,restored_invalid_ids=None):
    from autoslay.agent.config import atomic,Profile
    package,credentials=unpack_package(package,password)
    new,migration=migrate_export(package)
    old=store.configuration()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=store.directory/'backups'/(stamp+'.json')
    atomic(backup,old)
    original_secrets=json.loads(store.secret_path.read_text()) if store.secret_path.exists() else None
    if original_secrets is not None: atomic(backup.with_name(stamp+'.credentials.dpapi.json'),original_secrets)
    invalid=set(store.credential_invalidated_ids)
    for p in new['profiles']:
        previous=store.profiles.get(p['id'])
        if previous is None or any(getattr(previous,k)!=p[k] for k in ('kind','base_url','key_env')): invalid.add(p['id'])
    prepared_secrets=deepcopy(original_secrets or {})
    if credentials is not None:
        for identity,key in credentials.items():
            prepared_secrets[identity]=base64.b64encode(store.cipher(key.encode('utf-8'))).decode()
            invalid.discard(identity)
    if restored_secrets is not None: prepared_secrets=restored_secrets
    if restored_invalid_ids is not None: invalid=set(restored_invalid_ids)
    credential_change=credentials is not None or restored_secrets is not None
    new['credential_invalidated_ids']=sorted(invalid)
    # All portable settings share one atomic file, including policy overrides.
    try:
        if credential_change: atomic(store.secret_path,prepared_secrets)
        atomic(store.path,new)
    except OSError:
        if credential_change:
            if original_secrets is None: store.secret_path.unlink(missing_ok=True)
            else: atomic(store.secret_path,original_secrets)
        raise
    store.profiles={p['id']:Profile.model_validate(p) for p in new['profiles']};store.active=new['active']
    store.decision=new['decision'];store.ui=new['ui'];store.credential_invalidated_ids=set(new['credential_invalidated_ids'])
    return dict(imported=True,backup_id=stamp,migration=migration,ui=new['ui'],credentials_restored=len(credentials or {}),credentials_need_reentry=sorted(invalid&set(store.profiles)))


def backups(store):
    import re
    return [dict(id=p.stem,created_at=p.stem) for p in sorted((store.directory/'backups').glob('*.json'),reverse=True) if re.fullmatch(r'\d{8}T\d{12}Z',p.stem)][:100]


def restore(store,identity):
    import re
    if not isinstance(identity,str) or not re.fullmatch(r'\d{8}T\d{12}Z',identity): raise ValueError('CONFIG_INVALID_BACKUP_ID')
    value=migrate_store(json.loads((store.directory/'backups'/(identity+'.json')).read_text(encoding='utf-8-sig')))
    package=dict(format=FORMAT,schema_version=VERSION,settings=dict(agent=dict(active=value['active'],profiles=value['profiles']),decision=value['decision'],ui=value['ui']))
    secret_path=store.directory/'backups'/(identity+'.credentials.dpapi.json')
    secret_values=json.loads(secret_path.read_text()) if secret_path.exists() else {}
    return install(store,package,restored_secrets=secret_values,restored_invalid_ids=value['credential_invalidated_ids'])


def decision_config(path):
    path=Path(path)
    name=path.name.removesuffix('.default.json')
    local=ROOT/'.runtime/local/agent/profiles.json'
    if name in POLICIES and path.resolve()==(ROOT/'config/decision'/f'{name}.default.json').resolve() and local.exists():
        value=json.loads(local.read_text(encoding='utf-8-sig'))
        overrides=value.get('decision',{})
        if name in overrides: return merge(baseline(name),overrides[name])
    return json.loads(path.read_text(encoding='utf-8-sig'))
