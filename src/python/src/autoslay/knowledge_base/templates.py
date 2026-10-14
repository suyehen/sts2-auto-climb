"""Canonical visible encyclopedia variants, refreshed when the local file changes."""
from autoslay.knowledge_base.repository import read_view, dataset_path
from autoslay.decision.contracts import Card
from autoslay.decision.cache import digest

VERSION = "1.0.0"
def index(path=None, expected_version="0.111.0"):
    path = path or dataset_path("catalog")
    def build(data):
        if data.get("status") != "complete": raise ValueError("Incomplete encyclopedia")
        return data['game_version'], {e["entity_id"]:e for e in data["entries"]}, digest(data)
    version, entries, fingerprint = read_view(path, 'canonical-catalog/1', build)
    if version != expected_version: raise ValueError("Stale encyclopedia version")
    return entries, fingerprint


def canonical(value, entries):
    entry = entries.get(value["entity_id"])
    if not entry or entry.get("category") != "card": raise ValueError("Card not in local encyclopedia")
    level = value.get("upgrade_level", 0)
    variant = next((v for v in entry["variants"] if v["upgrade_level"] == level), None)
    if variant is None or variant.get("costs_x") or variant.get('costs_star_x'):
        raise ValueError("Unsupported card variant/cost")
    return Card(value.get("instance_id", value["entity_id"]), value["entity_id"], variant["energy_cost"],
                variant["target_type"], variant["description"], level, variant["type"],star_cost=variant.get('star_cost',-1)), variant
