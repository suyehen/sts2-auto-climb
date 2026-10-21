import asyncio
import json
from pathlib import Path
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import sys


def test_real_stdio_roundtrip():
    async def check():
        root = Path(__file__).resolve().parents[3]
        params = StdioServerParameters(command=sys.executable, args=[str(root / "src/python/src/autoslay/mcp/server.py")], cwd=str(root))
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {"get_action_catalog", "get_action_space", "get_entity_capabilities", "get_state", "get_installation_info", "act", "get_action_status", "set_control_mode", "start_new_run", "analyze_combat", "analyze_decision", "evaluate_run_choices", "record_decision_choice", "observe_decision_result", "draw_odds", "search_knowledge", "knowledge_status", "query_encyclopedia", "query_behavior_log", "refresh_encyclopedia", "query_game_mechanics", "execute_plan", "audit_knowledge_rules", "rollback_encyclopedia", "get_decision_record"}
                result = await session.call_tool("get_state", {})
                assert not result.isError
                data = json.loads(result.content[0].text)
                assert "connected" in data
                result = await session.call_tool("draw_odds", {"total_cards": 7, "target_cards": 1, "draws": 3})
                assert not result.isError
                odds = json.loads(result.content[0].text)
                assert odds["fraction"] == "3/7" and odds["execution_authorized"] is False
    asyncio.run(check())
