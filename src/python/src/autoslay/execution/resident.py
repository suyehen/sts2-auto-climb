"""Authenticated loopback service. Keeps bridge transport alive between Codex calls."""
import asyncio
import hmac
import json
from pathlib import Path
from autoslay.bridge.client import load_config, persistent_transport, fetch_state
from autoslay.decision.service import analyze
from autoslay.execution.sequence import SequenceExecutor
from autoslay.execution.deterministic import DeterministicExecutor
from autoslay.decision.deterministic import assess as deterministic_assess
from autoslay.decision.unified import analyze_decision, evaluate_run_choices
from autoslay.calibration import store as calibration
from autoslay.telemetry.journal import append_record
from autoslay.paths import ROOT
from time import perf_counter
from autoslay.telemetry import decisions
from autoslay.telemetry import runs
from autoslay.knowledge.events import observe as observe_event
from autoslay.knowledge.updates import CatalogSynchronizer

PORT = 18766

async def serve(port=PORT, ready=None):
    token = Path(load_config().token_file).read_text().strip()
    if not token:
        raise ValueError("Empty bridge token")
    executor = SequenceExecutor()
    deterministic_executor = DeterministicExecutor()
    synchronizer=CatalogSynchronizer()
    async def handle(reader, writer):
        started=perf_counter(); stages={}; route='unknown'; snapshot=None
        status, result = 200, {}
        try:
            header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = header.decode("ascii").split("\r\n")
            method, route, _ = lines[0].split()
            fields = dict(line.split(":", 1) for line in lines[1:] if ":" in line)
            fields = {k.lower(): v.strip() for k, v in fields.items()}
            if not hmac.compare_digest(fields.get("authorization", ""), "Bearer " + token):
                status, result = 401, {"error": "UNAUTHORIZED"}
            elif method != "POST" or route not in {"/state", "/analyze", "/decision", "/compare", "/plan", "/health", "/autonomy", "/calibration/choice", "/calibration/observe"}:
                status, result = 404, {"error": "UNKNOWN_ROUTE"}
            else:
                length = int(fields.get("content-length", "0"))
                if not 0 <= length <= 1_000_000 or "transfer-encoding" in fields:
                    raise ValueError("Unsupported body")
                body = json.loads(await asyncio.wait_for(reader.readexactly(length), 5) or b"{}")
                if route == "/health":
                    result = {"ready": True, "version": "1.3.0", "persistent_transport": True,"catalog_sync":synchronizer.status}
                elif route == "/state":
                    result = snapshot = await fetch_state()
                elif route == '/autonomy':
                    if body.get('execute') is True:
                        result = await deterministic_executor.execute(body['request_id'])
                    else:
                        snapshot = await fetch_state()
                        result = deterministic_assess({}, snapshot)
                elif route == "/analyze":
                    snapshot=await fetch_state()
                    result = await asyncio.to_thread(analyze, snapshot)
                    result['decision_record']=await asyncio.to_thread(decisions.archive_tool_result,result,snapshot,'combat')
                elif route in {'/decision','/compare'}:
                    mark=perf_counter(); snapshot=await fetch_state(); stages['bridge_read_ms']=(perf_counter()-mark)*1000
                    mark=perf_counter()
                    if not snapshot.get('connected') or snapshot.get('error'): result=snapshot
                    elif route=='/decision':
                        await asyncio.to_thread(runs.stage,'analysis_started',snapshot)
                        result=await asyncio.to_thread(analyze_decision,snapshot,body.get('context'),body.get('scenarios'),body.get('use_cache',True))
                        archive_started=perf_counter()
                        try: result['calibration_evidence']=await asyncio.to_thread(calibration.archive,result,snapshot)
                        except OSError as exc: result['calibration_evidence']=dict(archived=False,error=type(exc).__name__)
                        result['decision_record']=await asyncio.to_thread(decisions.archive,result,snapshot)
                        result=decisions.compact(result)
                        stages['archive_ms']=(perf_counter()-archive_started)*1000
                    else:
                        result=await asyncio.to_thread(evaluate_run_choices,snapshot,body['kind'],body['options'],body.get('context'))
                        result['decision_record']=await asyncio.to_thread(decisions.archive_tool_result,result,snapshot,body['kind'],body['options'])
                    stages['analysis_ms']=(perf_counter()-mark)*1000
                elif route in {'/calibration/choice','/calibration/observe'}:
                    snapshot=await fetch_state()
                    if not snapshot.get('connected') or snapshot.get('error'): result=snapshot
                    elif route=='/calibration/choice': result=await asyncio.to_thread(calibration.choose,body['analysis_id'],body['group'],body['index'],body['reason'],snapshot,body.get('trial_id'))
                    else: result=await asyncio.to_thread(calibration.observe,body['decision_id'],body['horizon'],snapshot,body.get('note',''),body.get('trial_result'))
                    if not result.get('error'):
                        await asyncio.to_thread(runs.stage,'ai_choice' if route=='/calibration/choice' else 'outcome_observed',snapshot,
                            {k:v for k,v in body.items() if k in {'analysis_id','decision_id','group','index','reason','horizon','note'}})
                else:
                    result = await executor.execute(body)
                    first=(result.get('steps') or [{}])[0].get('before')
                    if first:
                        public_steps=[dict(request=step.get('request'),receipt={k:v for k,v in (step.get('receipt') or {}).items() if k in {'request_id','status','error','message','dispatched'}},action_ms=step.get('action_ms')) for step in result.get('steps',[])]
                        await asyncio.to_thread(runs.stage,'action_plan',first,dict(plan=result.get('plan'),status=result.get('status'),reason=result.get('reason'),total_ms=result.get('total_ms'),steps=public_steps))
                if snapshot:
                    await synchronizer.observe(snapshot)
                    if snapshot.get('connected') and (snapshot.get('state') or {}).get('phase')=='event':
                        await asyncio.to_thread(observe_event,snapshot)
        except (ValueError, KeyError, TypeError, asyncio.TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            status, result = 400, {"error": "INVALID_REQUEST"}
        except Exception as exc:
            # A saved pending request remains queryable; never replay after failure.
            status, result = 500, {"error": "SERVICE_ERROR", "type": type(exc).__name__,
                                   "message": "Inspect the persisted plan; do not replay with a new ID."}
        payload = json.dumps(result, ensure_ascii=True).encode()
        stages['service_elapsed_ms']=(perf_counter()-started)*1000
        await asyncio.to_thread(append_record,ROOT/'.runtime/logs/service-timings.jsonl',dict(route=route,status=status,
            timings_ms=stages,response_bytes=len(payload),measurement_scope='resident_request',
            bridge_connected=bool(snapshot and snapshot.get('connected')),session_epoch=(snapshot or {}).get('session_epoch'),
            unmeasured=['Codex reasoning/tool roundtrip','animation vs backend within action settlement']))
        writer.write(f"HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode() + payload)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    async with persistent_transport():
        from autoslay.archive.monitor import monitor
        stop_monitor=asyncio.Event()
        monitor_task=asyncio.create_task(monitor(stop_monitor))
        server = await asyncio.start_server(handle, "127.0.0.1", port, limit=16384)
        if ready is not None:
            ready.set_result(server.sockets[0].getsockname()[1])
        try:
            async with server:
                await server.serve_forever()
        finally:
            stop_monitor.set()
            await monitor_task
            await synchronizer.close()
