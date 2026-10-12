"""Live negative check: paused bridge must reject without dispatching."""
import asyncio
import json
import uuid
from autoslay.bridge.client import ActionRequest, fetch_state, get_action_status, submit_action
from autoslay.paths import ROOT


async def main():
    state = await fetch_state()
    assert state.get("bridge_version") in {"0.2.0", "0.3.0", "0.4.0", "0.5.0", "0.6.0", "0.7.0", "0.8.0", "0.9.0", "0.10.0", "0.10.1", "0.11.0", "0.12.0", "0.13.0", "0.13.1", "0.14.0"}, "Upgrade required"
    assert state["state"]["control_mode"] == "paused", "Pause control first"
    request = ActionRequest(request_id="pause-check-" + uuid.uuid4().hex,
                            run_id=state["state"].get("run_id") or "no-run",
                            session_epoch=state["session_epoch"], expected_state_version=state["state_version"], action_id="end_turn")
    receipt = await submit_action(request)
    assert receipt.get("error") == "CONTROL_PAUSED" and receipt.get("dispatched") is False
    repeat = await submit_action(request)
    assert repeat.get("error") == "CONTROL_PAUSED" and repeat.get("dispatched") is False
    status = await get_action_status(request.request_id)
    assert status.get("status") == "rejected"
    report = {"bridge_version": state["bridge_version"], "paused_command_rejected": True,
              "duplicate_receipt_verified": True, "status_query_verified": True, "dispatched": False}
    path = ROOT / ".runtime/logs/p1-paused-check.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main())
