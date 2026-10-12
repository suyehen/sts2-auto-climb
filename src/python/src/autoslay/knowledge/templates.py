"""Canonical visible encyclopedia variants, refreshed when the local file changes."""
from pathlib import Path
from threading import RLock
import json
from autoslay.knowledge.catalog import CATALOG_PATH
from autoslay.decision.contracts import Card
from autoslay.decision.cache import digest

VERSION = "1.0.0"
_lock = RLock()
_loaded = None

def index(path=CATALOG_PATH, expected_version="0.111.0"):
    global _loaded
    path = Path(path)
    stat = path.stat()
    stamp = (str(path.resolve()), stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _loaded is None or _loaded[0] != stamp:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if data.get("status") != "complete": raise ValueError("Incomplete encyclopedia")
            _loaded = (stamp, data["game_version"], {e["entity_id"]:e for e in data["entries"]}, digest(data))
        if _loaded[1] != expected_version: raise ValueError("Stale encyclopedia version")
        return _loaded[2], _loaded[3]

def canonical(value, entries):
    entry = entries.get(value["entity_id"])
    if not entry or entry.get("category") != "card": raise ValueError("Card not in local encyclopedia")
    level = value.get("upgrade_level", 0)
    variant = next((v for v in entry["variants"] if v["upgrade_level"] == level), None)
    if variant is None or variant.get("costs_x") or variant.get('costs_star_x'):
        raise ValueError("Unsupported card variant/cost")
    return Card(value.get("instance_id", value["entity_id"]), value["entity_id"], variant["energy_cost"],
                variant["target_type"], variant["description"], level, variant["type"],star_cost=variant.get('star_cost',-1)), variant
