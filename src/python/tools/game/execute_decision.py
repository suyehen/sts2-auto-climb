"""Execute one explicit Codex decision through MCP; no strategy or gameplay loop."""
import asyncio
import json
import sys
import uuid
from time import perf_counter
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from autoslay.paths import ROOT
from autoslay.telemetry.timing import Timing
from autoslay.telemetry.journal import append_record


async def main():
    clock = Timing()
    startup = perf_counter()
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "src/python/src/autoslay/mcp/server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            clock.stages_ms["mcp_startup"] = (perf_counter() - startup) * 1000

            async def call(name, args):
                result = await session.call_tool(name, args)
                if result.isError:
                    raise RuntimeError(str(result.content))
                return json.loads(result.content[0].text)

            with clock.stage("state_read_before"):
                before = await call("get_state", {})
            if not before.get("ready") or before.get("busy"):
                raise RuntimeError("Game is not ready for a decision")
            action_id = sys.argv[1]
            target_id = sys.argv[2] if len(sys.argv) > 2 else None
            legal = next((a for a in before["state"]["legal_actions"] if a["action_id"] == action_id), None)
            if not legal or (target_id not in legal["allowed_target_ids"] if target_id else not legal["allows_no_target"]):
                raise RuntimeError("Explicit decision is not legal in current state")
            context_id = before["state"].get("command_context_id") or before["state"].get("run_id")
            if not context_id:
                raise RuntimeError("Current state has no command context")
            request = dict(request_id="codex-" + uuid.uuid4().hex, run_id=context_id,
                           session_epoch=before["session_epoch"], expected_state_version=before["state_version"],
                           action_id=action_id, target_id=target_id)
            path = ROOT / ".runtime/logs" / (request["request_id"] + ".json")
            # Persist identity before dispatch so an interrupted call can be queried.
            evidence = {"before": before, "request": request}
            path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            with clock.stage("action_and_game_settlement"):
                receipt = await call("act", request)
            with clock.stage("state_read_after"):
                after = await call("get_state", {})
            evidence.update(receipt=receipt, after=after, timings_ms=clock.stages_ms)
            path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            (ROOT / ".runtime/logs/latest-decision.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
            append_record(ROOT / ".runtime/logs/action-timings.jsonl", {
                "request_id": request["request_id"], "action_id": action_id,
                "status": receipt.get("status"), "timings_ms": clock.stages_ms,
                "unmeasured": ["Codex thinking time; requires external turn timing"]})
            state = after.get("state", {})
            player = state.get("player") or {}
            print(json.dumps({"request_id": request["request_id"], "status": receipt.get("status"),
                "error": receipt.get("error"), "ready": after.get("ready"), "busy": after.get("busy"),
                "phase": state.get("phase"), "turn": player.get("turn"), "energy": player.get("energy"),
                "player": player.get("creature"), "hand": player.get("hand"), "enemies": state.get("enemies"),
                "legal_actions": state.get("legal_actions"), "menu": state.get("menu"),
                "reward_screen": state.get("reward_screen"), "map": state.get("map"), "event_screen": state.get("event_screen"), "card_selection": state.get("card_selection"), "shop": state.get("shop"), "rest_site": state.get("rest_site"), "evidence": str(path)}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
