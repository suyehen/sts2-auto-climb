"""Invoke one explicit MCP tool; contains no gameplay strategy or action loop."""
import asyncio
import json
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from autoslay.paths import ROOT


async def main():
    tool = sys.argv[1]
    arguments = json.loads((ROOT / sys.argv[2]).read_text(encoding="utf-8-sig")) if len(sys.argv) > 2 else {}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "src/python/src/autoslay/mcp/server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            response = await session.call_tool(tool, arguments)
            if response.isError:
                raise RuntimeError(str(response.content))
            result = json.loads(response.content[0].text)
    output = ROOT / ".runtime/logs" / ("mcp-" + tool + ".json")
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    display = dict(result)
    snapshot = display.get("snapshot", display)
    if isinstance(snapshot, dict) and "state" in snapshot:
        snapshot = dict(snapshot)
        snapshot["state"] = dict(snapshot["state"])
        if snapshot["state"].get("player"):
            snapshot["state"]["player"] = dict(snapshot["state"]["player"])
            snapshot["state"]["player"].pop("deck", None)
            for pile in ("hand", "discard", "exhaust"):
                snapshot["state"]["player"][pile] = [
                    {key: card[key] for key in ("instance_id", "entity_id", "energy_cost")}
                    for card in (snapshot["state"]["player"].get(pile) or [])
                ]
        if "snapshot" in display:
            display["snapshot"] = snapshot
        else:
            display = snapshot
    print(json.dumps(display, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
