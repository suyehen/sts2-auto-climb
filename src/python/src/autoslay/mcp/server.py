"""Codex MCP entry point. Tool definitions only; logic lives in autoslay."""
import json
import asyncio
from autoslay.knowledge_base.service import SERVICE as knowledge_service
from autoslay.knowledge_base.repository import scoped

from mcp.server.fastmcp import FastMCP
from autoslay.bridge.client import ActionRequest, fetch_state, get_action_status as query_action, set_control_mode as control, submit_action
from autoslay.paths import ROOT
from autoslay.bridge.new_run import start_new_run as setup_run
from autoslay.decision.service import analyze, load_policy
from autoslay.planning.search import SearchBudget
from autoslay.probability.draw import at_least_one
from autoslay.telemetry.timing import Timing
from autoslay.telemetry.journal import append_record
from autoslay.knowledge_base.catalog import Catalog, export_catalog
from autoslay.evaluation.relic_timing import OpeningContext
from autoslay.knowledge_base.mechanics import references as mechanism_references
from autoslay.execution.sequence import SequenceExecutor
from autoslay.bridge.client import persistent_transport
from contextlib import asynccontextmanager
from autoslay.decision.unified import analyze_decision as unified_analysis, evaluate_run_choices as run_comparison
from autoslay.calibration import store as calibration
from autoslay.telemetry import decisions
from autoslay.telemetry import runs
from autoslay.knowledge_base.rulebook import Rulebook
from autoslay.knowledge_base.events import observe as observe_event
from autoslay.knowledge_base.updates import CatalogSynchronizer,rollback

synchronizer=CatalogSynchronizer()

@asynccontextmanager
async def lifespan(server):
    async with persistent_transport():
        try: yield {}
        finally: await synchronizer.close()

mcp = FastMCP("AutoSlayTheSpire", lifespan=lifespan)
sequence_executor = SequenceExecutor()


@mcp.tool()
@scoped
def get_action_catalog(action_type: str | None = None) -> dict:
    """Read global command definitions or reverse lookup potential providers; never grants live permission."""
    from autoslay import actions
    if action_type:
        return dict(definition=actions.definition(action_type),providers=actions.entities_for_action(action_type))
    return actions.catalog()


@mcp.tool()
@scoped
def get_entity_capabilities(entity_id: str) -> dict:
    """Read potential commands and existing effect/trigger/move references, including coverage status."""
    from autoslay.actions import entity_capabilities
    return entity_capabilities(entity_id)


@mcp.tool()
async def get_action_space() -> dict:
    """Observe live action/source/target bindings; no game action is submitted."""
    from autoslay.actions import describe
    snapshot=await fetch_state()
    return await asyncio.to_thread(describe,snapshot)

@mcp.tool()
async def analyze_decision(context: dict | None = None, scenarios: list | None = None, use_cache: bool = True) -> dict:
    """Unified read-only combat/reward/shop/event/map report, adaptive budgets and cache.
    Combat includes bounded draw continuations under EXPLICIT future-attack scenarios;
    default repeats visible attacks, not actual enemy future moves. Conditional metrics
    are not win probabilities. Context can supply next_room/enemy_count for run choices.
    Never executes. Fresh snapshot/source are required even on a cache hit.
    """
    try:
        snapshot=await fetch_state()
        if not snapshot.get('connected') or snapshot.get('error'): return snapshot
        await synchronizer.observe(snapshot)
        await asyncio.to_thread(observe_event,snapshot)
        await asyncio.to_thread(runs.stage,'analysis_started',snapshot)
        report=await asyncio.to_thread(unified_analysis,snapshot,context,scenarios,use_cache)
        try: report['calibration_evidence']=await asyncio.to_thread(calibration.archive,report,snapshot)
        except OSError as exc: report['calibration_evidence']=dict(archived=False,error=type(exc).__name__)
        report['decision_record']=await asyncio.to_thread(decisions.archive,report,snapshot)
        return decisions.compact(report)
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return {'error':'ANALYSIS_UNAVAILABLE','message':str(exc),'execution_authorized':False}

