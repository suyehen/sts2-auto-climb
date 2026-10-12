"""Explicit new-run setup; never makes in-run gameplay decisions."""
import asyncio
import json
import re
import uuid

from autoslay.bridge import client
from autoslay.paths import ROOT


def normalize_character(character: str) -> str:
    aliases = {"铁甲战士": "IRONCLAD", "战士": "IRONCLAD"}
    value = aliases.get(character.strip(), character.strip().upper())
    return value.removeprefix("CHARACTER.")


def next_menu_action(snapshot: dict, character: str, ascension: int | None) -> str:
    state = snapshot.get("state") or {}
    menu = state.get("menu") or {}
    if menu.get("has_saved_run"):
        raise ValueError("EXISTING_RUN: New-run setup cannot overwrite an unfinished save")
    phase = menu.get("phase")
    if phase == "main_menu":
        return "new_game"
    if phase == "singleplayer_menu":
        return "standard_run"
    if phase != "character_selection":
        raise ValueError("UNSUPPORTED_MENU: Start from the main menu or character selection")
    option = next((c for c in menu["characters"]
                   if normalize_character(c["entity_id"]) == character and not c.get("random")), None)
    if not option or not option.get("unlocked"):
        raise ValueError("CHARACTER_UNAVAILABLE: " + character)
    if not option.get("selected"):
        if not option.get("action_id"):
            raise ValueError("CHARACTER_NOT_READY")
        return option["action_id"]
    current, maximum = menu.get("ascension"), menu.get("max_ascension")
    if type(current) is not int or type(maximum) is not int:
        raise ValueError("ASCENSION_NOT_READY")
    target = maximum if ascension is None else ascension
    if not 0 <= target <= maximum:
        raise ValueError(f"ASCENSION_UNAVAILABLE: requested {target}, unlocked 0..{maximum}")
    return "ascension_up" if current < target else "ascension_down" if current > target else "embark"


async def start_new_run(request_id: str, character: str = "IRONCLAD",
                        ascension: int | None = None) -> dict:
    """Persist setup identity and each action before dispatch; never replay uncertain actions."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", request_id):
        return {"status": "rejected", "error": "INVALID_REQUEST_ID"}
    if ascension is not None and (type(ascension) is not int or ascension < 0):
        return {"status": "rejected", "error": "INVALID_ASCENSION"}
    character = normalize_character(character)
    path = ROOT / ".runtime/logs" / f"new-run-{request_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {"request_id": request_id, "character": character, "ascension": ascension,
              "status": "running", "steps": []}
    # Exclusive creation also blocks concurrent calls and retries of a completed setup.
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    except FileExistsError:
        previous = json.loads(path.read_text(encoding="utf-8"))
        if (previous["character"], previous["ascension"]) != (character, ascension):
            return {"status": "rejected", "error": "REQUEST_ID_CONFLICT"}
        return previous

    def save():
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        initial = await client.fetch_state()
        report["before"] = initial
        epoch = initial.get("session_epoch")
        if not initial.get("connected") or initial.get("busy") or not epoch or not (initial.get("state") or {}).get("menu", {}).get("decision_ready"):
            raise ValueError("GAME_NOT_READY")
        # Validate menu/save before enabling control.
        next_menu_action(initial, character, ascension)
        control = await client.set_control_mode("auto")
        if control.get("error"):
            raise ValueError(control["error"])
        for _ in range(64):
            snapshot = await client.fetch_state()
            if snapshot.get("session_epoch") != epoch:
                raise ValueError("SESSION_CHANGED")
            if not snapshot.get("ready") or snapshot.get("busy"):
                raise ValueError("GAME_NOT_READY")
            action = next_menu_action(snapshot, character, ascension)
            legal = snapshot["state"].get("legal_actions") or []
            if not any(a["action_id"] == action and a["allows_no_target"] for a in legal):
                raise ValueError("ACTION_UNAVAILABLE: " + action)
            if action == "embark":
                report["selected_menu"] = snapshot["state"]["menu"]
            request = client.ActionRequest(request_id="new-run-step-" + uuid.uuid4().hex,
                run_id=snapshot["state"]["command_context_id"], session_epoch=epoch,
                expected_state_version=snapshot["state_version"], action_id=action)
            step = {"request": request.model_dump(), "before": snapshot}
            report["steps"].append(step)
            save()
            receipt = await client.submit_action(request)
            step["receipt"] = receipt
            save()
            if receipt.get("status") != "completed":
                report.update(status="unresolved", error="ACTION_UNRESOLVED",
                              pending_request_id=request.request_id)
                return report
            if action == "embark":
                after = await client.fetch_state()
                report["after"] = after
                if after.get("session_epoch") != epoch or not (after.get("state") or {}).get("run_id"):
                    raise ValueError("NEW_RUN_NOT_VERIFIED")
                report["status"] = "completed"
                return report
            await asyncio.sleep(0.3)
        raise ValueError("SETUP_STEP_LIMIT")
    except (ValueError, KeyError) as exc:
        report.update(status="failed", error=str(exc))
        return report
    finally:
        # Setup ends at the first room. Pause even after unresolved dispatch; no replay.
        report["pause"] = await client.set_control_mode("paused")
        save()
