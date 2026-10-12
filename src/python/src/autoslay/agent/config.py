"""Non-secret profiles and Windows user-bound encrypted credentials."""
import base64
import ctypes
import json
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, model_validator
from autoslay.paths import ROOT

DIRECTORY = ROOT / '.runtime/local/agent'


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


class Profile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,60}$')
    label: str = Field(min_length=1, max_length=100)
    kind: Literal['codex', 'api', 'local']
    base_url: str = ''
    model: str = Field(default='', max_length=200)
    output_mode: Literal['json', 'schema', 'text'] = 'json'
    timeout_seconds: int = Field(default=120, ge=5, le=600)
    max_tokens: int = Field(default=4096, ge=256, le=32768)
    reasoning_effort: Literal['', 'none', 'low', 'medium', 'high', 'max'] = ''
    key_env: str = Field(default='', pattern=r'^[A-Za-z_][A-Za-z0-9_]*$|^$')
    codex_executable: str = 'codex'

    @model_validator(mode='after')
    def endpoint(self):
        if self.kind != 'codex':
            u = urlsplit(self.base_url)
            loopback = u.hostname in {'localhost', '127.0.0.1', '::1'}
            if u.username or u.password or u.query or u.fragment or not u.hostname:
                raise ValueError('INVALID_ENDPOINT')
            if u.scheme != 'https' and not (u.scheme == 'http' and loopback):
                raise ValueError('HTTPS_OR_LOOPBACK_REQUIRED')
            if self.kind == 'local' and not loopback:
                raise ValueError('LOCAL_ENDPOINT_MUST_BE_LOOPBACK')
        return self


def defaults():
    return [
        Profile(id='codex', label='本机 Codex 登录', kind='codex'),
        Profile(id='deepseek', label='DeepSeek', kind='api', base_url='https://api.deepseek.com',
                model='deepseek-flash', key_env='DEEPSEEK_API_KEY'),
        Profile(id='openai', label='OpenAI API', kind='api', base_url='https://api.openai.com/v1',
                output_mode='schema', key_env='OPENAI_API_KEY'),
        Profile(id='ollama', label='本地 Ollama', kind='local', base_url='http://127.0.0.1:11434/v1'),
        Profile(id='lmstudio', label='本地 LM Studio', kind='local', base_url='http://127.0.0.1:1234/v1'),
        Profile(id='custom', label='其他 OpenAI 兼容服务', kind='api', base_url='https://example.com/v1'),
    ]


def protect(value, decrypt=False):
    if os.name != 'nt':
        raise ValueError('WINDOWS_CREDENTIAL_STORE_REQUIRED_USE_ENV')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buf = ctypes.create_string_buffer(value)
    src = Blob(len(value), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
    dst = Blob()
    # UI_FORBIDDEN: user-bound DPAPI, without an interactive prompt.
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(src), None, None, None, None, 1, ctypes.byref(dst)):
        raise ValueError('CREDENTIAL_STORE_FAILED')
    try:
        return ctypes.string_at(dst.data, dst.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(ctypes.cast(dst.data, ctypes.c_void_p))


class Store:
    def __init__(self, directory=DIRECTORY, cipher=protect):
        self.directory, self.cipher = Path(directory), cipher
        self.path = self.directory / 'profiles.json'
        self.secret_path = self.directory / 'credentials.dpapi.json'
        self.profiles = {p.id: p for p in defaults()}
        self.active = 'deepseek'
        self.decision={};self.ui={'display_mode':'human'};self.credential_invalidated_ids=set()
        if self.path.exists():
            from autoslay.configuration import migrate_store
            value = migrate_store(json.loads(self.path.read_text(encoding='utf-8-sig')))
            self.profiles = {p.id: p for p in map(Profile.model_validate, value['profiles'])}
            self.active = value['active']
            self.decision=value['decision'];self.ui=value['ui'];self.credential_invalidated_ids=set(value['credential_invalidated_ids'])
            if self.active not in self.profiles: raise ValueError('INVALID_ACTIVE_PROFILE')

    def save(self):
        if self.path.exists():
            previous=json.loads(self.path.read_text(encoding='utf-8-sig'))
            if previous.get('version')!=2:
                from autoslay.configuration import backup_stamp
                stamp=backup_stamp(self.directory)
                atomic(self.directory/'backups'/(stamp+'.json'),previous)
                if self.secret_path.exists(): atomic(self.directory/'backups'/(stamp+'.credentials.dpapi.json'),json.loads(self.secret_path.read_text()))
        atomic(self.path,self.configuration())

    def configuration(self):
        return dict(version=2,active=self.active,profiles=[p.model_dump() for p in self.profiles.values()],
                    decision=self.decision,ui=self.ui,credential_invalidated_ids=sorted(self.credential_invalidated_ids))

    def key(self, profile):
        if profile.id in self.credential_invalidated_ids: return ''
        if profile.key_env and os.environ.get(profile.key_env):
            return os.environ[profile.key_env]
        values = json.loads(self.secret_path.read_text()) if self.secret_path.exists() else {}
        return self.cipher(base64.b64decode(values[profile.id]), True).decode() if profile.id in values else ''

    def set_key(self, identity, key):
        if identity not in self.profiles: raise ValueError('UNKNOWN_PROFILE')
        if not isinstance(key, str) or len(key) > 8192 or '\n' in key or '\r' in key:
            raise ValueError('INVALID_KEY')
        values = json.loads(self.secret_path.read_text()) if self.secret_path.exists() else {}
        if key: values[identity] = base64.b64encode(self.cipher(key.encode())).decode()
        else: values.pop(identity, None)
        atomic(self.secret_path, values)
        if identity in self.credential_invalidated_ids:
            self.credential_invalidated_ids.remove(identity);self.save()

    def public(self):
        rows = []
        for p in self.profiles.values():
            try: configured = bool(self.key(p))
            except ValueError: configured = False
            rows.append(p.model_dump() | dict(key_configured=configured,
                ready=p.kind == 'codex' or bool(p.model) and (p.kind == 'local' or configured)))
        return dict(active=self.active, profiles=rows)
