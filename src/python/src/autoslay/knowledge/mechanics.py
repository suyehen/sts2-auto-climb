"""Persistent versioned rules notes queried independently of the card encyclopedia."""
import json
from autoslay.paths import ROOT

VERSION = "1.0.0"


def references(query: str, game_version: str = "0.111.0") -> dict:
    if game_version != "0.111.0":
        return {"error": "UNSUPPORTED_MECHANICS_VERSION", "game_version": game_version}
    if not isinstance(query, str) or not query.strip():
        return {"error": "QUERY_REQUIRED"}
    data = json.loads((ROOT / "data/knowledge/mechanics/game-0.111.0.json").read_text(encoding="utf-8"))
    needle = query.strip().casefold()
    return {"game_version": data["game_version"], "version": data["version"], "verification": data["verification"],
            "entries": [e for e in data["entries"] if needle in json.dumps(e, ensure_ascii=False).casefold()]}
