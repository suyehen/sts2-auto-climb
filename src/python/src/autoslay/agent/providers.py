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
from .contracts import prompt, schema_for, tool_schema_for, tool_result
from autoslay.telemetry.audit import safe_usage, error_code


async def observed_attempt(provider, call, attempt=1, max_tokens=None):
    """Report each transport attempt without exposing response text or credentials."""
    started = time.monotonic()
    def emit(status, **details):
        callback = getattr(provider, 'on_attempt', None)
        if callback:
            try:
                callback(dict(status=status, attempt=attempt, max_tokens=max_tokens,
                              elapsed_ms=round((time.monotonic()-started)*1000), **details))
            except Exception:
                pass  # Instrumentation must not turn a completed request into a retry.
    emit('started')
    try:
        raw, usage = await call()
    except asyncio.CancelledError:
        emit('cancelled')
        raise
    except Exception as exc:
        emit('failed', code=error_code(exc), usage=safe_usage(getattr(exc, 'details', {}).get('usage')))
        raise
    emit('returned', usage=safe_usage(usage))
    return raw, usage


def strict_unsupported(status, payload):
    """Downgrade only an explicit unsupported-feature response, never bad schema/auth."""
    if status != 400 or not isinstance(payload, dict):
        return False
    error = payload.get('error')
    if not isinstance(error, dict):
        return False
    code, param = error.get('code'), error.get('param')
    return (code in {'unsupported_parameter', 'unsupported_value', 'unsupported_feature', 'not_supported'}
            and param in {'tools', 'tool_choice', 'strict', 'tools[0].function.strict'})

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
        self.on_attempt = None
        self.strict_fallback = False

    async def decide(self, packet):
        # Only the official DeepSeek transport has reviewed recovery limits.
        recover=urlsplit(self.profile.base_url).hostname=='api.deepseek.com'
        budget=self.profile.max_tokens
        enlarged=False
        consumed={}
        for attempt in range(1,4 if recover else 2):
            try:
                raw,usage=await observed_attempt(self, lambda: self.decide_once(packet,budget), attempt, budget)
                if attempt>1:
                    usage={k:usage.get(k,0)+consumed.get(k,0) for k in set(usage)|set(consumed)}
                    usage['request_attempts']=attempt
                return raw,usage
            except ProviderError as exc:
                for k,v in exc.details.get('usage',{}).items():consumed[k]=consumed.get(k,0)+v
                temporary=str(exc) in {'MODEL_TIMEOUT','MODEL_UNAVAILABLE','MODEL_HTTP_429','MODEL_GENERATION_INTERRUPTED','MODEL_EMPTY_RESPONSE'} or bool(re.fullmatch(r'MODEL_HTTP_5\d\d',str(exc)))
                grow=str(exc)=='MODEL_OUTPUT_LIMIT' and not enlarged and budget<131072
                fallback = str(exc)=='MODEL_STRICT_UNSUPPORTED' and not self.strict_fallback
                if not recover or attempt==3 or not (grow or temporary or fallback):
                    if attempt>1:exc.details.update(attempts=attempt,total_usage=consumed)
                    raise
                if grow:
                    budget=min(budget*2,131072);enlarged=True
                if fallback:
                    self.strict_fallback = True
                if self.on_retry:self.on_retry(dict(attempt=attempt+1,retry_reason=str(exc),max_tokens=budget,started_at=time.time()))
                if not (grow or fallback):await asyncio.sleep(attempt)

    async def decide_once(self, packet, max_tokens):
        p = self.profile
        if not p.model or p.kind == 'api' and not self.key: raise ProviderError('PROVIDER_NOT_CONFIGURED')
        strict = p.output_mode == 'deepseek_strict' and not self.strict_fallback
        endpoint = p.base_url.rstrip('/') + '/chat/completions'
        body = dict(model=p.model, messages=[dict(role='user', content=prompt(packet, tool_mode=strict))],
                    stream=False, max_tokens=max_tokens)
        if p.output_mode == 'json' or self.strict_fallback: body['response_format'] = dict(type='json_object')
        if strict:
            endpoint = 'https://api.deepseek.com/beta/chat/completions'
            body['tools'] = [dict(type='function', function=dict(name='submit_decision', strict=True,
                description='Submit a proposed game decision for local validation. This does not execute game actions.',
                parameters=tool_schema_for(packet)))]
            # Thinking mode accepts auto only; named/required choices return 400.
            body['tool_choice'] = (dict(type='function', function=dict(name='submit_decision'))
                if p.reasoning_effort == 'none' else 'auto')
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
                    async with client.stream('POST', endpoint,
                                             json=body, headers=headers) as r:
                        chunks = bytearray()
                        async for chunk in r.aiter_bytes():
                            chunks.extend(chunk)
                            if len(chunks) > 2_000_000: raise ProviderError('MODEL_RESPONSE_TOO_LARGE')
                        if r.status_code != 200:
                            try: error_payload = json.loads(chunks)
                            except (ValueError, UnicodeError): error_payload = None
                            if strict and strict_unsupported(r.status_code, error_payload):
                                raise ProviderError('MODEL_STRICT_UNSUPPORTED')
                            raise ProviderError('MODEL_HTTP_' + str(r.status_code))
            data = json.loads(chunks)
            choice = data['choices'][0]
            usage = {k: v for k, v in (data.get('usage') or {}).items()
                     if k in {'prompt_tokens', 'completion_tokens', 'total_tokens'} and type(v) is int and v >= 0}
            reasoning=(data.get('usage') or {}).get('completion_tokens_details') or {}
            if type(reasoning.get('reasoning_tokens')) is int and reasoning['reasoning_tokens'] >= 0:
                usage['reasoning_tokens']=reasoning['reasoning_tokens']
            if strict and choice['finish_reason'] == 'stop':
                raise ProviderError('MODEL_TOOL_CALL_REQUIRED', dict(usage=usage))
            if choice['finish_reason'] != ('tool_calls' if strict else 'stop'):
                finish=choice['finish_reason']
                details=dict(finish_reason=finish if finish in {'length','content_filter','tool_calls','function_call','insufficient_system_resource','aborted'} else 'other',
                             max_tokens=max_tokens)
                details['usage']=usage
                raise ProviderError('MODEL_OUTPUT_LIMIT' if finish=='length' else 'MODEL_GENERATION_INTERRUPTED' if finish in {'insufficient_system_resource','aborted'} else 'MODEL_RESPONSE_INCOMPLETE',details)
            msg = choice['message']
            if strict:
                calls = msg.get('tool_calls')
                if (msg.get('refusal') or not isinstance(calls, list) or len(calls) != 1
                        or not isinstance(calls[0], dict) or calls[0].get('type') != 'function'):
                    raise ProviderError('MODEL_INVALID_TOOL_CALL', dict(usage=usage))
                function = calls[0].get('function')
                if (not isinstance(function, dict) or function.get('name') != 'submit_decision'
                        or not isinstance(function.get('arguments'), str)):
                    raise ProviderError('MODEL_INVALID_TOOL_CALL', dict(usage=usage))
                return tool_result(function['arguments']), usage
            if msg.get('refusal') or msg.get('tool_calls'): raise ProviderError('MODEL_REFUSAL_OR_TOOL_CALL')
            if not isinstance(msg.get('content'), str) or not msg['content'].strip():
                raise ProviderError('MODEL_EMPTY_RESPONSE',dict(finish_reason='stop',max_tokens=max_tokens,usage=usage))
            # Persist only standardized integer usage counters, never arbitrary response fields.
            return msg['content'], usage
        except (httpx.TimeoutException, TimeoutError): raise ProviderError('MODEL_TIMEOUT') from None
        except httpx.RequestError: raise ProviderError('MODEL_UNAVAILABLE') from None
        except (ValueError, KeyError, IndexError, TypeError): raise ProviderError('MODEL_INVALID_RESPONSE') from None


class CodexProvider:
    """Official noninteractive CLI interface, reuses login without reading OAuth tokens.

    Isolated ephemeral invocations avoid loading user MCPs/plugins or project instructions.
    The CLI manages subscription authentication and its own sandbox.
    """
    def __init__(self, profile):
        self.profile = profile
        self.on_attempt = None

    async def decide(self, packet):
        return await observed_attempt(self, lambda: self.decide_once(packet))

    async def decide_once(self, packet):
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
