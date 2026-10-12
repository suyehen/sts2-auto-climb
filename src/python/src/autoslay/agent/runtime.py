"""Single owner loop, fresh-state dispatch, cancelable provider switching."""
import asyncio
import json
import time
import re
from uuid import uuid4
from autoslay.paths import ROOT
from autoslay.bridge.client import fetch_state, set_control_mode, submit_action, get_action_status, ActionRequest
from autoslay.decision.deterministic import assess, source
from autoslay.decision.unified import analyze_decision
from autoslay.execution.deterministic import DeterministicExecutor
from autoslay.execution.lock import dispatch_lock
from autoslay.telemetry import runs, decisions
from .config import Store, Profile, atomic
from .contracts import validate
from .context import build
from .providers import create, ProviderError


class Agent:
    def __init__(self, store=None, fetch=fetch_state, control=set_control_mode, submit=submit_action,
                 status=get_action_status, factory=create, analyze=analyze_decision, record=True,
                 journal=None, deterministic=None):
        self.store = store or Store()
        self.fetch, self.control, self.submit, self.action_status = fetch, control, submit, status
        self.factory, self.analyze, self.record = factory, analyze, record
        self.journal = journal or ROOT/'.runtime/logs/agent'
        self.deterministic = deterministic or DeterministicExecutor()
        self.task, self.generation, self.running = None, 0, False
        self.phase, self.error, self.last, self.recent = 'paused', None, None, []
        self.lock = asyncio.Lock()
        from .launch import LaunchCoordinator
        self.launch = LaunchCoordinator(self, self.journal/'launch')

    def status(self):
        return dict(running=self.running, phase=self.phase, error=self.error, generation=self.generation,
                    last=self.last, launch_active=self.launch.active,
                    launch=self.launch.job, **self.store.public())

    async def pause(self):
        await self.launch.cancel()
        self.running = False
        self.generation += 1
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
            try: await self.task
            except asyncio.CancelledError: pass
        self.task = None
        await self.control('paused')
        self.phase = 'paused'

    async def command(self, route, body):
        async with self.lock:
            if route == '/status': return self.status()
            if route == '/play/state': return await self.launch.state(body.get('request_id'))
            if route == '/play/start': return await self.launch.accept(body)
            if route.startswith('/config/'):
                from autoslay.configuration import export_package,preview,install,backups,restore
                if route=='/config/export': return export_package(self.store,body.get('ui'),body.get('mode','standard'),body.get('password'))
                if route=='/config/preview': return preview(self.store,body['package'],body.get('password'))
                if route=='/config/backups': return dict(backups=backups(self.store))
                if route=='/config/import':
                    preview(self.store,body['package'],body.get('password'))
                    await self.pause()
                    return install(self.store,body['package'],body.get('password'))
                if route=='/config/restore':
                    await self.pause()
                    return restore(self.store,body['backup_id'])
                raise ValueError('UNKNOWN_CONFIG_ROUTE')
            if route in {'/pause', '/stop'}:
                await self.pause()
            elif route == '/switch':
                identity = body.get('id')
                if identity not in self.store.profiles: raise ValueError('UNKNOWN_PROFILE')
                await self.pause()
                self.store.active = identity
                self.store.save()
                self.error = None
            elif route == '/profile':
                p = Profile.model_validate(body['profile'])
                if 'api_key' in body and (not isinstance(body['api_key'], str) or len(body['api_key']) > 8192
                                         or '\n' in body['api_key'] or '\r' in body['api_key']):
                    raise ValueError('INVALID_KEY')
                await self.pause()
                old = self.store.profiles.get(p.id)
                self.store.profiles[p.id] = p
                try:
                    if 'api_key' in body: self.store.set_key(p.id, body['api_key'])
                    self.store.save()
                except Exception:
                    if old: self.store.profiles[p.id] = old
                    else: self.store.profiles.pop(p.id, None)
                    raise
            elif route == '/models':
                # Read-only discovery; no completion requests and no game actions.
                p = self.store.profiles[body.get('id', self.store.active)]
                if p.kind == 'codex': return dict(models=[], note='Leave model empty to use Codex default.')
                import httpx
                key = self.store.key(p)
                try:
                    async with httpx.AsyncClient(trust_env=False, timeout=8, follow_redirects=False) as c:
                        r = await c.get(p.base_url.rstrip('/')+'/models',
                                        headers={'Authorization': 'Bearer '+key} if key else {})
                        if r.status_code != 200: return dict(models=[], error='MODEL_LIST_HTTP_'+str(r.status_code))
                        if len(r.content) > 500_000: raise ValueError('MODEL_LIST_TOO_LARGE')
                        models = [x['id'] for x in r.json()['data'] if isinstance(x.get('id'), str)]
                        return dict(models=models[:500])
                except (httpx.HTTPError, KeyError, TypeError, ValueError):
                    return dict(models=[], error='MODEL_LIST_UNAVAILABLE')
            elif route == '/start':
                if self.launch.active and asyncio.current_task() is not self.launch.task:
                    raise ValueError('AGENT_ALREADY_ACTIVE')
                if self.running: return self.status()
                await self.launch.recover()
                p = self.store.profiles[self.store.active]
                if not p.model and p.kind != 'codex' or p.kind == 'api' and not self.store.key(p):
                    raise ValueError('PROVIDER_NOT_CONFIGURED')
                snapshot = await self.fetch()
                if not snapshot.get('connected') or snapshot.get('error'): raise ValueError('GAME_NOT_CONNECTED')
                if (snapshot.get('state') or {}).get('phase') in {'main_menu', 'singleplayer_menu', 'character_selection', 'no_run', 'game_over'}:
                    raise ValueError('ENTER_OR_CONTINUE_RUN_FIRST')
                self.error = None
                result = await self.control('auto')
                if result.get('error'): raise ValueError('GAME_CONTROL_FAILED')
                self.generation += 1
                self.running = True
                self.task = asyncio.create_task(self.loop(self.generation))
            else: raise ValueError('UNKNOWN_ROUTE')
            return self.status()

    async def unresolved(self):
        for path in self.journal.glob('*.json'):
            record = json.loads(path.read_text(encoding='utf-8'))
            if record['status'] not in {'completed', 'failed', 'rejected', 'awaiting_choice'}:
                receipt = await self.action_status(record['request']['request_id'])
                if receipt.get('status') not in {'completed', 'failed', 'rejected', 'awaiting_choice'}:
                    raise ValueError('PREVIOUS_ACTION_UNRESOLVED')
                record.update(status=receipt['status'], receipt=receipt, recovered_by_status_query=True)
                atomic(path, record)

    async def dispatch(self, decision, snapshot, generation):
        with dispatch_lock(ROOT/'.runtime/logs/plans') as acquired:
            if not acquired: raise ValueError('EXECUTOR_BUSY')
            await self.unresolved()
            fresh = await self.fetch()
            if not self.running or self.generation != generation: return None
            if source(fresh) != source(snapshot) or not fresh.get('ready') or fresh.get('busy'):
                return None
            validate(decision.model_dump_json(), fresh)
            src = source(fresh)
            request = ActionRequest(request_id='agent-'+uuid4().hex, run_id=src['run_id'],
                session_epoch=src['session_epoch'], expected_state_version=src['state_version'],
                action_id=decision.action_id, target_id=decision.target_id)
            path = self.journal/(request.request_id+'.json')
            record = dict(status='dispatch_pending', request=request.model_dump(), provider=self.store.active)
            atomic(path, record)
            receipt = await self.submit(request)
            record.update(status=receipt.get('status', 'unresolved'), receipt=receipt)
            atomic(path, record)
            return record

    async def loop(self, generation):
        previous = None
        run_identity = None
        try:
            while self.running and generation == self.generation:
                self.phase = 'reading_state'
                snapshot = await self.fetch()
                if not snapshot.get('connected') or snapshot.get('error'): raise ValueError('GAME_DISCONNECTED')
                s = snapshot.get('state') or {}
                current_run = (snapshot.get('session_epoch'), s.get('run_id'))
                if current_run != run_identity:
                    self.recent = []
                    run_identity = current_run
                if s.get('phase') == 'game_over' or (snapshot.get('run_clock') or {}).get('result') in {'victory', 'defeat'}:
                    self.phase = 'finished'
                    break
                if not snapshot.get('ready') or snapshot.get('busy') or s.get('control_mode') == 'paused':
                    await asyncio.sleep(.4)
                    continue
                # A previous uncertain dispatch blocks deterministic shortcuts as well as AI actions.
                await self.unresolved()
                token = source(snapshot)
                if token == previous:
                    await asyncio.sleep(.4)
                    continue
                proof = assess({}, snapshot)
                if proof['eligible']:
                    self.phase = 'local_deterministic'
                    result = await self.deterministic.execute(uuid4().hex)
                    if result.get('status') not in {'completed', 'awaiting_choice'}:
                        raise ValueError('LOCAL_ACTION_'+str(result.get('status', 'UNKNOWN')).upper())
                    previous = token
                    self.last = dict(source='local_deterministic', status=result['status'])
                    continue
                if proof['mode'] == 'wait':
                    await asyncio.sleep(.4)
                    continue
                self.phase = 'calculating'
                report = await asyncio.to_thread(self.analyze, snapshot)
                packet = build(snapshot, report, self.recent)
                p = self.store.profiles[self.store.active]
                provider = self.factory(p, self.store.key(p))
                self.phase = 'waiting_model'
                identity = uuid4().hex
                if self.record:
                    await asyncio.to_thread(decisions.archive, report, snapshot)
                    await asyncio.to_thread(runs.stage, 'agent_packet', snapshot,
                        dict(analysis_id=report['analysis_id'], request_id=identity,
                             provider=p.id, model=p.model, packet=packet))
                start = time.monotonic()
                raw, usage = await provider.decide(packet)
                if not self.running or generation != self.generation: break
                decision = validate(raw, snapshot)
                self.last = dict(source=p.id, model=p.model, reason=decision.reason,
                    action_id=decision.action_id, target_id=decision.target_id,
                    model_ms=round((time.monotonic()-start)*1000), usage=usage)
                if self.record:
                    await asyncio.to_thread(runs.stage, 'ai_choice', snapshot,
                        dict(analysis_id=report['analysis_id'], group='agent_explicit',
                             steps=[dict(action_id=decision.action_id, target_id=decision.target_id)], **self.last))
                self.phase = 'executing'
                result = await self.dispatch(decision, snapshot, generation)
                if result is None: continue
                self.last['status'] = result['status']
                self.recent.append(dict(action_id=decision.action_id, target_id=decision.target_id,
                                        status=result['status'], reason=decision.reason))
                self.recent = self.recent[-4:]
                if self.record:
                    after=await self.fetch()
                    from autoslay.calibration.events import record_action
                    comparison=await asyncio.to_thread(record_action,report,snapshot,after,decision.action_id,result)
                    await asyncio.to_thread(runs.stage, 'agent_outcome', after,
                        dict(analysis_id=report['analysis_id'],event_comparison=comparison, **result))
                if result['status'] not in {'completed', 'awaiting_choice'}:
                    raise ValueError('ACTION_'+result['status'].upper())
                previous = token
        except asyncio.CancelledError: raise
        except ProviderError as exc: self.error = str(exc)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            code = str(exc)
            self.error = code if re.fullmatch(r'[A-Z_]{1,80}', code) else 'INVALID_STATE_CONFIG_OR_ACTION'
        except Exception: self.error = 'AGENT_ERROR'
        finally:
            if generation == self.generation:
                self.running = False
                await self.control('paused')
                if self.phase != 'finished': self.phase = 'error' if self.error else 'paused'
