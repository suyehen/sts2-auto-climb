import asyncio
import pytest
from pydantic import ValidationError
from autoslay.bridge import client


def request():
    return client.ActionRequest(request_id="test-1", run_id="run", session_epoch="epoch",
                                expected_state_version=42, action_id="play_c1", target_id="e1")


def test_response_loss_queries_same_id_without_replaying(monkeypatch):
    calls = []
    async def transport(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if method == "POST":
            return {"connected": False, "error": "TIMEOUT"}
        return {"connected": True, "status": "completed", "request_id": "test-1", "dispatched": True}
    monkeypatch.setattr(client, "request_bridge", transport)
    result = asyncio.run(client.submit_action(request()))
    assert result["status"] == "completed"
    assert [c[0] for c in calls] == ["POST", "GET"]
    assert calls[1][2]["params"] == {"request_id": "test-1"}


def test_unknown_receipt_does_not_retry(monkeypatch):
    calls = []
    async def transport(method, path, **kwargs):
        calls.append(method)
        return {"error": "TIMEOUT"} if method == "POST" else {"status": "unknown", "dispatched": None}
    monkeypatch.setattr(client, "request_bridge", transport)
    result = asyncio.run(client.submit_action(request()))
    assert result["status"] == "unknown"
    assert result["dispatched"] is None
    assert calls == ["POST", "GET"]


def test_unsupported_choice_returns_to_caller(monkeypatch):
    async def transport(method, path, **kwargs):
        return {"status": "needs_user_input", "dispatched": True, "error": "UNSUPPORTED_CHOICE"}
    monkeypatch.setattr(client, "request_bridge", transport)
    assert asyncio.run(client.submit_action(request()))["status"] == "needs_user_input"


def test_pending_combat_choice_returns_without_polling_or_replay(monkeypatch):
    calls = []
    async def transport(method, path, **kwargs):
        calls.append(method)
        return {"status": "awaiting_choice", "dispatched": True, "request_id": "test-1"}
    monkeypatch.setattr(client, "request_bridge", transport)
    result = asyncio.run(client.submit_action(request()))
    assert result["status"] == "awaiting_choice"
    assert calls == ["POST"]


def test_old_bridge_reports_upgrade_without_status_poll(monkeypatch):
    calls = []
    async def transport(method, path, **kwargs):
        calls.append(method)
        return {"connected": True, "http_status": 404, "error": "BRIDGE_HTTP_ERROR"}
    monkeypatch.setattr(client, "request_bridge", transport)
    result = asyncio.run(client.submit_action(request()))
    assert result["error"] == "BRIDGE_UPGRADE_REQUIRED"
    assert result["dispatched"] is False
    assert calls == ["POST"]


@pytest.mark.parametrize("changes", [{"expected_state_version": "42"}, {"expected_state_version": -1},
                                    {"request_id": ""}, {"unexpected": True}])
def test_invalid_command_cannot_reach_transport(changes):
    fields = request().model_dump()
    fields.update(changes)
    with pytest.raises(ValidationError):
        client.ActionRequest(**fields)
