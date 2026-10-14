"""Persistent versioned rules notes queried independently of the card encyclopedia."""
import json
from autoslay.knowledge_base.repository import read_json, dataset_path

VERSION = "1.0.0"


def references(query: str, game_version: str = "0.111.0") -> dict:
    if not isinstance(query, str) or not query.strip():
        return {"error": "QUERY_REQUIRED"}
    data = read_json(dataset_path("mechanics"))
    if data.get("game_version") != game_version:
        return {"error": "UNSUPPORTED_MECHANICS_VERSION", "game_version": game_version}
    needle = query.strip().casefold()
    return {"game_version": data["game_version"], "version": data["version"], "verification": data["verification"],
            "entries": [e for e in data["entries"] if needle in json.dumps(e, ensure_ascii=False).casefold()]}
