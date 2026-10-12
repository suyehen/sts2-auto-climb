"""Wait for a new bridge session and optionally continue the existing save."""
import argparse
import asyncio
import json
import sys
import time
import uuid
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from autoslay.paths import ROOT

async def main(args):
    async with stdio_client(StdioServerParameters(command=sys.executable, args=[str(ROOT / "src/python/src/autoslay/mcp/server.py")], cwd=str(ROOT))) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                if response.isError: raise RuntimeError(str(response.content))
                return json.loads(response.content[0].text)
            deadline = time.monotonic() + 60
            while True:
                state = await call("get_state", {})
                menu = state.get("state", {}).get("menu", {})
                if (state.get("bridge_version") == args.version and state.get("session_epoch") != args.previous_epoch
                    and menu.get("phase") == "main_menu" and menu.get("decision_ready")):
                    break
                if time.monotonic() > deadline: raise RuntimeError("New bridge session/main menu not ready within 60s")
                await asyncio.sleep(1)
            report = {"startup": state, "continued": False}
            path = ROOT / ".runtime/logs/restart-ready.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if args.continue_save and menu.get("has_saved_run"):
                if not menu.get("save_loadable"): raise RuntimeError("Existing save cannot be loaded")
                await call("set_control_mode", {"mode": "auto"})
                state = await call("get_state", {})
                if not state.get("ready") or not any(a["action_id"] == "continue_game" for a in state["state"]["legal_actions"]):
                    raise RuntimeError("Continue action is unavailable")
                request = dict(request_id="restart-" + uuid.uuid4().hex, run_id=state["state"]["command_context_id"],
                    session_epoch=state["session_epoch"], expected_state_version=state["state_version"], action_id="continue_game", target_id=None)
                report["request"] = request
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                receipt = await call("act", request)
                report.update(receipt=receipt, continued=receipt.get("status") == "completed", after=await call("get_state", {}))
                path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                if not report["continued"]: raise RuntimeError("Continue unresolved; query " + request["request_id"])
            print(json.dumps({"bridge_version": state["bridge_version"], "session_epoch": state["session_epoch"], "continued": report["continued"]}))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    parser.add_argument("--previous-epoch", default="")
    parser.add_argument("--continue-save", action="store_true")
    asyncio.run(main(parser.parse_args()))
