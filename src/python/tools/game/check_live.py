"""Check live read-only bridge and its actual MCP transport; save local evidence."""
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from autoslay.bridge.client import fetch_state
from autoslay.paths import ROOT


async def main():
    first = await fetch_state()
    if not first.get("connected"):
        print(json.dumps(first, ensure_ascii=True))
        return 1
    await asyncio.sleep(1)
    second = await fetch_state()
    if not second.get("connected") or first.get("session_epoch") != second.get("session_epoch"):
        raise RuntimeError("Connection or bridge session changed during observation")
    if first.get("captured_at") == second.get("captured_at"):
        raise RuntimeError("Snapshot is not updating")
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "src/python/src/autoslay/mcp/server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.call_tool("get_state", {})
            if response.isError:
                raise RuntimeError("MCP get_state failed")
            result = json.loads(response.content[0].text)
            if not result.get("connected") or result.get("session_epoch") != second.get("session_epoch"):
                raise RuntimeError("MCP did not read the same live bridge")
    log_dir = ROOT / ".runtime/logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "p0-snapshot.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    state = result.get("state", {})
    player = state.get("player") or {}
    print(json.dumps({
        "http_connected": True, "mcp_connected": True, "snapshot_updating": True,
        "state_version": result.get("state_version"), "phase": state.get("phase"),
        "player": player.get("creature"), "energy": player.get("energy"),
        "hand": [c["name"] for c in player.get("hand") or []], "enemies": state.get("enemies"),
        "evidence": str(log_dir / "p0-snapshot.json")
    }, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
