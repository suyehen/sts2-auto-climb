"""Local game transport. Mutations are never automatically resubmitted."""
import asyncio
import time
from pathlib import Path
from contextlib import asynccontextmanager
from contextvars import ContextVar
import httpx
from pydantic import BaseModel, ConfigDict, Field
from autoslay.paths import ROOT

_transport = ContextVar("bridge_transport", default=None)
_snapshots = {}

@asynccontextmanager
async def persistent_transport():
    """Reuse HTTP connections within one long-lived event loop; credentials stay local."""
    async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
        marker = _transport.set(client)
        try:
            yield
        finally:
            _transport.reset(marker)

class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = "http://127.0.0.1:18765"
    token_file: str = str(ROOT / ".runtime/local/bridge-token.txt")

class ActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    request_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1)
    session_epoch: str = Field(min_length=1)
    expected_state_version: int = Field(ge=0)
    action_id: str = Field(min_length=1)
    target_id: str | None = None

def load_config() -> BridgeConfig:
    path = ROOT / ".runtime/local/bridge-config.json"
    return BridgeConfig.model_validate_json(path.read_text(encoding="utf-8-sig")) if path.exists() else BridgeConfig()

async def request_bridge(method: str, path: str, *, body: dict | None = None, params: dict | None = None) -> dict:
    try:
        config = load_config()
        url = httpx.URL(config.url)
        if url.host not in {"127.0.0.1", "localhost", "::1"} or url.scheme != "http" or url.userinfo or url.query or url.fragment:
            return {"connected": False, "error": "INVALID_CONFIG"}
        token_path = Path(config.token_file)
        if not token_path.exists():
            return {"connected": False, "error": "NOT_CONFIGURED"}
        token = token_path.read_text().strip()
        async def send(client):
            response = await client.request(method, config.url.rstrip("/") + path, json=body, params=params,
                                            headers={"Authorization": f"Bearer {token}"})
            if response.status_code != 200:
                return {"connected": True, "error": "BRIDGE_HTTP_ERROR", "http_status": response.status_code}
            data = response.json()
            if not isinstance(data, dict) or data.get("schema_version") != "1.0":
                return {"connected": True, "error": "INVALID_RESPONSE"}
            return {**data, "connected": True}
        if _transport.get() is not None:
            return await send(_transport.get())
        async with httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False) as client:
            return await send(client)
    except httpx.TimeoutException:
        return {"connected": False, "error": "TIMEOUT"}
    except httpx.RequestError:
        return {"connected": False, "error": "NOT_CONNECTED"}
    except (ValueError, OSError):
        return {"connected": False, "error": "INVALID_CONFIG_OR_RESPONSE"}

async def fetch_state() -> dict:
    result=await request_bridge("GET", "/state")
    s=result.get('state') or {}
    if result.get('connected') and not result.get('error'):
        _snapshots[(result.get('session_epoch'),s.get('run_id') or s.get('command_context_id'),result.get('state_version'))]=result
        while len(_snapshots)>32: _snapshots.pop(next(iter(_snapshots)))
    from autoslay.telemetry.runs import safe_observe
    await asyncio.to_thread(safe_observe,result)
    return result

async def get_action_status(request_id: str) -> dict:
    result=await request_bridge("GET", "/action-status", params={"request_id": request_id})
    if result.get('status') in {'completed','rejected','failed','needs_user_input','awaiting_choice','unknown'}:
        from autoslay.telemetry.runs import safe_action
        await asyncio.to_thread(safe_action,'action_result',dict(request_id=request_id),result)
    return result

async def set_control_mode(mode: str) -> dict:
    if mode not in {"auto", "paused"}:
        return {"error": "INVALID_CONTROL_MODE"}
    result = await request_bridge("POST", "/control", body={"mode": mode})
    if not result.get("error"):
        await asyncio.sleep(0.3)
        result["snapshot"] = await fetch_state()
    return result

async def submit_action(request: ActionRequest, wait_seconds: float = 25) -> dict:
    from autoslay.telemetry.runs import safe_action
    # Reuse the exact state the caller based this action on; no extra game round trip.
    before=_snapshots.get((request.session_epoch,request.run_id,request.expected_state_version),{})
    await asyncio.to_thread(safe_action,'action_submitted',request.model_dump(),dict(snapshot=before))
    result = await request_bridge("POST", "/act", body=request.model_dump())
    await asyncio.to_thread(safe_action,'action_receipt',request.model_dump(),result)
    if result.get("http_status") == 404:
        return {**result, "error": "BRIDGE_UPGRADE_REQUIRED", "request_id": request.request_id, "dispatched": False}
    if result.get("http_status") in {400, 401, 403}:
        return {**result, "request_id": request.request_id, "dispatched": False}
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if result.get("status") in {"completed", "rejected", "failed", "needs_user_input", "awaiting_choice"}:
            return result
        await asyncio.sleep(0.25)
        result = await get_action_status(request.request_id)
        if result.get("status") == "unknown":
            return {**result, "request_id": request.request_id,
                    "message": "Result unknown. Read current state and verify session before making another decision; do not replay blindly."}
    return {**result, "request_id": request.request_id, "wait_expired": True,
            "message": "Query this request ID again; do not submit a replacement action."}