@mcp.tool()
async def record_decision_choice(analysis_id: str, group: str, index: int, reason: str, trial_id: str | None = None) -> dict:
    """Record Codex's explicit choice and rationale BEFORE action, without executing.
    Uses archived unified report; fresh epoch/version/state hash must match.
    group: shortlist/observations/choices/shop/event/route/rest/combat.candidates.
    Supply stable trial_id to group a complete run; default groups current session/run.
    Reuse the same analysis_id only for identical choice, never change old evidence.
    """
    try:
        snapshot=await fetch_state()
        if not snapshot.get('connected') or snapshot.get('error'): return snapshot
        result=await asyncio.to_thread(calibration.choose,analysis_id,group,index,reason,snapshot,trial_id)
        await asyncio.to_thread(runs.stage,'ai_choice',snapshot,dict(analysis_id=analysis_id,group=group,index=index,reason=reason))
        return result
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return dict(error='CALIBRATION_RECORD_UNAVAILABLE',message=str(exc),execution_authorized=False)

@mcp.tool()
async def observe_decision_result(decision_id: str, horizon: str, note: str = '', trial_result: str | None = None) -> dict:
    """Record current public outcome for an earlier intent; never executes or invents counterfactuals.
    horizon action/turn/combat/run; trial_result win/loss/abandoned only at run horizon,
    explicitly caller-reported. Different sessions/runs require reviewed offline import.
    A recorded intent alone does not prove its action was executed.
    """
    try:
        snapshot=await fetch_state()
        if not snapshot.get('connected') or snapshot.get('error'): return snapshot
        result=await asyncio.to_thread(calibration.observe,decision_id,horizon,snapshot,note,trial_result)
        await asyncio.to_thread(runs.stage,'outcome_observed',snapshot,dict(decision_id=decision_id,horizon=horizon,note=note))
        return result
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return dict(error='CALIBRATION_OBSERVATION_UNAVAILABLE',message=str(exc),execution_authorized=False)

@mcp.tool()
async def evaluate_run_choices(kind: str, options: list, context: dict | None = None) -> dict:
    """Read-only explicit add/remove/upgrade/shop/event comparisons on the actual deck.
    Events may supply structured effects or outcomes with caller probabilities; these
    are caller assumptions, never hidden game data or executable authorizations.
    Shop options use item_id/category/price/card or entity_id; no purchase is dispatched.
    """
    try:
        snapshot=await fetch_state()
        if not snapshot.get('connected') or snapshot.get('error'): return snapshot
        result=await asyncio.to_thread(run_comparison,snapshot,kind,options,context)
        result['decision_record']=await asyncio.to_thread(decisions.archive_tool_result,result,snapshot,kind,options)
        return result
    except (ValueError,KeyError,TypeError,OSError) as exc:
        return {'error':'COMPARISON_UNAVAILABLE','message':str(exc),'execution_authorized':False}

@mcp.tool()
async def execute_plan(plan: dict) -> dict:
    """Execute a bounded explicit Codex combat prefix, never select strategy.
    Requires plan_id, fresh session_epoch/run_id/expected_state_version and 1..12
    steps [{action_id,target_id}]. Each step is checked against fresh legal actions.
    Deterministic modeled results must match before continuing. Unknown, random,
    draw, choice, end-turn or phase transitions return control immediately.
    Plans and child IDs persist BEFORE dispatch; reuse the same plan ID to read
    its record, never restart an interrupted plan with a new ID.
    """
    return await sequence_executor.execute(plan)


@mcp.tool()
@scoped
def query_game_mechanics(query: str, game_version: str = "0.111.0") -> dict:
    """Offline versioned mechanisms: draw capacity, generated overflow, draw prevention,
    persistent periodic counters. Includes source and limitations, no game actions.
    """
    return mechanism_references(query, game_version)


