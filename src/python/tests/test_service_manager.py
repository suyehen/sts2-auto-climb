"""Supervisor tests use fake service processes and game receipts; no live gameplay."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import AsyncMock
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest
from autoslay.archive.repository import atomic
from autoslay.execution.lock import dispatch_lock
from autoslay.services.manager import Manager, make_server, pending_records


def snapshot():
    return dict(connected=True, busy=False, session_epoch='epoch',
                state=dict(run_id='run', command_context_id='run', control_mode='paused'))


class Owner:
    def __init__(self, pid=100): self.pid=pid
    def __enter__(self): return self
    def __exit__(self, *_): pass


class Backend:
    def __init__(self):
        self.calls = []
        self.current = snapshot()
        self.receipts = {}
        self.bad_owner = None
    def health(self, name): return dict(ready=True, running=False)
    def owner(self, name):
        self.calls.append(('verify', name))
        if name == self.bad_owner: raise ValueError('PROCESS_IDENTITY_UNVERIFIED')
        return Owner()
    def pause(self):
        self.calls.append(('pause',));return deepcopy(self.current)
    def snapshot(self): return deepcopy(self.current)
    def receipt(self, identity):
        self.calls.append(('receipt', identity));return self.receipts.get(identity, dict(status='unknown'))
    def ensure(self, name):
        self.calls.append(('start', name))
        return dict(started=True,pid={'agent':201,'execution':202,'dashboard':203}[name])
    def stop(self, name, owner): self.calls.append(('stop', name))


def manager(tmp_path, action='restart', scope='agent'):
    m = Manager(tmp_path, Backend(), settle_timeout=0)
    m.job = dict(action=action, scope=scope, request_id='test', status='running', phase='checking', paused=False)
    return m


def pending(tmp_path, epoch='epoch', folder='agent'):
    path = tmp_path/'.runtime/logs'/folder/'pending.json'
    value = dict(status='dispatch_pending', request=dict(request_id='exact', action_id='strike',
                                                       session_epoch=epoch, run_id='run'))
    atomic(path, value)
    return path


def test_agent_restart_replaces_agent_and_execution_in_order(tmp_path):
    m = manager(tmp_path);m.perform()
    assert m.job['status'] == 'completed' and m.job['paused'] is True
    assert m.backend.calls == [('verify', 'agent'), ('verify', 'execution'), ('pause',), ('stop', 'agent'), ('stop','execution'),
                               ('start', 'execution'), ('start', 'agent')]
    assert m.job['restarted_services']=={'execution':{'previous_pid':100,'pid':202},'agent':{'previous_pid':100,'pid':201}}
    assert not any(c[0] in {'submit', 'play', 'launch'} for c in m.backend.calls)


@pytest.mark.parametrize('result',[dict(started=False),dict(started=True,pid=100),dict(started=True)])
def test_restart_cannot_report_completion_without_a_new_process(tmp_path,result):
    m=manager(tmp_path)
    m.backend.ensure=lambda name:result
    m.perform()
    assert m.job['status']=='failed' and m.job['error']=='SERVICE_RESTART_UNCONFIRMED'


def test_start_is_idempotent_service_ensure_without_pausing_running_game(tmp_path):
    m = manager(tmp_path, 'start', 'all');m.perform()
    assert m.backend.calls == [('start', 'execution'), ('start', 'agent'), ('start', 'dashboard')]
    assert m.job['status'] == 'completed' and not m.job['paused']


def test_unknown_receipt_prevents_termination_and_restart(tmp_path):
    path = pending(tmp_path)
    m = manager(tmp_path);m.perform()
    assert m.job['error'] == 'PREVIOUS_ACTION_UNRESOLVED'
    assert not any(c[0] in {'start', 'stop'} for c in m.backend.calls)
    assert json.loads(path.read_text())['status'] == 'dispatch_pending'


def test_recovered_receipt_is_persisted_and_all_services_restart_in_order(tmp_path):
    path = pending(tmp_path)
    m = manager(tmp_path, scope='all');m.backend.receipts['exact'] = dict(status='completed', request_id='exact')
    m.perform()
    assert m.job['status'] == 'completed'
    assert json.loads(path.read_text())['recovered_by_status_query']
    assert [c for c in m.backend.calls if c[0] in {'stop', 'start'}] == [
        ('stop', 'agent'), ('stop', 'execution'), ('stop', 'dashboard'),
        ('start', 'execution'), ('start', 'agent'), ('start', 'dashboard')]
    assert ('receipt', 'exact') in m.backend.calls


def test_unknown_previous_session_stays_untouched_and_is_never_replayed(tmp_path):
    path = pending(tmp_path, epoch='old')
    m = manager(tmp_path);m.perform()
    assert m.job['status'] == 'completed'
    assert json.loads(path.read_text())['status'] == 'dispatch_pending'
    assert not any(c[0] == 'receipt' for c in m.backend.calls)


def test_preflight_verifies_all_processes_before_pause_or_termination(tmp_path):
    m = manager(tmp_path, scope='all');m.backend.bad_owner = 'dashboard';m.perform()
    assert m.job['error'] == 'PROCESS_IDENTITY_UNVERIFIED'
    assert all(c[0] == 'verify' for c in m.backend.calls)


def test_busy_game_and_shared_dispatch_lock_block_restart(tmp_path):
    m = manager(tmp_path);m.backend.current['busy'] = True;m.perform()
    assert m.job['error'] == 'PREVIOUS_ACTION_UNRESOLVED'
    m = manager(tmp_path)
    with dispatch_lock(tmp_path/'.runtime/logs/plans') as acquired:
        assert acquired
        m.perform()
    assert m.job['error'] == 'EXECUTOR_BUSY'
    assert not any(c[0] == 'stop' for c in m.backend.calls)


def test_pending_launch_steps_and_deterministic_actions_are_also_checked(tmp_path):
    pending(tmp_path, folder='deterministic')
    launch = dict(request=dict(request_id='launch-plan'), steps=[dict(request=dict(
        request_id='menu-exact', run_id='run', session_epoch='epoch', action_id='continue_game'))])
    atomic(tmp_path/'.runtime/logs/agent/launch/menu.json', launch)
    rows = pending_records(tmp_path, snapshot())
    assert {row[3] for row in rows} == {'exact', 'menu-exact'}


def test_duplicate_operation_and_concurrent_requests_never_spawn_twice(tmp_path, monkeypatch):
    calls = []
    class Thread:
        def __init__(self, **kwargs): pass
        def start(self): calls.append('worker')
    monkeypatch.setattr('autoslay.services.manager.threading.Thread', Thread)
    m = Manager(tmp_path, Backend())
    body = dict(action='restart', scope='agent', request_id='same')
    assert m.accept(body)['job']['status'] == 'running'
    assert m.accept(body)['job']['request_id'] == 'same'
    with pytest.raises(ValueError, match='SERVICE_OPERATION_BUSY'):
        m.accept(body | dict(request_id='other'))
    with pytest.raises(ValueError, match='REQUEST_ID_CONFLICT'):
        m.accept(body | dict(scope='all'))
    assert calls == ['worker']


def test_interrupted_manager_does_not_resume_or_repeat_operation(tmp_path):
    m = manager(tmp_path);m.save()
    resumed = Manager(tmp_path, Backend())
    assert resumed.job['status'] == 'interrupted'
    assert resumed.job['error'] == 'MANAGER_INTERRUPTED'
    assert resumed.backend.calls == []


def test_maintenance_blocks_agent_and_menu_start_but_status_remains_available(tmp_path, monkeypatch):
    from autoslay.agent.runtime import Agent
    from autoslay.agent.config import Store
    monkeypatch.setattr('autoslay.agent.runtime.ROOT', tmp_path)
    a = Agent(store=Store(tmp_path/'config.json'), journal=tmp_path/'agent', record=False)
    a._command = AsyncMock(return_value=dict(running=False))
    a.status = lambda: dict(running=False)
    with dispatch_lock(tmp_path/'.runtime/local/service-maintenance') as acquired:
        assert acquired
        for route in ('/start', '/play/start'):
            with pytest.raises(ValueError, match='SERVICE_MAINTENANCE'):
                asyncio.run(a.command(route, {}))
        assert asyncio.run(a.command('/status', {})) == dict(running=False)
    assert a._command.await_count == 0


def test_agent_health_status_does_not_queue_behind_slow_game_read(tmp_path):
    from autoslay.agent.runtime import Agent
    from autoslay.agent.config import Store
    a = Agent(store=Store(tmp_path/'config.json'), journal=tmp_path/'agent', record=False)
    async def check():
        async with a.lock:
            result = await asyncio.wait_for(a.command('/status', {}), .1)
            assert result['running'] is False
    asyncio.run(check())


@pytest.fixture
def service(tmp_path):
    m = Manager(tmp_path, Backend())
    server = make_server(m, port=0, credential=lambda: 'test-token')
    thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    yield m, 'http://127.0.0.1:'+str(server.server_port)
    server.shutdown();server.server_close();thread.join()


def test_independent_manager_allows_only_ui_origin_or_authenticated_cli(service):
    m, url = service
    for headers in ({}, {'Origin':'http://evil.test','X-Autoslay-ReadOnly':'1'},
                    {'Origin':'http://127.0.0.1:18767'}, {'Host':'localhost'}):
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(url+'/status', headers=headers), timeout=3)
        assert exc.value.code == 403
    headers = {'Origin':'http://127.0.0.1:18767','X-Autoslay-ReadOnly':'1'}
    with urlopen(Request(url+'/status', headers=headers), timeout=3) as response:
        assert response.headers['Access-Control-Allow-Origin'] == headers['Origin']
        assert json.load(response)['ready'] is True
    with urlopen(Request(url+'/status', headers={'Authorization':'Bearer test-token'}), timeout=3) as response:
        assert json.load(response)['ready'] is True
    with urlopen(Request(url+'/operate', method='OPTIONS', headers={
        'Origin':headers['Origin'], 'Access-Control-Request-Method':'POST'}), timeout=3) as response:
        assert response.status == 204


def test_manager_rejects_arbitrary_process_or_scope_parameters(service):
    _, url = service
    for value in (dict(action='kill',scope='all',request_id='bad'),
                  dict(action='restart',scope='game',request_id='bad'),
                  dict(action='restart',scope='agent',request_id='../escape'),
                  dict(action='restart',scope='agent',request_id='bad',pid=123)):
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(url+'/operate', data=json.dumps(value).encode(), headers={
                'Authorization':'Bearer test-token','Content-Type':'application/json'}), timeout=3)
        assert exc.value.code == 400


@pytest.mark.skipif(os.name != 'nt', reason='Windows process identity guard')
def test_reused_foreign_pid_is_absent_without_requesting_termination(tmp_path, monkeypatch):
    from autoslay.services import processes
    from autoslay.services.manager import Backend as RealBackend
    pidfile = tmp_path/'.runtime/local/dashboard.pid'
    pidfile.parent.mkdir(parents=True)
    pidfile.write_text('12345', encoding='ascii')
    monkeypatch.setattr(processes, 'process_executable', lambda pid: tmp_path/'foreign.exe')
    # A foreign executable is rejected before opening a handle with termination rights.
    monkeypatch.setattr(processes.ctypes, 'WinDLL', lambda *a, **k: pytest.fail('termination handle requested'))
    monkeypatch.setattr('autoslay.services.manager.occupied', lambda port: False)
    assert RealBackend(tmp_path).owner('dashboard') is None
    monkeypatch.setattr('autoslay.services.manager.occupied', lambda port: True)
    with pytest.raises(ValueError, match='PROCESS_IDENTITY_UNVERIFIED'):
        RealBackend(tmp_path).owner('dashboard')


@pytest.mark.parametrize('name', ['WmiPrvSE', 'python', None])
@pytest.mark.skipif(os.name != 'nt', reason='Windows process identity guard')
def test_protected_pid_requires_evidence_of_a_different_process_name(tmp_path, monkeypatch, name):
    from autoslay.services import processes
    def denied(pid):
        raise ValueError('PROCESS_IDENTITY_UNVERIFIED')
    monkeypatch.setattr(processes, 'process_executable', denied)
    monkeypatch.setattr(processes.subprocess, 'run', lambda *a, **k:
        subprocess.CompletedProcess(a, 0, stdout=json.dumps(name)))
    monkeypatch.setattr(processes.ctypes, 'WinDLL', lambda *a, **k: pytest.fail('termination handle requested'))
    expected = ProcessLookupError if name == 'WmiPrvSE' else ValueError
    with pytest.raises(expected):
        processes.OwnedProcess(12345, tmp_path/'python.exe', tmp_path/'dashboard.py')


@pytest.mark.skipif(os.name != 'nt', reason='Windows process identity guard')
def test_windows_owned_handle_rejects_wrong_script_and_stops_only_test_child(tmp_path):
    from autoslay.services.processes import OwnedProcess
    script = tmp_path/'harmless-service.py'
    script.write_text('import time\ntime.sleep(30)\n', encoding='utf-8')
    child = subprocess.Popen([sys.executable, str(script)], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        with pytest.raises(ValueError, match='PROCESS_IDENTITY_UNVERIFIED'):
            OwnedProcess(child.pid, Path(sys.executable), tmp_path/'other.py')
        assert child.poll() is None
        with OwnedProcess(child.pid, Path(sys.executable), script) as owner:
            owner.terminate()
        assert child.wait(timeout=3) == 0
    finally:
        if child.poll() is None:
            child.terminate();child.wait(timeout=3)


def test_process_program_must_be_the_service_not_a_python_inline_argument(tmp_path):
    from autoslay.services.processes import service_command
    executable = tmp_path/'python.exe';script = tmp_path/'agent_service.py'
    assert service_command([str(executable), str(script)], executable, script)
    assert service_command([str(executable), '-X', 'utf8', str(script)], executable, script)
    assert not service_command([str(executable), '-c', 'import time; time.sleep(30)', str(script)], executable, script)
    assert not service_command([str(executable), '-m', 'other', str(script)], executable, script)
