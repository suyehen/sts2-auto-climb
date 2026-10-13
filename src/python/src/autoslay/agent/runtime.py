"""Single owner loop, fresh-state dispatch, cancelable provider switching."""
import asyncio
import json
import time
import re
from uuid import uuid4
from pydantic import ValidationError
from autoslay.paths import ROOT
from autoslay.bridge.client import fetch_state, set_control_mode, submit_action, get_action_status, ActionRequest
from autoslay.decision.deterministic import assess, source
from autoslay.decision.unified import analyze_decision
from autoslay.execution.deterministic import DeterministicExecutor
from autoslay.execution.lock import dispatch_lock
from autoslay.telemetry import runs, decisions
from .config import Store, Profile, atomic
from .contracts import validate, validate_plan, Decision
from .plans import prepare, matches
from .context import build
from .providers import create, ProviderError


def reward_settling(snapshot):
    state=snapshot.get('state') or {}
    cards=(state.get('reward_screen') or {}).get('cards') or []
    return (state.get('phase')=='card_reward_selection' and bool(cards)
            and all(c.get('available') is False for c in cards)
            and not any(a.get('type')=='choose_reward_card' for a in state.get('legal_actions',[])))


def reusable_state(origin, fresh):
    """Only a version counter may differ; every actionable state field is hashed."""
    if (not fresh.get('connected') or fresh.get('error') or not fresh.get('ready')
            or fresh.get('busy') or (fresh.get('state') or {}).get('control_mode')!='auto'
            or reward_settling(fresh)):
        return False
    if any(origin.get(k)!=fresh.get(k) for k in ('schema_version','game_assembly_sha256')):
        return False
    left,right=source(origin),source(fresh)
    return all(left[k]==right[k] for k in left if k!='state_version')


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
        self.model_wait = None
        self.error_details = None
        try:
            self.error_details=json.loads((self.journal/'diagnostics/latest.json').read_text(encoding='utf-8'))
            self.error=self.error_details['code']
        except (OSError, ValueError, KeyError, TypeError):
            self.error_details=None
        self.lock = asyncio.Lock()
        from .launch import LaunchCoordinator
        self.launch = LaunchCoordinator(self, self.journal/'launch')

    def status(self):
        return dict(running=self.running, phase=self.phase, error=self.error, generation=self.generation,
                    last=self.last, model_wait=self.model_wait, error_details=self.error_details, launch_active=self.launch.active,
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
        self.model_wait = None
        await self.control('paused')
        self.phase = 'paused'

    async def command(self, route, body):
        # Local status must remain available while a game-state read or pause
        # holds the mutation lock; the supervisor uses it as its readiness check.
        if route == '/status':
            return self.status()
        if route in {'/start', '/play/start'}:
            with dispatch_lock(ROOT/'.runtime/local/service-maintenance') as acquired:
                if not acquired:
                    raise ValueError('SERVICE_MAINTENANCE')
                return await self._command(route, body)
        return await self._command(route, body)

    async def _command(self, route, body):
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
            if not reusable_state(snapshot,fresh):
                return None
            validate(decision.model_dump_json(), fresh)
            src = source(fresh)
            request = ActionRequest(request_id='agent-'+uuid4().hex, run_id=src['run_id'],
                session_epoch=src['session_epoch'], expected_state_version=src['state_version'],
                action_id=decision.action_id, target_id=decision.target_id)
            path = self.journal/(request.request_id+'.json')
            record = dict(status='dispatch_pending', request=request.model_dump(), provider=self.store.active,
                          state_rebound=source(fresh)!=source(snapshot), original_state_version=snapshot.get('state_version'))
            atomic(path, record)
            receipt = await self.submit(request)
            record.update(status=receipt.get('status', 'unresolved'), receipt=receipt)
            atomic(path, record)
            return record

    async def loop(self, generation):
        previous = None
        run_identity = None
        snapshot = None
        identity = None
        reward_wait = None
        try:
            while self.running and generation == self.generation:
                identity = None
                self.phase = 'reading_state'
                snapshot = await self.fetch()
                if not snapshot.get('connected') or snapshot.get('error'): raise ValueError('GAME_DISCONNECTED')
                s = snapshot.get('state') or {}
                if reward_settling(snapshot):
                    screen=(s.get('reward_screen') or {}).get('screen_id')
                    key=(snapshot.get('session_epoch'),s.get('run_id'),screen)
                    if reward_wait is None or reward_wait[0]!=key:reward_wait=(key,time.monotonic())
                    if time.monotonic()-reward_wait[1]>15:raise ValueError('REWARD_CARDS_NOT_READY')
                    self.phase='waiting_reward_cards'
                    await asyncio.sleep(.4)
                    continue
                reward_wait=None
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
                self.model_wait = dict(request_id=identity, started_at=time.time(),
                                       timeout_seconds=p.timeout_seconds, phase=s.get('phase'), attempt=1, max_tokens=p.max_tokens)
                self.model_wait['overall_started_at']=self.model_wait['started_at']
                if hasattr(provider,'on_retry'):
                    def update_retry(progress):
                        if self.model_wait and self.running and generation==self.generation:
                            self.model_wait.update(progress)
                            self.request_event(snapshot,identity,'retrying',**progress)
                    provider.on_retry=update_retry
                try:
                    plan, usage = await self.decide_plan(provider, packet, snapshot, generation, identity, report, p)
                except asyncio.CancelledError:
                    self.request_event(snapshot,identity,'cancelled',reason='agent_paused_or_switched')
                    raise
                finally:
                    self.model_wait = None
                if not self.running or generation != self.generation: break
                if plan is None:
                    self.request_event(snapshot,identity,'not_executed',reason='state_changed_before_correction')
                    continue
                previous = await self.execute_plan(plan, snapshot, report, generation, p,
                    identity, round((time.monotonic()-start)*1000), usage)
        except asyncio.CancelledError: raise
        except ProviderError as exc:
            self.save_error(str(exc), snapshot, identity, exc.details)
        except (ValidationError, json.JSONDecodeError):
            self.save_error('MODEL_INVALID_DECISION' if self.phase=='waiting_model' else 'INVALID_STATE_CONFIG_OR_ACTION', snapshot, identity)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            code = str(exc)
            self.save_error(code if re.fullmatch(r'[A-Z_0-9]{1,80}', code) else 'INVALID_STATE_CONFIG_OR_ACTION', snapshot, identity)
        except Exception:
            self.save_error('AGENT_ERROR', snapshot, identity)
        finally:
            if generation == self.generation:
                self.running = False
                await self.control('paused')
                if self.phase != 'finished': self.phase = 'error' if self.error else 'paused'

    async def decide_plan(self, provider, packet, snapshot, generation, identity, report, profile):
        raw,usage=await provider.decide(packet)
        try:
            return validate_plan(raw,snapshot),usage
        except ValueError as exc:
            if str(exc)!='MULTI_ACTION_REQUIRES_COMBAT':raise
        # No part of the rejected plan was submitted. Retry once only after
        # verifying that the same visible state is still waiting for a choice.
        fresh=await self.fetch()
        if not self.running or generation!=self.generation or not reusable_state(snapshot,fresh):
            return None,usage
        correction=dict(packet,validation_feedback=dict(code='MULTI_ACTION_REQUIRES_COMBAT',
            instruction='Previous response was rejected and NO actions were executed. Return steps with exactly one currently legal action.'))
        if self.model_wait:self.model_wait.update(validation_attempt=2,retry_reason='MULTI_ACTION_REQUIRES_COMBAT',started_at=time.time())
        if self.record:
            await asyncio.to_thread(runs.stage,'agent_packet',snapshot,dict(analysis_id=report['analysis_id'],
                request_id=identity,provider=profile.id,model=profile.model,validation_attempt=2,packet=correction))
        raw,retry_usage=await provider.decide(correction)
        combined={k:usage.get(k,0)+retry_usage.get(k,0) for k in set(usage)|set(retry_usage)}
        combined['request_attempts']=usage.get('request_attempts',1)+retry_usage.get('request_attempts',1)
        combined['validation_attempts']=2
        return validate_plan(raw,snapshot),combined

    def save_error(self, code, snapshot, request_id, provider_details=None):
        self.error=code
        state=(snapshot or {}).get('state') or {}
        profile=self.store.profiles.get(self.store.active)
        details=dict(code=code, occurred_at=time.time(), stage=self.phase,
                     provider=self.store.active, model=profile.model if profile else None,
                     request_id=request_id, plan_request_id=request_id, game_phase=state.get('phase'), turn=(state.get('player') or {}).get('turn',state.get('turn')))
        if self.phase=='executing' and self.last:
            details.update({k:self.last[k] for k in ['plan_step','plan_length','action_id','status'] if k in self.last})
        if provider_details:details.update(provider_details)
        self.error_details=details
        try:
            atomic(self.journal/'diagnostics/latest.json',details)
            atomic(self.journal/('diagnostics/error-'+uuid4().hex+'.json'),details)
            if self.record and snapshot:runs.stage('agent_error',snapshot,details)
        except Exception:
            # Diagnosis must never interfere with stopping the executor.
            pass

    def request_event(self,snapshot,identity,status,**details):
        event=dict(plan_request_id=identity,status=status,occurred_at=time.time(),**details)
        try:
            atomic(self.journal/('requests/'+uuid4().hex+'.json'),event)
            if self.record and snapshot:runs.stage('agent_request',snapshot,event)
        except Exception:
            pass

    async def execute_plan(self, plan, snapshot, report, generation, profile, identity, model_ms, usage):
        prepared = prepare(plan, snapshot)
        previous = None
        current = snapshot
        for index, (step, expected) in enumerate(prepared):
            if not self.running or generation != self.generation:
                break
            if index:
                current = await self.fetch()
                if expected is None or not matches(expected, current, snapshot):
                    self.last['plan_interruption'] = 'state_changed_or_observation_required'
                    break
                # A planned action may cease to be legal despite matching modeled resources.
                try:
                    validate(Decision(state_token=source(current)['state_sha256'],
                        reason=plan.reason, **step.model_dump()).model_dump_json(), current)
                except ValueError:
                    self.last['plan_interruption'] = 'next_action_no_longer_legal'
                    break
            decision = Decision(state_token=source(current)['state_sha256'],
                                reason=plan.reason, **step.model_dump())
            self.last = dict(source=profile.id, model=profile.model, reason=plan.reason,
                action_id=step.action_id, target_id=step.target_id, model_ms=model_ms, usage=usage,
                plan_request_id=identity, plan_step=index+1, plan_length=len(prepared),
                original_plan_length=len(plan.steps), original_plan_steps=[s.model_dump() for s in plan.steps],
                plan_preparation='modeled_prefix_or_observation_boundary' if len(prepared)<len(plan.steps) else 'complete',
                plan_steps=[s.model_dump() for s, _ in prepared])
            self.phase = 'executing'
            step_report = report
            if self.record:
                if index:
                    step_report = await asyncio.to_thread(self.analyze, current)
                if index == 0:
                    await asyncio.to_thread(runs.stage, 'ai_choice', current,
                        dict(analysis_id=report['analysis_id'], group='agent_explicit',
                             steps=self.last['plan_steps'], **self.last))
            result = await self.dispatch(decision, current, generation)
            if result is None:
                self.last.update(status='not_executed',plan_interruption='state_changed_or_agent_paused')
                self.request_event(current,identity,'not_executed',reason=self.last['plan_interruption'],plan_step=index+1)
                break
            if result.get('state_rebound'):
                self.request_event(current,identity,'state_rebound',original_state_version=result['original_state_version'],
                                   expected_state_version=result['request']['expected_state_version'])
            previous = source(current)
            self.last['status'] = result['status']
            self.recent.append(dict(action_id=step.action_id, target_id=step.target_id,
                                    status=result['status'], reason=plan.reason))
            self.recent = self.recent[-4:]
            if self.record:
                after = await self.fetch()
                from autoslay.calibration.events import record_action
                comparison = await asyncio.to_thread(record_action, step_report, current, after, step.action_id, result)
                from autoslay.telemetry.effects import observe as observe_effects
                settled=(result.get('receipt') or {}).get('snapshot') or after
                observed_effects=observe_effects(current,settled,result['status'],(result.get('request') or {}).get('request_id'))
                await asyncio.to_thread(runs.stage, 'agent_outcome', after,
                    dict(analysis_id=step_report['analysis_id'], event_comparison=comparison,observed_effects=observed_effects,
                         plan_request_id=identity, plan_step=index+1, **result))
            if result['status'] not in {'completed', 'awaiting_choice'}:
                raise ValueError('ACTION_'+result['status'].upper())
            if result['status'] == 'awaiting_choice':
                break
        return previous