@mcp.tool()
@scoped
def query_encyclopedia(query: str, category: str | None = None, limit: int = 10,
                       expected_game_version: str | None = None) -> dict:
    """Offline reference lookup by entity ID, name or keyword; no game connection.
    Pass the live snapshot's game_version to detect stale data. Descriptions do not
    imply simulation support. Categories: card, potion, relic, event, monster, intent; limit 1..50.
    """
    try:
        if category in {'event','monster','intent'}:
            from autoslay.knowledge_base.behavior import query as query_behavior
            return query_behavior(query,category,limit,game_version=expected_game_version,for_agent=True)
        return Catalog().query(query, category, limit, expected_game_version)
    except (ValueError, OSError) as exc:
        return {"error": "CATALOG_UNAVAILABLE", "message": str(exc)}


@mcp.tool()
@scoped
def query_behavior_log(query: str, category: str = '', limit: int = 10,
                       expected_game_version: str | None = None, include_evidence: bool = False) -> dict:
    """Offline event/monster/intent evidence, options, moves and source provenance.
    Unreviewed programs are references, not executable rules. Only request source
    method evidence when needed; do not load the whole library into model context.
    """
    from autoslay.knowledge_base.behavior import query as query_behavior
    try:return query_behavior(query,category,limit,game_version=expected_game_version,include_evidence=include_evidence,for_agent=True)
    except (ValueError,OSError) as exc:return dict(error='BEHAVIOR_UNAVAILABLE',message=str(exc))


@mcp.tool()
async def refresh_encyclopedia() -> dict:
    """Explicitly refresh local encyclopedia from the loaded bridge in main menu.
    No gameplay action, control change or save loading. Validated successful exports
    replace the local file atomically, retaining history; failed exports preserve it.
    Updated games may require a compatible bridge first.
    """
    return await export_catalog(refresh=True)


@mcp.tool()
@scoped
def audit_knowledge_rules() -> dict:
    """Report complete catalog coverage and executable rule compatibility; never approves rules."""
    return Rulebook().audit()


@mcp.tool()
def search_knowledge(query: str, category: str | None = None, dataset: str | None = None,
                     expected_game_version: str | None = None,
                     expected_assembly_hash: str | None = None, language: str | None = None,
                     limit: int = 10, include_related: bool = False) -> dict:
    """Offline keyword/alias search with build filters and explicit related notes.
    Results cite a pinned knowledge revision. Ranking never enables simulation
    rules. No embedding, rerank, remote API or gameplay action is used.
    """
    try:
        return knowledge_service.search(query, category=category, dataset=dataset,
            game_version=expected_game_version, assembly_hash=expected_assembly_hash,
            language=language, limit=limit, include_related=include_related)
    except (ValueError, OSError) as exc:
        return dict(error='KNOWLEDGE_UNAVAILABLE', message=str(exc))


@mcp.tool()
def knowledge_status() -> dict:
    """Local knowledge availability, revision and process-local cache diagnostics."""
    try:
        return knowledge_service.status()
    except (ValueError, OSError) as exc:
        return dict(error='KNOWLEDGE_UNAVAILABLE', message=str(exc))


@mcp.tool()
def rollback_encyclopedia(history_filename: str) -> dict:
    """Restore a named local catalog history snapshot. Rule compatibility remains checked."""
    from autoslay.knowledge_base.updates import compact_update
    return compact_update(rollback(history_filename))


@mcp.tool()
def get_decision_record(record_id: str, origin: str = 'live') -> dict:
    """Read a detailed archived decision. Replay evidence is separately labelled."""
    return decisions.load(record_id,origin)


