"""Start an explicitly requested new run through the MCP setup tool."""
import argparse
import asyncio
import json
import sys
import uuid

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from autoslay.paths import ROOT


async def main(args):
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "src/python/src/autoslay/mcp/server.py")], cwd=str(ROOT))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("start_new_run", {
                "request_id": args.request_id, "character": args.character, "ascension": args.ascension})
            if result.isError:
                raise RuntimeError(str(result.content))
            report = json.loads(result.content[0].text)
    after = report.get("after", {}).get("state", {})
    print(json.dumps({"request_id": args.request_id, "status": report.get("status"),
        "error": report.get("error"), "pending_request_id": report.get("pending_request_id"),
        "selected_menu": report.get("selected_menu"), "run_id": after.get("run_id"),
        "phase": after.get("phase"),
        "evidence": str(ROOT / ".runtime/logs" / f"new-run-{args.request_id}.json")}, ensure_ascii=False, indent=2))
    return 0 if report.get("status") == "completed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--character", default="IRONCLAD")
    parser.add_argument("--ascension", type=int, default=None)
    parser.add_argument("--request-id", default="codex-" + uuid.uuid4().hex)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
