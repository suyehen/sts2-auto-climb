import asyncio
import copy
import json
import pytest
from autoslay.knowledge import catalog


def payload():
    return {"schema_version": "1.0", "status": "complete", "game_version": "0.111.0",
            "language": "zhs", "exported_at": "2026-10-09", "scope": "currently_unlocked_reference_models",
            "generation": 1, "errors": [], "entries": [
                {"entity_id": "CARD.STRIKE_IRONCLAD", "category": "card", "name": "打击",
                 "variants": [{"upgrade_level": 0, "description": "造成6点伤害"},
                              {"upgrade_level": 1, "description": "造成9点伤害"}]},
                {"entity_id": "POTION.FIRE", "category": "potion", "name": "火焰药水", "description": "造成伤害"}]}


def test_offline_queries_and_version_warning(tmp_path):
    path = tmp_path / "encyclopedia.json"
    catalog.save_catalog(payload(), path)
    db = catalog.Catalog(path)
    assert db.query("CARD.STRIKE_IRONCLAD")["entries"][0]["name"] == "打击"
    assert db.query("伤害", "potion")["total_matches"] == 1
    assert len(db.query("打击")["entries"][0]["variants"]) == 2
    assert db.query("打击", expected_game_version="0.112.0")["version_matches"] is False


def test_failed_refresh_preserves_old_and_success_keeps_history(tmp_path):
    path = tmp_path / "encyclopedia.json"
    data = payload()
    catalog.save_catalog(data, path)
    before = path.read_bytes()
    broken = copy.deepcopy(data)
    broken["errors"] = [{"entity_id": "X", "message": "failed"}]
    with pytest.raises(ValueError):
        catalog.save_catalog(broken, path)
    assert path.read_bytes() == before
    data["game_version"] = "0.112.0"
    catalog.save_catalog(data, path)
    assert json.loads(path.read_text(encoding="utf-8"))["game_version"] == "0.112.0"
    assert next((tmp_path / "history").glob("*.json")).read_bytes() == before


def test_refresh_waits_for_new_generation(monkeypatch, tmp_path):
    calls = []
    old = payload()
    newer = copy.deepcopy(old)
    newer["generation"] = 2
    async def request(method, path):
        calls.append((method, path))
        if method == "POST":
            return {"generation": 2}
        return old if len(calls) == 2 else newer
    async def no_wait(_):
        pass
    monkeypatch.setattr(catalog, "request_bridge", request)
    monkeypatch.setattr(catalog.asyncio, "sleep", no_wait)
    result = asyncio.run(catalog.export_catalog(refresh=True, path=tmp_path / "catalog.json"))
    assert "error" not in result
    assert calls == [("POST", "/encyclopedia-refresh"), ("GET", "/encyclopedia"), ("GET", "/encyclopedia")]


def test_reject_duplicate_and_empty_catalog():
    data = payload()
    data["entries"].append(data["entries"][0])
    with pytest.raises(ValueError):
        catalog.validate(data)
    data["entries"] = []
    with pytest.raises(ValueError):
        catalog.validate(data)