@mcp.tool()
async def analyze_combat(max_nodes: int = 2000, time_ms: int = 200, top_k: int = 5,
                         opening_draw: int | None = None, opening_hand: int = 0,
                         later_draw: int = 5, later_hand: int = 0) -> dict:
    """Read-only bounded combat candidates for Codex; never dispatches an action.
    Includes separate draw prefixes, coverage warnings, heuristic weights and module versions.
    Unknown effects block exact simulation. Potion effects and draw continuations have
    partial explicit coverage; inspect gaps and do not treat scores as win probabilities.
    Handles capacity/prevention and ordinary reshuffle marginals. Supported periodic relics
    add joint timing/wait scenarios only when present. Future draw/hand parameters are explicit
    assumptions (default opening draw 5), not known next-combat state. Full waiting costs and
    future kills are not certified from current intents; no automatic stalling.
    Re-read legal actions before executing a chosen step. Paused games remain paused.
    """
    try:
        budget = SearchBudget(max_nodes=max_nodes, time_ms=time_ms, top_k=top_k)
        clock = Timing()
        with clock.stage("bridge_read"):
            snapshot = await fetch_state()
        if not snapshot.get("connected") or snapshot.get("error"):
            return {"error": snapshot.get("error", "NOT_CONNECTED"), "execution_authorized": False}
        report = await asyncio.to_thread(analyze, snapshot, budget=budget,
                                        policy=load_policy(ROOT / "config/decision/combat.default.json"),
                                        timing_context=OpeningContext(opening_draw=5 if opening_draw is None else opening_draw,
                                            opening_hand=opening_hand, later_draw=later_draw, later_hand=later_hand,
                                            basis="caller_supplied_what_if_context" if opening_draw is not None else
                                                  "assumed_opening_draw_5; verify innate/retain/relic modifiers"))
        report["timings_ms"].update(clock.stages_ms)
        await asyncio.to_thread(append_record, ROOT / ".runtime/logs/decision-reports.jsonl", report)
        report['decision_record']=await asyncio.to_thread(decisions.archive_tool_result,report,snapshot,'combat')
        return report
    except (ValueError, KeyError, TypeError, OSError) as exc:
        return {"error": "ANALYSIS_UNAVAILABLE", "message": str(exc), "execution_authorized": False}


@mcp.tool()
def draw_odds(total_cards: int, target_cards: int, draws: int) -> dict:
    """Exact probability of at least one target in uniform drawing without replacement.
    Requires draws <= pile size; no reshuffle, draw lock, innate cards or hidden order assumptions.
    """
    try:
        probability = at_least_one(total_cards, target_cards, draws)
        return {"probability": float(probability), "fraction": str(probability), "method": "hypergeometric_exact",
                "assumption": "uniform draw without replacement; no reshuffle", "execution_authorized": False}
    except (ValueError, TypeError) as exc:
        return {"error": "INVALID_DRAW_INPUT", "message": str(exc)}

@mcp.tool()
async def start_new_run(request_id: str, character: str = "IRONCLAD", ascension: int | None = None) -> dict:
    """Start one standard single-player run, then pause at the first room.
    Default character is Ironclad; omitted ascension uses that character's highest unlocked level.
    Accept character entity IDs or 铁甲战士. Existing saves are never overwritten.
    Use a unique setup request_id; reuse it to read the saved result without replay.
    On unresolved results query pending_request_id with get_action_status; do not use a new ID.
    """
    return await setup_run(request_id, character, ascension)

