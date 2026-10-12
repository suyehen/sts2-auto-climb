import asyncio
import pytest
from autoslay.bridge import new_run


def snapshot(selected=True, current=3, maximum=10):
    return {"connected": True, "ready": True, "session_epoch": "epoch", "state_version": 1, "state": {
        "command_context_id": "menu", "menu": {"phase": "character_selection",
        "decision_ready": True, "has_saved_run": False, "ascension": current, "max_ascension": maximum,
        "characters": [{"entity_id": "CHARACTER.IRONCLAD", "unlocked": True,
                        "selected": selected, "action_id": "character_1"}]},
        "legal_actions": [{"action_id": "embark", "allows_no_target": True}]}}


def test_select_character_before_resolving_character_specific_maximum():
    assert new_run.next_menu_action(snapshot(False, maximum=20), "IRONCLAD", None) == "character_1"
    assert new_run.next_menu_action(snapshot(maximum=10), "IRONCLAD", None) == "ascension_up"
    assert new_run.next_menu_action(snapshot(current=10), "IRONCLAD", None) == "embark"


def test_explicit_level_zero_and_lower_level():
    assert new_run.next_menu_action(snapshot(), "IRONCLAD", 0) == "ascension_down"
    assert new_run.next_menu_action(snapshot(current=0), "IRONCLAD", 0) == "embark"
    with pytest.raises(ValueError, match="ASCENSION_UNAVAILABLE"):
        new_run.next_menu_action(snapshot(), "IRONCLAD", 11)


def test_saved_run_and_locked_character_block_setup():
    value = snapshot()
    value["state"]["menu"]["has_saved_run"] = True
    with pytest.raises(ValueError, match="EXISTING_RUN"):
        new_run.next_menu_action(value, "IRONCLAD", None)
    value["state"]["menu"]["has_saved_run"] = False
    value["state"]["menu"]["characters"][0]["unlocked"] = False
    with pytest.raises(ValueError, match="CHARACTER_UNAVAILABLE"):
        new_run.next_menu_action(value, "IRONCLAD", None)


def test_uncertain_embark_is_persisted_and_never_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr(new_run, "ROOT", tmp_path)
    calls = []
    async def fetch(): return snapshot(current=10)
    async def control(mode): return {"mode": mode}
    async def submit(request):
        calls.append(request)
        # Identity must be on disk before the mutation.
        assert request.request_id in (tmp_path / ".runtime/logs/new-run-test.json").read_text()
        return {"status": "unknown"}
    monkeypatch.setattr(new_run.client, "fetch_state", fetch)
    monkeypatch.setattr(new_run.client, "set_control_mode", control)
    monkeypatch.setattr(new_run.client, "submit_action", submit)
    result = asyncio.run(new_run.start_new_run("test"))
    assert result["status"] == "unresolved"
    assert result["pending_request_id"] == calls[0].request_id
    assert asyncio.run(new_run.start_new_run("test"))["status"] == "unresolved"
    assert len(calls) == 1
    assert asyncio.run(new_run.start_new_run("test", ascension=0))["error"] == "REQUEST_ID_CONFLICT"


def test_chinese_character_alias():
    assert new_run.normalize_character("铁甲战士") == "IRONCLAD"
