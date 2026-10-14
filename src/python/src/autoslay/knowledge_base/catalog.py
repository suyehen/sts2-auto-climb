"""Versioned local game reference. No simulation rules or game actions."""
import asyncio
import json
import os
import shutil
import tempfile
import time
from pathlib import Path
from autoslay.knowledge_base.repository import read_json, dataset_path
from autoslay.bridge.client import request_bridge

CATALOG_PATH = dataset_path("catalog")


def validate(data: dict) -> None:
    if data.get("schema_version") != "1.0" or data.get("status") != "complete":
        raise ValueError("Incomplete or unsupported encyclopedia")
    for field in ("game_version", "language", "exported_at", "scope"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise ValueError(f"Missing catalog field: {field}")
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Empty or invalid encyclopedia")
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("category") not in {"card", "potion", "relic"}:
            raise ValueError("Invalid encyclopedia entry")
        entity_id = entry.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id or entity_id in ids:
            raise ValueError("Invalid or duplicate encyclopedia ID")
        if not isinstance(entry.get("name"), str) or not entry["name"]:
            raise ValueError("Missing encyclopedia name")
        if entry["category"] == "card":
            variants = entry.get("variants")
            if not isinstance(variants, list) or not variants or any(
                not isinstance(v, dict) or not isinstance(v.get("description"), str) for v in variants
            ):
                raise ValueError("Invalid card variants")
        elif not isinstance(entry.get("description"), str):
            raise ValueError("Missing encyclopedia description")
        ids.add(entity_id)
    if data.get("errors"):
        raise ValueError(f"Export has {len(data['errors'])} failed entries; old catalog preserved")


def save_catalog(data: dict, path: Path = CATALOG_PATH, *, allow_rollback: bool = False) -> dict:
    from autoslay.knowledge_base.updates import LOCK
    from autoslay.execution.lock import dispatch_lock
    path=Path(path)
    with LOCK:
        with dispatch_lock(path.parent/'.catalog-lock') as acquired:
            if not acquired: raise OSError('Catalog update busy in another process; old catalog preserved')
            return _save_catalog(data,path,allow_rollback=allow_rollback)


def _save_catalog(data: dict, path: Path, *, allow_rollback: bool = False) -> dict:
    from autoslay.knowledge_base.updates import compare,content_fingerprint
    validate(data)
    path=Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = {k: v for k, v in data.items() if k != "connected"}
    old=read_json(path, default={}, fresh=True) or None
    if not allow_rollback and old and old.get('session_epoch') and old.get('session_epoch')==clean.get('session_epoch'):
        previous,current=old.get('generation'),clean.get('generation')
        if type(previous) is int and type(current) is int and current<previous:
            raise ValueError('Stale export generation; newer local catalog preserved')
    difference=compare(old,clean)
    clean['update_report']=difference
    if old and content_fingerprint(old)==content_fingerprint(clean):
        return dict(path=str(path),game_version=clean['game_version'],language=clean['language'],
                    counts={c:sum(e['category']==c for e in clean['entries']) for c in ('card','potion','relic')},
                    scope=clean['scope'],changed=False,update_report=difference)
    text = json.dumps(clean, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(prefix="encyclopedia-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            # Unique backups include both version metadata and the original complete payload.
            history = path.parent / "history"
            history.mkdir(exist_ok=True)
            shutil.copy2(path, history / f"encyclopedia-{time.time_ns()}.json")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    counts = {category: sum(e["category"] == category for e in clean["entries"])
              for category in ("card", "potion", "relic")}
    return {"path": str(path), "game_version": clean["game_version"], "language": clean["language"],
            "counts": counts, "scope": clean["scope"],"changed":True,"update_report":difference}


async def export_catalog(*, refresh: bool = False, path: Path = CATALOG_PATH, timeout: float = 90,
                         expected_game_version: str | None = None) -> dict:
    generation = None
    if refresh:
        receipt = await request_bridge("POST", "/encyclopedia-refresh")
        if receipt.get("error"):
            return receipt
        generation = receipt.get("generation")
        if not isinstance(generation, int):
            return {"error": "INVALID_REFRESH_RECEIPT"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = await request_bridge("GET", "/encyclopedia")
        if data.get("error"):
            return data
        if data.get("status") == "failed":
            return {"error": "CATALOG_EXPORT_FAILED", "message": data.get("message")}
        if data.get("status") == "complete" and (generation is None or data.get("generation", -1) >= generation):
            if expected_game_version is not None and data.get('game_version')!=expected_game_version:
                return dict(error='CATALOG_VERSION_MISMATCH',message='Export not saved; live version differs')
            try:
                from autoslay.knowledge_base.updates import compact_update
                return compact_update(await asyncio.to_thread(save_catalog, data, path))
            except (ValueError, OSError) as exc:
                return {"error": "CATALOG_NOT_REPLACED", "message": str(exc)}
        await asyncio.sleep(0.5)
    return {"error": "CATALOG_EXPORT_TIMEOUT", "message": "Open the main menu; no game action was taken. Old local catalog preserved."}


class Catalog:
    def __init__(self, path: Path | None = None):
        self.data = read_json(path or dataset_path("catalog"))
        validate(self.data)
        self.entries = {entry["entity_id"]: entry for entry in self.data["entries"]}

    def query(self, query: str, category: str | None = None, limit: int = 10,
              expected_game_version: str | None = None) -> dict:
        if not query.strip() or not 1 <= limit <= 50 or category not in {None, "card", "potion", "relic"}:
            raise ValueError("Supply a query, valid category and limit 1..50")
        needle = query.strip().casefold()
        matches = []
        exact = self.entries.get(query.strip())
        for entry in ([exact] if exact else self.entries.values()):
            if category and entry["category"] != category:
                continue
            if exact or needle in json.dumps(entry, ensure_ascii=False).casefold():
                matches.append(entry)
        matches.sort(key=lambda e: (e["name"].casefold() != needle, e["entity_id"]))
        stale = expected_game_version is not None and expected_game_version != self.data["game_version"]
        return {"game_version": self.data["game_version"], "language": self.data["language"],
                "exported_at": self.data["exported_at"], "scope": self.data["scope"],
                "version_matches": None if expected_game_version is None else not stale,
                "warning": "Game version differs; refresh before relying on these descriptions" if stale else None,
                "total_matches": len(matches), "entries": matches[:limit],
                "reference_only": True, "simulation_support": "consult rules registry separately"}
