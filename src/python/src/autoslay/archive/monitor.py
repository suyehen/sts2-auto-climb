"""Capture clocks and public state while Codex reasons; never changes control or acts."""
import asyncio
import logging
from uuid import uuid4
from autoslay.bridge.client import fetch_state
from autoslay.telemetry.decisions import public_snapshot
from autoslay.telemetry.runs import location
from autoslay.archive.repository import for_legacy,now,atomic
from autoslay.archive.collector import store

async def monitor(stop,interval=2):
    while not stop.is_set():
        try:
            snapshot=await fetch_state()
            await asyncio.to_thread(atomic,for_legacy().root/'.live-status.json',dict(snapshot=public_snapshot(snapshot),observed_at=now()))
            if snapshot.get('connected') and (snapshot.get('state') or {}).get('run_id'):
                await asyncio.to_thread(store,dict(event_id=uuid4().hex,kind='heartbeat',recorded_at=now(),
                    location=location(snapshot),snapshot=public_snapshot(snapshot)))
            await asyncio.to_thread(for_legacy().recover_pending)
        except Exception:
            logging.exception('Read-only run monitor failed')
        try:await asyncio.wait_for(stop.wait(),interval)
        except asyncio.TimeoutError:pass
