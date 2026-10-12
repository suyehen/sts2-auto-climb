"""Interchangeable inference transports. Bounded inference retries never dispatch actions."""
import asyncio
import json
import os
import re
import time
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from autoslay.paths import ROOT
from .contracts import prompt, schema_for

class ProviderError(Exception):
    """Sanitized code: no provider response, credentials, or stderr in public errors."""
    def __init__(self, code, details=None):
        super().__init__(code)
        self.details = details or {}


def resolve_codex_executable(configured):
    """Respect explicit paths; recover the desktop CLI when service PATH is stale."""
    executable = shutil.which(configured)
    if executable or configured != 'codex' or sys.platform != 'win32':
        return executable
    roots = []
    if local := os.environ.get('LOCALAPPDATA'):
        roots.append(Path(local))
    # Desktop sandbox processes can receive a redirected LOCALAPPDATA.
    if user := os.environ.get('USERPROFILE'):
        roots.append(Path(user) / 'AppData' / 'Local')
    for root in dict.fromkeys(roots):
        directory = root / 'OpenAI' / 'Codex' / 'bin'
        try:
            candidates = [p for p in directory.glob('*/codex.exe') if p.is_file()]
            direct = directory / 'codex.exe'
            if direct.is_file():
                candidates.append(direct)
            candidates.sort(key=lambda p: (p.stat().st_mtime_ns, str(p)), reverse=True)
            if candidates:
                return str(candidates[0])
        except OSError:
            continue
    return None