@mcp.tool()
async def act(request_id: str, run_id: str, session_epoch: str, expected_state_version: int,
              action_id: str, target_id: str | None = None) -> dict:
    """Execute ONE listed legal menu/combat/reward/map/event action chosen by Codex using fresh get_state.
    Put state.command_context_id in run_id (on older bridges use state.run_id).
    Control must be auto. After timeout query the existing ID; do not replace or replay blindly.
    Opening a card reward completes when its selection screen is ready; choose a card or
    listed alternative in a separate action. Reward choices take target_id=None.
    Main menu actions include continue_game and new_game, then standard_run, character
    selection, ascension_up/down and embark. Existing saves block new_game/embark.
    abandon_run explicitly ends the saved run using the game's normal main-menu handler.
    Use it only when the user has requested abandoning/replacing the prior run.
    choose_map_node selects one currently available node and waits for room arrival.
    Event options include their current descriptions and hover_tips. choose_event_option
    executes one choice; proceed_event returns to map. Custom layouts and sub-selections
    need backend adapters; query original request IDs. Events opening NRewardsScreen return
    awaiting_choice and expose pending_event_action. Claim/proceed rewards in child actions,
    then query the original event request; do not replay it. Only reward/selection actions
    and potion discards are available while this parent is pending.
    Standard deck selection supports toggle_selection_card, preview_card_selection,
    confirm_card_selection, cancel_selection_preview and close_card_selection when listed.
    Non-combat simple card selectors also support toggling several cards up to max_select.
    Some confirm automatically at the selection limit; others list confirm_card_selection.
    Always read a fresh snapshot after each choice; there is no preview for simple selectors.
    Relics expose visible status and counter (null when the game hides its counter).
    Deck enchant selectors use the same preview/cancel/confirm actions and expose enchantment
    identity, amount and the displayed description in card_selection.enchantment.
    Combat card plays can return awaiting_choice: keep that request ID pending, read
    pending_combat_action/card_selection, then use select_combat_card and
    confirm_combat_selection when listed. Only selection actions are legal until the
    original play finishes; query its request ID afterward. Supported combat screens:
    NPlayerHand SimpleSelect/UpgradeSelect, NSimpleCardSelectScreen and generated-card
    NChooseACardSelectionScreen. Potion use may also return awaiting_choice; select_combat_card
    or skip_combat_choice completes the child, then query the original potion request ID.
    All choices run through the game's backend handlers; no keyboard or mouse input is needed.
    Opening a selector and picking each card are separate actions; only confirmation
    applies the selected modification. Other custom selector layouts need backend adapters.
    Shop actions: open_shop, buy_shop_item, close_shop, proceed_shop. Buy only a listed
    current item; prices reflect game modifiers. Card removal opens a separate selector
    and awaits confirmation before payment.
    Rest sites support choose_rest_option (Heal/Smith) and proceed_rest. Smith opens
    a separate upgrade selector; use listed selection and confirmation actions.
    Potions support use_potion and discard_potion with instance IDs and actual slot indices.
    Use only listed targets; Self can use target_id=None, targeted enemy potions require
    an allowed creature ID. Automatic and location-targeted potions cannot be manually used.
    Discarding is a separate decision; claim a replacement reward only after the slot is free.
    Treasure rooms support open_chest, take_treasure_relic, skip_treasure_relic and
    proceed_treasure. Opening and claiming are separate decisions; hidden loot is not exposed.
    return_to_main_menu uses normal run cleanup and preserves the game's room checkpoint.
    quit_game is available only in the main menu; completed means normal exit is scheduled.
    Verify OS process termination before deploying DLLs or launching again.
    """
    request = ActionRequest(request_id=request_id, run_id=run_id, session_epoch=session_epoch,
                            expected_state_version=expected_state_version, action_id=action_id, target_id=target_id)
    return await submit_action(request)

@mcp.tool()
async def get_action_status(request_id: str) -> dict:
    """Query an existing command without executing or resubmitting it."""
    return await query_action(request_id)

@mcp.tool()
async def set_control_mode(mode: str) -> dict:
    """Set auto or paused; enabling auto executes no gameplay action.
    Pause blocks new dispatches; effects already dispatched finish normally.
    """
    return await control(mode)


@mcp.tool()
async def get_state() -> dict:
    """Read live player-visible game state. Read-only; no gameplay action is executed."""
    snapshot=await fetch_state()
    await synchronizer.observe(snapshot)
    if snapshot.get('connected'):
        await asyncio.to_thread(observe_event,snapshot)
    return snapshot


@mcp.tool()
def get_installation_info() -> dict:
    """Report local installation and available tools, without exposing credentials."""
    info = ROOT / ".runtime/local" / "installation.json"
    return {"stage": "versioned knowledge, detailed decision records and guarded game control; full mechanism coverage in progress", "capabilities": ["get_state", "act", "execute_plan", "get_action_status", "set_control_mode", "start_new_run", "analyze_combat", "analyze_decision", "evaluate_run_choices", "record_decision_choice", "observe_decision_result", "draw_odds", "search_knowledge", "knowledge_status", "query_encyclopedia", "query_behavior_log", "refresh_encyclopedia", "query_game_mechanics", "audit_knowledge_rules", "rollback_encyclopedia", "get_decision_record"], "decision_panel": "http://127.0.0.1:18767/", "installation": json.loads(info.read_text(encoding="utf-8-sig")) if info.exists() else None}


if __name__ == "__main__":
    mcp.run(transport="stdio")
