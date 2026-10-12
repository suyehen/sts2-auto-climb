"""Cancelable launch coordinator. The browser never dispatches game menu actions."""
import asyncio
import json
import os
import re
import subprocess
import time
from pathlib import Path
from uuid import uuid4
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from autoslay.paths import ROOT
from autoslay.bridge.client import ActionRequest
from autoslay.bridge.new_run import next_menu_action, normalize_character
from autoslay.execution.lock import dispatch_lock
from .config import atomic


class LaunchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    request_id: str = Field(pattern=r'^[A-Za-z0-9_-]{1,100}$')
    mode: Literal['new', 'continue', 'current']
    profile_id: str = Field(min_length=1, max_length=60)
    character: str = Field(default='IRONCLAD', min_length=1, max_length=60)
    ascension: int | None = Field(default=None, ge=0)
    replace_save: bool = False
    expected_epoch: str | None = None
    expected_state_version: int | None = Field(default=None, ge=0)


def launch_game():
    """Use the installed Steam launcher, without restarting or terminating a game."""
    installation = json.loads((ROOT/'.runtime/local/installation.json').read_text(encoding='utf-8-sig'))
    game = Path(installation['game_path'])
    candidates = [game.parents[2]/'Steam.exe']
    if os.name == 'nt':
        import winreg
        for hive, key, value in [(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam', 'SteamExe'),
                                 (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Valve\Steam', 'InstallPath')]:
            try:
                with winreg.OpenKey(hive, key) as entry:
                    found = Path(winreg.QueryValueEx(entry, value)[0])
                    candidates.append(found if value == 'SteamExe' else found/'Steam.exe')
            except OSError:
                pass
    steam = next((p for p in candidates if p.is_file()), None)
    if os.name != 'nt' or steam is None:
        raise ValueError('GAME_LAUNCHER_UNAVAILABLE')
    subprocess.Popen([str(steam), '-applaunch', '2868840'],
                     creationflags=subprocess.CREATE_NO_WINDOW)


class LaunchCoordinator:
    def __init__(self, agent, directory=None, launcher=launch_game, timeout=120):
        self.agent = agent
        self.directory = directory or ROOT/'.runtime/logs/launch'
        self.launcher, self.timeout = launcher, timeout
        self.task = None
        self.job = None
        records = sorted(self.directory.glob('*.json'), key=lambda p: p.stat().st_mtime)
        if records:
            self.job = json.loads(records[-1].read_text(encoding='utf-8'))
            if self.job['status'] in {'starting', 'running'}:
                self.job.update(status='interrupted', phase='interrupted', error='START_INTERRUPTED')
                self.save()

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def save(self):
        atomic(self.directory/(self.job['request']['request_id']+'.json'), self.job)

    async def state(self, request_id=None):
        job = self.job
        if request_id is not None:
            if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
                raise ValueError('INVALID_REQUEST_ID')
            path = self.directory/(request_id+'.json')
            job = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        snapshot = await self.agent.fetch()
        state = snapshot.get('state') or {}
        return dict(job=job, agent=self.agent.status(), game=dict(
            connected=bool(snapshot.get('connected') and not snapshot.get('error')),
            phase=state.get('phase'), run_id=state.get('run_id'),
            epoch=snapshot.get('session_epoch'), state_version=snapshot.get('state_version'),
            menu=state.get('menu') or {}))

    async def accept(self, body):
        request = LaunchRequest.model_validate(body)
        if normalize_character(request.character) not in {'IRONCLAD', 'SILENT', 'DEFECT', 'NECROBINDER', 'REGENT'}:
            raise ValueError('CHARACTER_UNAVAILABLE')
        path = self.directory/(request.request_id+'.json')
        if path.exists():
            previous = json.loads(path.read_text(encoding='utf-8'))
            if previous['request'] != request.model_dump():
                raise ValueError('REQUEST_ID_CONFLICT')
            return dict(job=previous, agent=self.agent.status())
        if self.active or self.agent.running:
            raise ValueError('AGENT_ALREADY_ACTIVE')
        profile = self.agent.store.profiles.get(request.profile_id)
        if not profile or (not profile.model and profile.kind != 'codex') or (profile.kind == 'api' and not self.agent.store.key(profile)):
            raise ValueError('PROVIDER_NOT_CONFIGURED')
        self.job = dict(request=request.model_dump(), status='starting', phase='checking',
                        error=None, steps=[], started_at=time.time())
        self.save()
        self.agent.error = None
        self.task = asyncio.create_task(self.run(request))
        return dict(job=self.job, agent=self.agent.status())

    async def cancel(self):
        if self.active and self.task is not asyncio.current_task():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    def phase(self, phase):
        self.job['phase'] = phase
        self.agent.phase = phase
        self.save()

    async def recover(self):
        # Every journal is checked, including interrupted jobs after a service restart.
        for path in self.directory.glob('*.json'):
            record = json.loads(path.read_text(encoding='utf-8'))
            for step in record['steps']:
                if step.get('receipt', {}).get('status') in {'completed', 'rejected', 'failed'}:
                    continue
                receipt = await self.agent.action_status(step['request']['request_id'])
                if receipt.get('status') not in {'completed', 'rejected', 'failed'}:
                    raise ValueError('PREVIOUS_ACTION_UNRESOLVED')
                step['receipt'] = receipt
                atomic(path, record)

    async def action(self, snapshot, action):
        state = snapshot['state']
        if not any(a['action_id'] == action and a.get('allows_no_target') for a in state.get('legal_actions', [])):
            raise ValueError('MENU_ACTION_UNAVAILABLE')
        request = ActionRequest(request_id='launch-'+uuid4().hex,
            run_id=state['command_context_id'], session_epoch=snapshot['session_epoch'],
            expected_state_version=snapshot['state_version'], action_id=action)
        step = dict(request=request.model_dump())
        self.job['steps'].append(step)
        self.save()  # Never replay a submission whose outcome is unknown.
        receipt = await self.agent.submit(request)
        step['receipt'] = receipt
        self.save()
        if receipt.get('status') != 'completed':
            raise ValueError('MENU_ACTION_UNRESOLVED')

    async def run(self, request):
        handed_off = False
        try:
            with dispatch_lock(ROOT/'.runtime/logs/plans') as acquired:
                if not acquired:
                    raise ValueError('EXECUTOR_BUSY')
                await self.recover()
                snapshot = await self.agent.fetch()
                if not snapshot.get('connected') or snapshot.get('error'):
                    self.phase('launching_game')
                    await asyncio.to_thread(self.launcher)
                deadline = time.monotonic()+self.timeout
                epoch = None
                started = False
                abandoned = False
                for _ in range(240):
                    if time.monotonic() > deadline:
                        raise ValueError('GAME_START_TIMEOUT')
                    snapshot = await self.agent.fetch()
                    if not snapshot.get('connected') or snapshot.get('error'):
                        self.phase('waiting_game')
                        await asyncio.sleep(.5)
                        continue
                    epoch = epoch or snapshot.get('session_epoch')
                    if not epoch or snapshot.get('session_epoch') != epoch:
                        raise ValueError('SESSION_CHANGED')
                    state = snapshot.get('state') or {}
                    menu = state.get('menu') or {}
                    in_run = bool(state.get('run_id')) and state.get('phase') not in {'main_menu', 'singleplayer_menu', 'character_selection', 'game_over', 'no_run'}
                    if in_run:
                        if request.mode == 'new' and not started:
                            raise ValueError('SAVE_AND_EXIT_FIRST')
                        break
                    if started and request.mode == 'new':
                        self.phase('waiting_game')
                        await asyncio.sleep(.5)
                        continue
                    if request.mode == 'current':
                        raise ValueError('NO_CURRENT_RUN')
                    if state.get('phase') == 'game_over':
                        self.phase('closing_finished_run')
                        control = await self.agent.control('auto')
                        if control.get('error'):
                            raise ValueError('GAME_CONTROL_FAILED')
                        fresh = await self.agent.fetch()
                        if fresh.get('session_epoch') != epoch:
                            raise ValueError('SESSION_CHANGED')
                        legal = {a['action_id'] for a in (fresh.get('state') or {}).get('legal_actions', []) if a.get('allows_no_target')}
                        action = next((a for a in ['return_after_game_over', 'continue_game_over'] if a in legal), None)
                        if fresh.get('ready') and not fresh.get('busy') and action:
                            await self.action(fresh, action)
                        await asyncio.sleep(.3)
                        continue
                    if snapshot.get('busy') or not menu.get('decision_ready'):
                        self.phase('waiting_game')
                        await asyncio.sleep(.5)
                        continue
                    self.phase('continuing_save' if request.mode == 'continue' else 'setting_up_run')
                    if request.mode == 'continue':
                        if started:
                            await asyncio.sleep(.5)
                            continue
                        if not menu.get('has_saved_run') or not menu.get('save_loadable'):
                            raise ValueError('NO_LOADABLE_SAVE')
                        action = 'continue_game'
                    elif menu.get('has_saved_run'):
                        if not request.replace_save:
                            raise ValueError('EXISTING_SAVE_REQUIRES_CONFIRMATION')
                        if abandoned or snapshot.get('session_epoch') != request.expected_epoch or snapshot.get('state_version') != request.expected_state_version:
                            raise ValueError('SAVE_CONFIRMATION_EXPIRED')
                        action = 'abandon_run'
                        abandoned = True
                    else:
                        action = next_menu_action(snapshot, normalize_character(request.character), request.ascension)
                    control = await self.agent.control('auto')
                    if control.get('error'):
                        raise ValueError('GAME_CONTROL_FAILED')
                    # Control mode changes the state version: fetch again before dispatch.
                    fresh = await self.agent.fetch()
                    if fresh.get('session_epoch') != epoch:
                        raise ValueError('SESSION_CHANGED')
                    if fresh.get('busy') or not fresh.get('ready'):
                        raise ValueError('GAME_NOT_READY')
                    if action == 'abandon_run' and (fresh.get('state') or {}).get('command_context_id') != state.get('command_context_id'):
                        raise ValueError('SAVE_CONFIRMATION_EXPIRED')
                    if request.mode == 'new' and action != 'abandon_run' and next_menu_action(fresh, normalize_character(request.character), request.ascension) != action:
                        raise ValueError('MENU_STATE_CHANGED')
                    if action == 'embark':
                        self.job['selected_menu'] = fresh['state']['menu']
                    await self.action(fresh, action)
                    started = started or action in {'embark', 'continue_game'}
                    await asyncio.sleep(.3)
                else:
                    raise ValueError('SETUP_STEP_LIMIT')
            self.agent.store.active = request.profile_id
            self.agent.store.save()
            self.phase('starting_agent')
            await self.agent.command('/start', {})
            handed_off = True
            self.job.update(status='completed', phase='completed', finished_at=time.time())
        except asyncio.CancelledError:
            self.job.update(status='cancelled', phase='cancelled', error=None)
            raise
        except (ValueError, KeyError, OSError) as exc:
            code = str(exc).split(':', 1)[0]
            self.job.update(status='failed', phase='failed', error=code if code.isupper() and len(code)<80 else 'GAME_START_FAILED')
            self.agent.phase, self.agent.error = 'error', self.job['error']
        except Exception:
            self.job.update(status='failed', phase='failed', error='GAME_START_FAILED')
            self.agent.phase, self.agent.error = 'error', 'GAME_START_FAILED'
        finally:
            if not handed_off:
                await self.agent.control('paused')
            self.save()
