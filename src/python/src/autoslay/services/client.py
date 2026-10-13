"""CLI and startup use the same supervisor as the dashboard buttons."""
import os
import subprocess
import time
from uuid import uuid4
import httpx
from autoslay.paths import ROOT
from .manager import PORT, token, occupied


def request(path, body=None):
    with httpx.Client(trust_env=False, timeout=8, follow_redirects=False) as client:
        headers = {'Authorization': 'Bearer '+token()}
        response = (client.get if body is None else client.post)(
            f'http://127.0.0.1:{PORT}'+path, headers=headers, **({} if body is None else {'json': body}))
        data = response.json()
        if response.status_code >= 400 or data.get('error'):
            raise ValueError(data.get('error', 'SERVICE_MANAGER_UNAVAILABLE'))
        return data


def ensure_manager():
    if occupied(PORT):
        if request('/status').get('ready') is not True:
            raise ValueError('SERVICE_PORT_OCCUPIED')
        return
    logs = ROOT/'.runtime/logs'; logs.mkdir(parents=True, exist_ok=True)
    with (logs/f'{PORT}.out.log').open('a') as out, (logs/f'{PORT}.err.log').open('a') as err:
        child = subprocess.Popen([str(ROOT/'.runtime/conda/python.exe'), '-X', 'utf8',
                                  str(ROOT/'src/python/tools/runtime/service_manager.py')],
                                 cwd=ROOT, stdout=out, stderr=err,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    deadline = time.monotonic()+15
    while time.monotonic() < deadline:
        try:
            if request('/status').get('ready') is True:
                return
        except (httpx.HTTPError, OSError, ValueError):
            pass
        if child.poll() is not None:
            raise ValueError('SERVICE_MANAGER_START_FAILED')
        time.sleep(.2)
    raise ValueError('SERVICE_MANAGER_START_TIMEOUT')


def operate(action, scope='agent', progress=None):
    ensure_manager()
    identity = uuid4().hex
    request('/operate', dict(action=action, scope=scope, request_id=identity))
    deadline = time.monotonic()+150
    previous = None
    while time.monotonic() < deadline:
        data = request('/status?request_id='+identity)
        job = data.get('job') or {}
        current = (job.get('status'), job.get('phase'), job.get('service'))
        if current != previous and progress:
            progress(job)
        previous = current
        if job.get('status') in {'failed', 'interrupted'}:
            raise ValueError(job.get('error') or 'SERVICE_MANAGER_ERROR')
        if job.get('status') == 'completed':
            return data
        time.sleep(.3)
    raise ValueError('SERVICE_OPERATION_TIMEOUT')
