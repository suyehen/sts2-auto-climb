"""Launch lifecycle tests use a simulated bridge and never launch the real game."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from autoslay.agent.launch import LaunchCoordinator
from autoslay.agent.runtime import Agent
from autoslay.agent.config import Store


def menu(saved=False):
    return dict(connected=True, ready=True, busy=False, session_epoch='epoch', state_version=7,
                state=dict(phase='main_menu', command_context_id='menu', menu=dict(
                    decision_ready=True, phase='main_menu', has_saved_run=saved, save_loadable=saved),
                    legal_actions=[dict(action_id=a, allows_no_target=True) for a in
                                   (['continue_game', 'abandon_run'] if saved else ['new_game'])]))


def request(**changes):
    return dict(request_id='launch-test', profile_id='codex', mode='new') | changes


class FakeAgent:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.running = False
        self.phase, self.error = 'paused', None
        self.store = SimpleNamespace(profiles={'codex': SimpleNamespace(kind='codex', model='')},
                                     active='codex', key=lambda p: '', save=lambda: None)
        self.fetch = AsyncMock(side_effect=lambda: deepcopy(self.snapshot))
        self.control = AsyncMock(return_value={})
        self.action_status = AsyncMock(return_value=dict(status='unknown'))
        self.submit = AsyncMock(side_effect=self.apply)
        self.commands = []

    def status(self):
        return dict(running=self.running, phase=self.phase)

    async def command(self, route, body):
        self.commands.append(route)
        self.running = True

    async def apply(self, action):
        state = self.snapshot['state']
        if action.action_id in {'continue_game', 'embark'}:
            state.update(phase='map', run_id='run-1', menu={})
        elif action.action_id == 'abandon_run':
            self.snapshot = menu()
        elif action.action_id == 'new_game':
            state.update(phase='character_selection', menu=dict(phase='character_selection',
                decision_ready=True, has_saved_run=False, ascension=0, max_ascension=2,
                characters=[dict(entity_id='CHARACTER.IRONCLAD', unlocked=True, selected=True)]),
                legal_actions=[dict(action_id=a, allows_no_target=True) for a in ['ascension_up', 'embark']])
        elif action.action_id == 'ascension_up':
            state['menu']['ascension'] += 1
        elif action.action_id == 'continue_game_over':
            state['legal_actions'] = [dict(action_id='return_after_game_over',allows_no_target=True)]
        elif action.action_id == 'return_after_game_over':
            self.snapshot = menu()
        return dict(status='completed')


def coordinator(tmp_path, monkeypatch, snapshot=None, **kwargs):
    monkeypatch.setattr('autoslay.agent.launch.ROOT', tmp_path)
    agent = FakeAgent(snapshot or menu())
    coordinator = LaunchCoordinator(agent, tmp_path/'jobs', launcher=lambda: None, **kwargs)
    return agent, coordinator


def test_default_new_run_highest_ascension_and_single_handoff(tmp_path, monkeypatch):
    async def run():
        a, c = coordinator(tmp_path, monkeypatch)
        accepted = await c.accept(request())
        assert accepted['job']['status'] == 'starting' and c.active
        await c.task
        assert c.job['status'] == 'completed' and a.running
        assert [call.args[0].action_id for call in a.submit.call_args_list] == ['new_game', 'ascension_up', 'ascension_up', 'embark']
        assert a.commands == ['/start']
        assert c.job['selected_menu']['ascension'] == 2
        assert (await c.accept(request()))['job']['status'] == 'completed'
        assert a.submit.await_count == 4
        with pytest.raises(ValueError, match='REQUEST_ID_CONFLICT'):
            await c.accept(request(ascension=0))
    asyncio.run(run())


def test_continue_saved_and_current_run_without_menu_clicks(tmp_path, monkeypatch):
    async def run():
        a, c = coordinator(tmp_path, monkeypatch, menu(True))
        await c.accept(request(mode='continue'))
        await c.task
        assert a.submit.call_args.args[0].action_id == 'continue_game'
        assert a.running
        b, d = coordinator(tmp_path/'current', monkeypatch, dict(menu(), state=dict(phase='combat', run_id='run')))
        await d.accept(request(mode='current'))
        await d.task
        assert b.running and b.submit.await_count == 0
    asyncio.run(run())


@pytest.mark.parametrize('confirm,epoch,version,error',[
    (False, 'epoch', 7, 'EXISTING_SAVE_REQUIRES_CONFIRMATION'),
    (True, 'other', 7, 'SAVE_CONFIRMATION_EXPIRED'),
    (True, 'epoch', 3, 'SAVE_CONFIRMATION_EXPIRED')])
def test_save_protection(tmp_path, monkeypatch, confirm, epoch, version, error):
    async def run():
        a, c = coordinator(tmp_path, monkeypatch, menu(True))
        await c.accept(request(replace_save=confirm, expected_epoch=epoch, expected_state_version=version))
        await c.task
        assert c.job['error'] == error and a.submit.await_count == 0
        assert not a.running and a.control.call_args.args == ('paused',)
    asyncio.run(run())


def test_confirmed_replacement_and_custom_zero(tmp_path, monkeypatch):
    async def run():
        a, c = coordinator(tmp_path, monkeypatch, menu(True))
        await c.accept(request(replace_save=True, expected_epoch='epoch', expected_state_version=7, ascension=0))
        await c.task
        assert c.job['status'] == 'completed'
        assert [call.args[0].action_id for call in a.submit.call_args_list] == ['abandon_run', 'new_game', 'embark']
    asyncio.run(run())


def test_unavailable_ascension_exposes_a_stable_error_code(tmp_path, monkeypatch):
    async def run():
        a, c = coordinator(tmp_path, monkeypatch)
        await c.accept(request(ascension=99))
        await c.task
        assert c.job['error']=='ASCENSION_UNAVAILABLE'
        assert [call.args[0].action_id for call in a.submit.call_args_list] == ['new_game']
        assert not a.running
    asyncio.run(run())


def test_unknown_action_survives_restart_and_blocks_new_submission(tmp_path, monkeypatch):
    async def run():
        a, c = coordinator(tmp_path, monkeypatch)
        a.submit = AsyncMock(return_value=dict(status='unknown'))
        await c.accept(request())
        await c.task
        assert c.job['error'] == 'MENU_ACTION_UNRESOLVED'
        other = LaunchCoordinator(a, c.directory)
        await other.accept(request(request_id='next'))
        await other.task
        assert other.job['error'] == 'PREVIOUS_ACTION_UNRESOLVED'
        assert a.submit.await_count == 1
        assert a.action_status.await_count == 1
    asyncio.run(run())


def test_pause_cancels_inflight_start_and_persists_uncertain_action(tmp_path, monkeypatch):
    async def run():
        a, c = coordinator(tmp_path, monkeypatch)
        entered = asyncio.Event()
        async def submit(action):
            entered.set()
            await asyncio.Event().wait()
        a.submit = AsyncMock(side_effect=submit)
        await c.accept(request())
        await entered.wait()
        with pytest.raises(ValueError, match='AGENT_ALREADY_ACTIVE'):
            await c.accept(request(request_id='duplicate'))
        await c.cancel()
        assert c.job['status'] == 'cancelled' and not c.active and not a.running
        assert c.job['steps'][0].get('receipt') is None
        assert a.control.call_args.args == ('paused',)
        assert (await c.state('launch-test'))['job']['status'] == 'cancelled'
        assert (await c.state('missing'))['job'] is None
    asyncio.run(run())


def test_provider_preflight_and_validation_before_any_game_action(tmp_path, monkeypatch):
    async def run():
        a, c = coordinator(tmp_path, monkeypatch)
        for body in [request(profile_id='missing'), request(ascension=True), request(character='unknown')]:
            with pytest.raises(ValueError):
                await c.accept(body)
        assert a.fetch.await_count == 0 and a.submit.await_count == 0 and c.job is None
    asyncio.run(run())


def test_launch_offline_timeout_and_pause_responsiveness(tmp_path, monkeypatch):
    async def run():
        a, c = coordinator(tmp_path, monkeypatch, {'connected':False}, timeout=0)
        called = []
        c.launcher = lambda: called.append(True)
        await c.accept(request())
        await c.task
        assert called == [True] and c.job['error'] == 'GAME_START_TIMEOUT'
        assert not a.running and a.submit.await_count == 0
        real = Agent(store=Store(tmp_path/'config'), fetch=AsyncMock(return_value={'connected':False}),
                     control=AsyncMock(return_value={}), journal=tmp_path/'agent', record=False)
        real.launch.launcher = lambda: None
        await real.command('/play/start', request())
        assert real.status()['launch_active']
        await real.command('/pause', {})
        assert not real.status()['launch_active'] and not real.running
    asyncio.run(run())


def test_resident_agent_handoff_and_pause_share_one_lifecycle(tmp_path, monkeypatch):
    async def run():
        monkeypatch.setattr('autoslay.agent.launch.ROOT', tmp_path)
        bridge = FakeAgent(menu(True))
        agent = Agent(store=Store(tmp_path/'config'), fetch=bridge.fetch, control=bridge.control,
                      submit=bridge.submit, status=bridge.action_status, journal=tmp_path/'agent', record=False)
        async def idle_loop(generation):
            await asyncio.Event().wait()
        agent.loop = idle_loop
        await agent.command('/play/start', request(mode='continue'))
        await asyncio.wait_for(agent.launch.task, 3)
        assert agent.running and agent.launch.job['status']=='completed'
        assert agent.task and not agent.task.done()
        await agent.command('/pause', {})
        assert not agent.running and not agent.launch.active and agent.task is None
        assert bridge.control.call_args.args == ('paused',)
    asyncio.run(run())


def test_new_game_can_leave_the_finished_run_screen(tmp_path, monkeypatch):
    async def run():
        finished = menu()
        finished['state'].update(phase='game_over',run_id='old-run',menu={},
                                 legal_actions=[dict(action_id='continue_game_over',allows_no_target=True)])
        a,c = coordinator(tmp_path,monkeypatch,finished)
        await c.accept(request(ascension=0))
        await c.task
        assert c.job['status']=='completed'
        assert [call.args[0].action_id for call in a.submit.call_args_list] == ['continue_game_over','return_after_game_over','new_game','embark']
    asyncio.run(run())
