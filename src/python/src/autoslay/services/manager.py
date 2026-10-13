"""Independent loopback supervisor. Restarts never submit or replay game actions."""
import asyncio
from contextlib import ExitStack
import copy
import hmac
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
import httpx
from autoslay.paths import ROOT
from autoslay.archive.repository import atomic
from autoslay.bridge.client import load_config, set_control_mode, fetch_state, get_action_status
from autoslay.execution.lock import dispatch_lock
from .processes import OwnedProcess

PORT = 18770
UI_ORIGIN = 'http://127.0.0.1:18767'
SERVICES = {
    'execution': (18766, 'execution_service.py', 'execution-service.pid'),
    'agent': (18769, 'agent_service.py', 'agent.pid'),
    'dashboard': (18767, 'decision_dashboard.py', 'dashboard.pid'),
}
SETTLED = {'completed', 'failed', 'rejected', 'awaiting_choice', 'needs_user_input'}


def token():
    value = Path(load_config().token_file).read_text().strip()
    if not value:
        raise ValueError('NOT_CONFIGURED')
    return value


def occupied(port):
    with socket.socket() as connection:
        connection.settimeout(.5)
        return connection.connect_ex(('127.0.0.1', port)) == 0


class Backend:
    def __init__(self, root=ROOT):
        self.root = root
        self.python = root/'.runtime/conda/python.exe'

    def path(self, name):
        return self.root/'src/python/tools/runtime'/SERVICES[name][1]

    def health(self, name):
        port = SERVICES[name][0]
        try:
            with httpx.Client(trust_env=False, timeout=1, follow_redirects=False) as client:
                if name == 'dashboard':
                    response = client.get(f'http://127.0.0.1:{port}/api/archive/cursor', headers={'X-Autoslay-ReadOnly': '1'})
                else:
                    response = client.post(f'http://127.0.0.1:{port}'+('/status' if name == 'agent' else '/health'),
                                           json={}, headers={'Authorization': 'Bearer '+token()})
                response.raise_for_status()
                data = response.json()
                ready = (type(data.get('cursor')) is int if name == 'dashboard' else
                         isinstance(data.get('running'), bool) and isinstance(data.get('profiles'), list) if name == 'agent' else
                         data.get('ready') is True and data.get('persistent_transport') is True)
                return dict(ready=ready, running=data.get('running') if name == 'agent' else None)
        except (httpx.HTTPError, OSError, ValueError):
            return dict(ready=False, running=None)

    def owner(self, name):
        pidfile = self.root/'.runtime/local'/SERVICES[name][2]
        try:
            pid = int(pidfile.read_text(encoding='ascii').strip())
        except FileNotFoundError:
            if occupied(SERVICES[name][0]):
                raise ValueError('PROCESS_IDENTITY_UNVERIFIED')
            return None
        except (OSError, ValueError):
            raise ValueError('PROCESS_IDENTITY_UNVERIFIED') from None
        try:
            return OwnedProcess(pid, self.python, self.path(name))
        except ProcessLookupError:
            if occupied(SERVICES[name][0]):
                raise ValueError('PROCESS_IDENTITY_UNVERIFIED') from None
            return None

    def ensure(self, name):
        if self.health(name)['ready']:
            return
        if occupied(SERVICES[name][0]):
            raise ValueError('SERVICE_PORT_OCCUPIED')
        # Also refuse to duplicate a live process that has not bound its port yet.
        existing = self.owner(name)
        if existing:
            existing.close()
            raise ValueError('SERVICE_NOT_READY')
        logs = self.root/'.runtime/logs'; logs.mkdir(parents=True, exist_ok=True)
        command = [str(self.python), '-X', 'utf8', str(self.path(name))]
        if name == 'dashboard':
            command.append('--no-browser')
        with (logs/f'{SERVICES[name][0]}.out.log').open('a') as out, (logs/f'{SERVICES[name][0]}.err.log').open('a') as err:
            child = subprocess.Popen(command, cwd=self.root, stdout=out, stderr=err,
                                     creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        pidfile = self.root/'.runtime/local'/SERVICES[name][2]
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        pidfile.write_text(str(child.pid), encoding='ascii')
        deadline = time.monotonic()+20
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise ValueError('SERVICE_START_FAILED')
            health = self.health(name)
            if health['ready']:
                if name == 'agent' and health['running']:
                    raise ValueError('AGENT_NOT_PAUSED')
                return
            time.sleep(.2)
        raise ValueError('SERVICE_START_TIMEOUT')

    def stop(self, name, owner):
        if owner:
            owner.terminate()
        if occupied(SERVICES[name][0]):
            raise ValueError('SERVICE_STOP_FAILED')
        (self.root/'.runtime/local'/SERVICES[name][2]).unlink(missing_ok=True)

    def pause(self):
        # A responsive Agent cancels its model/launch task. A hung Agent is still
        # fenced by bridge paused mode and the shared dispatch lock below.
        try:
            with httpx.Client(trust_env=False, timeout=22, follow_redirects=False) as client:
                client.post('http://127.0.0.1:18769/pause', json={}, headers={'Authorization': 'Bearer '+token()})
        except (httpx.HTTPError, OSError, ValueError):
            pass
        result = asyncio.run(set_control_mode('paused'))
        snapshot = result.get('snapshot') or asyncio.run(fetch_state())
        if snapshot.get('connected') and (result.get('error') or snapshot.get('error') or
                                         (snapshot.get('state') or {}).get('control_mode') != 'paused'):
            raise ValueError('GAME_PAUSE_UNCONFIRMED')
        return snapshot

    def snapshot(self):
        return asyncio.run(fetch_state())

    def receipt(self, request_id):
        return asyncio.run(get_action_status(request_id))


def pending_records(root, snapshot):
    """Current-session durable requests only; old-session evidence remains untouched."""
    epoch = snapshot.get('session_epoch')
    state = snapshot.get('state') or {}
    run = state.get('command_context_id') or state.get('run_id')
    rows = []
    for folder in ('agent', 'agent/launch', 'deterministic', 'plans'):
        for path in (root/'.runtime/logs'/folder).glob('*.json'):
            record = json.loads(path.read_text(encoding='utf-8'))
            steps = [record] if record.get('request', {}).get('action_id') else record.get('steps') or []
            for step in steps:
                request = step.get('request') or {}
                if not request.get('request_id') or (step.get('receipt') or {}).get('status', step.get('status')) in SETTLED:
                    continue
                if epoch and run and (request.get('session_epoch') != epoch or request.get('run_id') != run):
                    continue
                rows.append((path, record, step, request['request_id']))
    return rows


class Manager:
    def __init__(self, root=ROOT, backend=None, settle_timeout=30):
        self.root, self.backend, self.settle_timeout = root, backend or Backend(root), settle_timeout
        self.lock = threading.Lock()
        self.job = None
        latest = root/'.runtime/local/service-operation.json'
        if latest.exists():
            self.job = json.loads(latest.read_text(encoding='utf-8'))
            if self.job['status'] == 'running':
                self.job.update(status='interrupted', phase='interrupted', error='MANAGER_INTERRUPTED')
                self.save()

    def save(self):
        atomic(self.root/'.runtime/local/service-operation.json', self.job)
        atomic(self.root/'.runtime/local/service-operations'/(self.job['request_id']+'.json'), self.job)

    def phase(self, phase, service=None):
        with self.lock:
            self.job.update(phase=phase, service=service)
            self.save()

    def state(self, request_id=None):
        with self.lock:
            job = copy.deepcopy(self.job)
            maintenance_active = bool(job and job['status'] == 'running')
        if request_id and (not job or request_id != job['request_id']):
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', request_id):
                raise ValueError('INVALID_REQUEST')
            path = self.root/'.runtime/local/service-operations'/(request_id+'.json')
            job = json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        return dict(ready=True, job=job, maintenance_active=maintenance_active,
                    services={name: self.backend.health(name) for name in SERVICES})

    def accept(self, body):
        if (set(body) != {'action', 'scope', 'request_id'} or
                not all(isinstance(body[k], str) for k in body) or body['action'] not in {'start', 'stop', 'restart'} or
                body['scope'] not in {'agent', 'all'} or not isinstance(body['request_id'], str) or
                not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', body['request_id'])):
            raise ValueError('INVALID_REQUEST')
        with self.lock:
            path = self.root/'.runtime/local/service-operations'/(body['request_id']+'.json')
            if path.exists():
                old = json.loads(path.read_text(encoding='utf-8'))
                if any(old[k] != body[k] for k in body):
                    raise ValueError('REQUEST_ID_CONFLICT')
                return dict(job=old)
            if self.job and self.job['status'] == 'running':
                raise ValueError('SERVICE_OPERATION_BUSY')
            self.job = dict(**body, status='running', phase='checking', error=None,
                            started_at=time.time(), paused=False)
            self.save()
            result = dict(job=copy.deepcopy(self.job))
        threading.Thread(target=self.perform, daemon=True).start()
        return result

    def settle(self, snapshot, deadline):
        rows = pending_records(self.root, snapshot)
        while True:
            remaining = []
            for path, record, step, request_id in rows:
                receipt = self.backend.receipt(request_id)
                if receipt.get('status') not in SETTLED:
                    remaining.append((path, record, step, request_id))
                    continue
                step.update(receipt=receipt, recovered_by_status_query=True)
                if step is record:
                    record['status'] = receipt['status']
                atomic(path, record)
            rows = remaining
            fresh = self.backend.snapshot()
            if fresh.get('connected'):
                if fresh.get('error') or (fresh.get('state') or {}).get('control_mode') != 'paused':
                    raise ValueError('GAME_PAUSE_UNCONFIRMED')
                if snapshot.get('session_epoch') and fresh.get('session_epoch') != snapshot['session_epoch']:
                    raise ValueError('GAME_SESSION_CHANGED')
                old_run = (snapshot.get('state') or {}).get('command_context_id') or (snapshot.get('state') or {}).get('run_id')
                new_run = (fresh.get('state') or {}).get('command_context_id') or (fresh.get('state') or {}).get('run_id')
                if old_run and old_run != new_run:
                    raise ValueError('GAME_SESSION_CHANGED')
            if not rows and not fresh.get('busy'):
                return fresh
            if time.monotonic() >= deadline:
                raise ValueError('PREVIOUS_ACTION_UNRESOLVED')
            time.sleep(.25)

    def perform(self):
        try:
            action, scope = self.job['action'], self.job['scope']
            names = ['agent'] if scope == 'agent' else ['agent', 'execution', 'dashboard']
            # Prevent independently launched managers/commands from overlapping.
            with dispatch_lock(self.root/'.runtime/local/service-maintenance') as exclusive:
                if not exclusive:
                    raise ValueError('SERVICE_OPERATION_BUSY')
                if action == 'start':
                    for name in (['execution', 'agent'] if scope == 'agent' else ['execution', 'agent', 'dashboard']):
                        self.phase('starting', name); self.backend.ensure(name)
                else:
                    with ExitStack() as owners:
                        handles = {name: (owners.enter_context(handle) if (handle := self.backend.owner(name)) else None)
                                   for name in names}
                        self.phase('pausing'); snapshot = self.backend.pause()
                        self.phase('settling'); deadline = time.monotonic()+self.settle_timeout
                        while True:
                            with dispatch_lock(self.root/'.runtime/logs/plans') as acquired:
                                if acquired:
                                    self.settle(snapshot, deadline)
                                    for name in names:
                                        self.phase('stopping', name); self.backend.stop(name, handles[name])
                                    if action == 'restart':
                                        for name in (['execution', 'agent'] if scope == 'agent' else ['execution', 'agent', 'dashboard']):
                                            self.phase('starting', name); self.backend.ensure(name)
                                    break
                            if time.monotonic() >= deadline:
                                raise ValueError('EXECUTOR_BUSY')
                            time.sleep(.25)
                    with self.lock:
                        self.job['paused'] = True
            with self.lock:
                self.job.update(status='completed', phase='completed', service=None, finished_at=time.time())
                self.save()
        except Exception as exc:
            code = str(exc)
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}', code):
                code = 'SERVICE_MANAGER_ERROR'
            with self.lock:
                self.job.update(status='failed', error=code, finished_at=time.time())
                self.save()


def make_server(manager, port=PORT, ui_origin=UI_ORIGIN, credential=token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def allowed(self):
            if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}':
                return False
            origin = self.headers.get('Origin')
            if origin:
                return origin == ui_origin and self.headers.get('X-Autoslay-ReadOnly') == '1'
            return hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+credential())
        def send(self, value, code=200):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            if self.headers.get('Origin') == ui_origin:
                self.send_header('Access-Control-Allow-Origin', ui_origin)
                self.send_header('Vary', 'Origin')
            self.end_headers(); self.wfile.write(data)
        def do_OPTIONS(self):
            if (self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}' or
                    self.headers.get('Origin') != ui_origin or
                    self.headers.get('Access-Control-Request-Method') not in {'GET', 'POST'}):
                self.send(dict(error='UNAUTHORIZED'), 403); return
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', ui_origin)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Autoslay-ReadOnly')
            self.send_header('Vary', 'Origin')
            self.end_headers()
        def handle_request(self):
            try:
                if not self.allowed():
                    self.send(dict(error='UNAUTHORIZED'), 403); return
                parsed = urlsplit(self.path)
                if self.command == 'GET' and parsed.path == '/status':
                    self.send(manager.state(parse_qs(parsed.query).get('request_id', [None])[0])); return
                if self.command == 'POST' and parsed.path == '/operate':
                    length = int(self.headers.get('Content-Length', '0'))
                    if not 0 < length <= 1000 or self.headers.get('Transfer-Encoding'):
                        raise ValueError('INVALID_REQUEST')
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict): raise ValueError('INVALID_REQUEST')
                    self.send(manager.accept(body), 202); return
                self.send(dict(error='UNKNOWN_ROUTE'), 404)
            except ValueError as exc:
                code = str(exc)
                self.send(dict(error=code if re.fullmatch(r'[A-Z][A-Z0-9_]{1,79}', code) else 'INVALID_REQUEST'), 400)
            except Exception:
                self.send(dict(error='SERVICE_MANAGER_ERROR'), 503)
        do_GET = handle_request
        do_POST = handle_request
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve():
    with dispatch_lock(ROOT/'.runtime/local/service-manager-owner') as acquired:
        if not acquired:
            raise RuntimeError('Service manager already running')
        manager = Manager()
        server = make_server(manager)
        (ROOT/'.runtime/local/service-manager.pid').write_text(str(os.getpid()), encoding='ascii')
        print(f'Service manager ready: http://127.0.0.1:{PORT}', flush=True)
        try: server.serve_forever()
        finally: server.server_close()
