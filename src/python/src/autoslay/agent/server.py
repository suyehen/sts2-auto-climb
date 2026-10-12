"""Authenticated loopback control; starts paused, launch requires an explicit command."""
import asyncio
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from autoslay.bridge.client import load_config, persistent_transport
from autoslay.execution.lock import dispatch_lock
from autoslay.paths import ROOT
from .runtime import Agent

PORT = 18769


def make_server(agent, loop, port=PORT):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, value, code=200):
            data = json.dumps(value, ensure_ascii=False).encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
        def do_POST(self):
            try:
                expected = Path(load_config().token_file).read_text().strip()
                if (not expected or self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}'
                    or self.headers.get('Origin') or not hmac.compare_digest(
                        self.headers.get('Authorization', ''), 'Bearer '+expected)):
                    self.send(dict(error='UNAUTHORIZED'), 403); return
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 250000 or self.headers.get('Transfer-Encoding'): raise ValueError()
                body = json.loads(self.rfile.read(length) or b'{}')
                if not isinstance(body, dict): raise ValueError()
                future = asyncio.run_coroutine_threadsafe(agent.command(self.path, body), loop)
                try: self.send(future.result(timeout=20))
                except TimeoutError:
                    future.cancel()
                    self.send(dict(error='CONTROL_TIMEOUT'), 503)
            except ValueError as exc:
                # Pydantic exceptions contain input data; publish only our bounded error codes.
                code = str(exc)
                self.send(dict(error=code if code.isupper() and len(code) < 80 else 'INVALID_REQUEST'), 400)
            except KeyError: self.send(dict(error='UNKNOWN_PROFILE'), 400)
            except Exception: self.send(dict(error='AGENT_SERVICE_ERROR'), 503)
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


async def run(port=PORT):
    with dispatch_lock(ROOT/'.runtime/local/agent-owner') as acquired:
        if not acquired: raise RuntimeError('Agent already running')
        async with persistent_transport():
            agent = Agent()
            server = make_server(agent, asyncio.get_running_loop(), port)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print(f'Agent paused: http://127.0.0.1:{port}', flush=True)
            try: await asyncio.Event().wait()
            finally:
                await agent.pause()
                server.shutdown()
                server.server_close()