class APIProvider:
    def __init__(self, profile, key='', transport=None):
        self.profile, self.key, self.transport = profile, key, transport
        self.on_retry = None

    async def decide(self, packet):
        # Only the official DeepSeek transport has reviewed recovery limits.
        recover=urlsplit(self.profile.base_url).hostname=='api.deepseek.com'
        budget=self.profile.max_tokens
        enlarged=False
        consumed={}
        for attempt in range(1,4 if recover else 2):
            try:
                raw,usage=await self.decide_once(packet,budget)
                if attempt>1:
                    usage={k:usage.get(k,0)+consumed.get(k,0) for k in set(usage)|set(consumed)}
                    usage['request_attempts']=attempt
                return raw,usage
            except ProviderError as exc:
                for k,v in exc.details.get('usage',{}).items():consumed[k]=consumed.get(k,0)+v
                temporary=str(exc) in {'MODEL_TIMEOUT','MODEL_UNAVAILABLE','MODEL_HTTP_429','MODEL_GENERATION_INTERRUPTED'} or bool(re.fullmatch(r'MODEL_HTTP_5\d\d',str(exc)))
                grow=str(exc)=='MODEL_OUTPUT_LIMIT' and not enlarged and budget<131072
                if not recover or attempt==3 or not (grow or temporary):
                    if attempt>1:exc.details.update(attempts=attempt,total_usage=consumed)
                    raise
                if grow:
                    budget=min(budget*2,131072);enlarged=True
                if self.on_retry:self.on_retry(dict(attempt=attempt+1,retry_reason=str(exc),max_tokens=budget,started_at=time.time()))
                if not grow:await asyncio.sleep(attempt)

    async def decide_once(self, packet, max_tokens):
        p = self.profile
        if not p.model or p.kind == 'api' and not self.key: raise ProviderError('PROVIDER_NOT_CONFIGURED')
        body = dict(model=p.model, messages=[dict(role='user', content=prompt(packet))],
                    stream=False, max_tokens=max_tokens)
        if p.output_mode == 'json': body['response_format'] = dict(type='json_object')
        if p.output_mode == 'schema':
            body['response_format'] = dict(type='json_schema', json_schema=dict(
                name='game_decision', strict=True, schema=schema_for(packet)))
        if p.reasoning_effort: body['reasoning_effort'] = p.reasoning_effort
        headers = {'Authorization': 'Bearer ' + self.key} if self.key else {}
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                        timeout=p.timeout_seconds, transport=self.transport) as client:
                # Bound total request time AND response size, including whitespace-only output.
                async with asyncio.timeout(p.timeout_seconds):
                    async with client.stream('POST', p.base_url.rstrip('/') + '/chat/completions',
                                             json=body, headers=headers) as r:
                        if r.status_code != 200: raise ProviderError('MODEL_HTTP_' + str(r.status_code))
                        chunks = bytearray()
                        async for chunk in r.aiter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > 2_000_000: raise ProviderError('MODEL_RESPONSE_TOO_LARGE')
            data = json.loads(chunks)
            choice = data['choices'][0]
            if choice['finish_reason'] != 'stop':
                finish=choice['finish_reason']
                details=dict(finish_reason=finish if finish in {'length','content_filter','tool_calls','function_call','insufficient_system_resource','aborted'} else 'other',
                             max_tokens=max_tokens)
                details['usage']={k:v for k,v in (data.get('usage') or {}).items()
                                  if k in {'prompt_tokens','completion_tokens','total_tokens'} and type(v) is int}
                raise ProviderError('MODEL_OUTPUT_LIMIT' if finish=='length' else 'MODEL_GENERATION_INTERRUPTED' if finish in {'insufficient_system_resource','aborted'} else 'MODEL_RESPONSE_INCOMPLETE',details)
            msg = choice['message']
            if msg.get('refusal') or msg.get('tool_calls'): raise ProviderError('MODEL_REFUSAL_OR_TOOL_CALL')
            if not isinstance(msg.get('content'), str) or not msg['content'].strip():
                raise ProviderError('MODEL_EMPTY_RESPONSE')
            # Persist only standardized integer usage counters, never arbitrary response fields.
            usage = {k: v for k, v in (data.get('usage') or {}).items()
                     if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'} and type(v) is int}
            return msg['content'], usage
        except (httpx.TimeoutException, TimeoutError): raise ProviderError('MODEL_TIMEOUT') from None
        except httpx.RequestError: raise ProviderError('MODEL_UNAVAILABLE') from None
        except (ValueError, KeyError, IndexError, TypeError): raise ProviderError('MODEL_INVALID_RESPONSE') from None


class CodexProvider:
    """Official noninteractive CLI interface, reuses login without reading OAuth tokens.

    Isolated ephemeral invocations avoid loading user MCPs/plugins or project instructions.
    The CLI manages subscription authentication and its own sandbox.
    """
    def __init__(self, profile): self.profile = profile

    async def decide(self, packet):
        p = self.profile
        exe = resolve_codex_executable(p.codex_executable)
        if not exe: raise ProviderError('CODEX_NOT_INSTALLED')
        base = ROOT / '.runtime/cache/agent-codex'
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            directory = Path(directory)
            schema, output = directory/'schema.json', directory/'result.json'
            schema.write_text(json.dumps(schema_for(packet)), encoding='utf-8')
            args = [exe, 'exec', '--skip-git-repo-check', '--ephemeral', '--ignore-user-config',
                    '--ignore-rules', '--sandbox', 'read-only', '--color', 'never',
                    '--output-schema', str(schema), '--output-last-message', str(output),
                    '-C', str(directory)]
            for feature in ('shell_tool', 'apps', 'computer_use', 'browser_use', 'in_app_browser',
                            'hooks', 'unbounded_connection_retries'):
                args += ['--disable', feature]
            args += ['-c', 'sandbox_read_only.network_access=false', '-c', 'web_search="disabled"']
            # Ignore user plugins/rules while retaining a reliable HTTP transport.
            # The built-in provider may use WebSockets, which can stall on this host.
            args += ['-c', 'model_provider="autoslay_http"', '-c',
                     'model_providers.autoslay_http={name="OpenAI HTTP only",'
                     'wire_api="responses",supports_websockets=false,requires_openai_auth=true}']
            if p.model: args += ['--model', p.model]
            if p.reasoning_effort: args += ['-c', 'model_reasoning_effort="'+p.reasoning_effort+'"']
            args.append('-')
            # API environment variables must not silently switch this backend away from ChatGPT login.
            env = {k: v for k, v in os.environ.items() if k not in {'OPENAI_API_KEY', 'CODEX_API_KEY'}}
            try:
                proc = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except OSError: raise ProviderError('CODEX_START_FAILED') from None
            try:
                await asyncio.wait_for(proc.communicate(prompt(packet).encode('utf-8')), p.timeout_seconds)
                if proc.returncode != 0: raise ProviderError('CODEX_LOGIN_OR_REQUEST_FAILED')
                if not output.exists() or output.stat().st_size > 100_000:
                    raise ProviderError('CODEX_INVALID_RESPONSE')
                return output.read_text(encoding='utf-8'), {}
            except TimeoutError: raise ProviderError('MODEL_TIMEOUT') from None
            finally:
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()


def create(profile, key=''):
    return CodexProvider(profile) if profile.kind == 'codex' else APIProvider(profile, key)
