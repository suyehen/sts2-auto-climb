"""Two explicit lifecycle actions through MCP; no forced termination or gameplay policy."""
import asyncio
import json
import sys
import uuid
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from autoslay.paths import ROOT

async def main():
    report = {"steps": []}
    path = ROOT / ".runtime/logs/exit-game.json"
    async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(ROOT / "src/python/src/autoslay/mcp/server.py")], cwd=str(ROOT))) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            async def call(name, args):
                response = await session.call_tool(name, args)
                if response.isError: raise RuntimeError(str(response.content))
                return json.loads(response.content[0].text)
            state = await call("get_state", {})
            if not state.get("connected") or not state.get("session_epoch"): raise RuntimeError("Game bridge unavailable; normal exit not dispatched")
            report["session_epoch"] = state["session_epoch"]
            if state.get("busy"): raise RuntimeError("An action is pending; resolve its receipt before exiting")
            await call("set_control_mode", {"mode": "auto"})
            async def action(action_id):
                state = await call("get_state", {})
                if not state.get("ready") or state.get("busy") or not any(a["action_id"] == action_id for a in state["state"]["legal_actions"]):
                    raise RuntimeError("Lifecycle action not available: " + action_id)
                request = dict(request_id="lifecycle-" + uuid.uuid4().hex, run_id=state["state"]["command_context_id"],
                    session_epoch=state["session_epoch"], expected_state_version=state["state_version"], action_id=action_id, target_id=None)
                step = {"request": request}
                report["steps"].append(step)
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt = await call("act", request)
                step["receipt"] = receipt
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                if receipt.get("status") != "completed": raise RuntimeError("Lifecycle action unresolved; query " + request["request_id"])
            if state["state"].get("run_id"):
                await action("return_to_main_menu")
            await action("quit_game")
    print("Normal exit scheduled; process termination must be verified before deployment.")

if __name__ == "__main__": asyncio.run(main())
