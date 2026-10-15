using AutoSlayBridge;

static void Check(bool condition, string name) { if (!condition) throw new Exception(name); Console.WriteLine("PASS " + name); }
static ActionRequest Request(string id = "one") => new(id, "run", "epoch", 42, "play_c1", "enemy");

Check(ActionCatalog.Types.Count == 42 && ActionCatalog.Require("play_card").DefinitionId == "ACTION.play_card",
    "global catalog includes all bridge command types and stable references");
Check(!ActionCatalog.Require("quit_game").AgentAllowed && ActionCatalog.Require("use_potion").AgentAllowed,
    "agent command policy comes from shared definitions");
ActionCatalog.ValidateBinding("play_card", ["enemy"], false);
ActionCatalog.ValidateBinding("end_turn", [], true);
foreach (var invalid in new[] { "unknown", "end_turn", "play_card" })
{
    var rejected = false;
    try { ActionCatalog.ValidateBinding(invalid, invalid == "play_card" ? [] : ["enemy"], false); }
    catch (InvalidOperationException) { rejected = true; }
    Check(rejected, "catalog rejects unknown or invalid targeting: " + invalid);
}
Console.WriteLine($"ACTION_CATALOG {ActionCatalog.Types.Count} {ActionCatalog.Sha256}");

var runtimeJson = System.Text.Json.JsonSerializer.Serialize(RuntimeFields.Read(new RuntimeProbe()));
using (var runtimeDocument = System.Text.Json.JsonDocument.Parse(runtimeJson))
{
    var runtimeValues = runtimeDocument.RootElement.GetProperty("fields");
    Check(runtimeValues.GetProperty("Charged").GetBoolean(), "runtime scalar backing fields retain boolean values");
    Check(!runtimeValues.TryGetProperty("DangerousGetter", out _) && !runtimeValues.TryGetProperty("Rng", out _),
        "runtime export never invokes property getters or serializes RNG objects");
}

var paused = new CommandLedger();
Check(paused.Submit(Request()).Error == "CONTROL_PAUSED" && !paused.TryDequeue(out _), "paused requests never enter queue");

var ledger = new CommandLedger();
ledger.SetPaused(false);
var request = Request();
Parallel.For(0, 32, _ => ledger.Submit(request));
Check(ledger.TryDequeue(out var first) && first == request && !ledger.TryDequeue(out _), "concurrent retries queue exactly one command");
Check(ledger.Submit(Request("other")).Error == "BUSY", "different requests blocked while command pending");
Check(ledger.Submit(request with { TargetId = "different" }).Error == "REQUEST_CONFLICT", "reused ID with changed parameters rejected");
ledger.SetPaused(true);
Check(!ledger.TryBeginDispatch("one"), "pause before dispatch prevents execution");
ledger.SetPaused(false);
Check(ledger.TryBeginDispatch("one") && ledger.Get("one").Dispatched == true, "dispatch records execution uncertainty before game call");
ledger.Update(new("one", "completed", true), release: true);
Check(ledger.Submit(request).Status == "completed" && !ledger.TryDequeue(out _), "completed retries return receipt without replay");
Check(ledger.Submit(Request("third")).Status == "received", "completion releases slot for new command");
Check(ledger.Get("missing").Status == "unknown" && ledger.Get("missing").Dispatched == null, "unknown result never claims non-execution");

Check(RewardCompletion.Evaluate(new("claim_reward", SelectionReady: true)) == RewardOutcome.SelectionOpened,
    "opening card reward releases command while reward task awaits a separate choice");
Check(RewardCompletion.Evaluate(new("choose_reward_card", HasChoiceToken: true, OldChoiceCompleted: true, Idle: true)) == RewardOutcome.Waiting,
    "choice callback alone does not finish before card acquisition settles");
Check(RewardCompletion.Evaluate(new("choose_reward_card", HasChoiceToken: true, SelectionReady: true, ChoiceTokenChanged: true)) == RewardOutcome.NextSelection,
    "multi-pick or reroll completes one choice only after a new choice token appears");
Check(RewardCompletion.Evaluate(new("reward_alternative", TaskCompleted: true, Idle: false)) == RewardOutcome.Waiting,
    "completed reward task waits for game queue to settle");
Check(RewardCompletion.Evaluate(new("reward_alternative", TaskCompleted: true, Idle: true)) == RewardOutcome.Completed,
    "skip finishes when reward task and game queue settle");
Check(RewardCompletion.Evaluate(new("proceed_rewards", ScreenChanged: true, Idle: true, ModalOpen: true)) == RewardOutcome.NeedsUserInput,
    "proceed encountering a tutorial modal requires user input instead of false completion");
Check(RewardCompletion.Evaluate(new("proceed_rewards", TimedOut: true)) == RewardOutcome.NeedsUserInput,
    "proceed timeout is reported rather than waiting silently forever");
Check(RewardCompletion.Evaluate(new("claim_reward", TaskFailed: true, SelectionReady: true)) == RewardOutcome.Failed,
    "task failure takes precedence over a visible selection");
Check(RewardCompletion.Evaluate(new("choose_reward_card", HasTask: false, HasChoiceToken: true,
    OldChoiceCompleted: true, ScreenChanged: true, Idle: true)) == RewardOutcome.Completed,
    "manually opened selection can finish after its normal UI flow closes");

Check(!MenuRules.CanEmbark(true, true, true, true), "existing save blocks embark even if character screen is open");
Check(!MenuRules.CanEmbark(false, false, true, true), "multiplayer lobby cannot be started by single-player adapter");
Check(!MenuRules.CanEmbark(false, true, false, true), "transition or modal blocks new run dispatch");
Check(!MenuRules.CanEmbark(false, true, true, false), "locked or missing character blocks embark");
Check(MenuRules.CanEmbark(false, true, true, true), "unlocked single-player selection without save permits embark");

var mapReady = new MapReadiness(true, true, true, false, false, false, false, false, false, true, true, true, true);
Check(MapRules.CanSelect(mapReady), "stable legal map node permits travel");
Check(!MapRules.CanSelect(mapReady with { TravelablePoint = false }), "non-adjacent map node cannot be selected");
Check(!MapRules.CanSelect(mapReady with { DebugTravel = true }), "debug map traversal is excluded");
Check(!MapRules.CanSelect(mapReady with { Drawing = true }), "map drawing blocks travel dispatch");
Check(!MapRules.CanSelect(mapReady with { TutorialReady = false }), "initial map tutorial blocks bypass");
var arrived = new MapProgress(true, true, true, true, false, false, false, true, true, false, false);
Check(MapRules.Evaluate(arrived with { RoomChanged = false }) == MapOutcome.Waiting, "coordinate update alone does not finish map travel");
Check(MapRules.Evaluate(arrived with { Transition = true }) == MapOutcome.Waiting, "map travel waits for room fade to finish");
Check(MapRules.Evaluate(arrived) == MapOutcome.Completed, "map travel completes at settled target room");
Check(MapRules.Evaluate(arrived with { ModalOpen = true }) == MapOutcome.NeedsUserInput, "map tutorial retains pending action for manual resolution");
Check(MapRules.Evaluate(arrived with { SameRun = false }) == MapOutcome.Failed, "run replacement fails pending map travel");

var eventDone = new EventProgress(true, false, true, true, true, true, false, false, false);
Check(EventRules.Evaluate(eventDone) == EventOutcome.Completed, "event choice completes after task and new options settle");
Check(EventRules.Evaluate(eventDone with { TasksSettled = false }) == EventOutcome.Waiting, "new event buttons cannot outrun pending option effects");
Check(EventRules.Evaluate(eventDone with { ChoiceChanged = false }) == EventOutcome.Waiting, "unchanged original event choice cannot falsely complete");
Check(EventRules.Evaluate(eventDone with { NextReady = false }) == EventOutcome.Waiting, "event transition waits for next decision");
Check(EventRules.Evaluate(eventDone with { UnsupportedScreen = true }) == EventOutcome.NeedsUserInput, "event card sub-selection retains action slot");
Check(EventRules.Evaluate(eventDone with { Modal = true }) == EventOutcome.NeedsUserInput, "event modal blocks false completion");
Check(EventRules.Evaluate(eventDone with { TasksSettled = false, TimedOut = true }) == EventOutcome.NeedsUserInput, "event timeout reports unresolved effects");
Check(EventRules.Evaluate(eventDone with { TaskFailed = true }) == EventOutcome.Failed, "event task failure overrides displayed next page");

var selection = new SelectionProgress(true, false, false, true, true, false, false, true, false, false);
Check(SelectionRules.Evaluate(selection) == SelectionOutcome.Completed, "selecting a card releases command at preview without waiting for final effects");
Check(SelectionRules.Evaluate(selection with { LocalChanged = false }) == SelectionOutcome.Waiting, "unchanged selector cannot falsely complete");
var confirmed = selection with { ScreenChanged = true, LocalChanged = false, EffectsSettled = true, NextReady = true };
Check(SelectionRules.Evaluate(confirmed) == SelectionOutcome.Completed, "confirmed card choice waits for settled effects and next decision");
Check(SelectionRules.Evaluate(confirmed with { EffectsSettled = false }) == SelectionOutcome.Waiting, "closing card grid alone cannot finish deletion or upgrade");
Check(SelectionRules.Evaluate(confirmed with { NextReady = false }) == SelectionOutcome.Waiting, "card effects wait for stable next page");
Check(SelectionRules.Evaluate(selection with { Modal = true }) == SelectionOutcome.NeedsUserInput, "selector modal retains unresolved action");
Check(SelectionRules.Evaluate(selection with { Failed = true }) == SelectionOutcome.Failed, "selector effect failure is reported");

var bought = new ShopProgress(true, false, true, true, false, true, true, false, false);
Check(ShopRules.Evaluate(bought) == ShopOutcome.Completed, "shop purchase requires success and settled inventory");
Check(ShopRules.Evaluate(bought with { Purchased = false }) == ShopOutcome.Failed, "completed shop routine without successful purchase cannot claim success");
Check(ShopRules.Evaluate(bought with { TaskDone = false }) == ShopOutcome.Waiting, "shop success callback alone does not outrun remaining hooks");
Check(ShopRules.Evaluate(bought with { TaskDone = false, Purchased = false, SelectionReady = true }) == ShopOutcome.SelectionOpened, "shop removal opens selector as separate decision before payment");
Check(ShopRules.Evaluate(bought with { NextReady = false }) == ShopOutcome.Waiting, "shop waits for inventory animation to finish");
Check(ShopRules.Evaluate(bought with { Modal = true }) == ShopOutcome.NeedsUserInput, "merchant tutorial blocks false completion");

Check(PotionRules.CanUse("CombatOnly", true, true, true, false, true, true, true), "combat potion is usable during a stable player turn");
Check(!PotionRules.CanUse("CombatOnly", false, true, true, false, true, true, true), "combat potion is blocked outside combat");
Check(PotionRules.CanUse("AnyTime", false, true, true, false, true, true, true), "anytime potion may be used outside combat");
Check(!PotionRules.CanUse("Automatic", true, true, true, false, true, true, true), "automatic potion cannot be manually consumed");
Check(!PotionRules.CanUse("None", true, true, true, false, true, true, true), "unknown usage is blocked");
Check(!PotionRules.CanUse("CombatOnly", true, true, true, true, true, true, true), "queued potion cannot be used twice");
Check(!PotionRules.CanUse("CombatOnly", true, true, true, false, false, true, true), "game restriction blocks potion use");
Check(!PotionRules.CanUse("CombatOnly", true, true, true, false, true, false, true), "custom potion usability check is respected");
Check(!PotionRules.CanUse("CombatOnly", true, true, true, false, true, true, false), "unsupported location target is blocked");
Check(!PotionRules.CanDiscard(true, true, true, true), "queued potion cannot be discarded");
Check(!PotionRules.CanDiscard(true, true, false, false), "game restriction blocks potion discard");
var potionDone = new PotionProgress(true, false, false, true, true, true, true, false, false);
Check(PotionRules.Evaluate(potionDone) == PotionOutcome.Completed, "potion completion requires finished action and settled next decision");
Check(PotionRules.Evaluate(potionDone with { Finished = false }) == PotionOutcome.Waiting, "belt removal cannot outrun potion effects");
Check(PotionRules.Evaluate(potionDone with { Removed = false }) == PotionOutcome.Waiting, "action completion without consuming potion cannot claim success");
Check(PotionRules.Evaluate(potionDone with { Idle = false }) == PotionOutcome.Waiting, "potion hooks keep command pending");
Check(PotionRules.Evaluate(potionDone with { NextReady = false }) == PotionOutcome.Waiting, "potion waits for next stable screen");
Check(PotionRules.Evaluate(potionDone with { Choice = true }) == PotionOutcome.NeedsUserInput, "potion choice retains command for manual handling");
Check(PotionRules.Evaluate(potionDone with { Failed = true }) == PotionOutcome.Failed, "potion cancellation or exception overrides completion");
Check(PotionRules.Evaluate(potionDone with { Finished = false, TimedOut = true }) == PotionOutcome.NeedsUserInput, "unresolved potion action is not replayed after timeout");

var choice = new CombatChoiceProgress(true, false, false, false, false, true, true, true, false, false);
Check(CombatChoiceRules.Evaluate(choice) == CombatChoiceOutcome.LocalCompleted, "selected combat card completes child without falsely finishing parent");
Check(CombatChoiceRules.Evaluate(choice with { Changed = false }) == CombatChoiceOutcome.Waiting, "unchanged combat choice cannot finish child");
Check(CombatChoiceRules.Evaluate(choice with { SameChoice = false }) == CombatChoiceOutcome.NextChoice, "new choice token permits consecutive child decisions while parent waits");
Check(CombatChoiceRules.Evaluate(choice with { ChoiceReady = false, ParentFinished = true }) == CombatChoiceOutcome.Waiting, "finished parent waits for executor and hooks to settle");
Check(CombatChoiceRules.Evaluate(choice with { ChoiceReady = false, ParentFinished = true, Idle = true, NextReady = true }) == CombatChoiceOutcome.Completed, "final combat choice completes after parent and next decision settle");
Check(CombatChoiceRules.Evaluate(choice with { Failed = true }) == CombatChoiceOutcome.Failed, "parent exception overrides visible combat selection");
Check(CombatChoiceRules.Evaluate(choice with { SameRun = false }) == CombatChoiceOutcome.Failed, "run replacement invalidates pending combat choice");
Check(CombatChoiceRules.Evaluate(choice with { Modal = true }) == CombatChoiceOutcome.NeedsUserInput, "modal cannot masquerade as completed combat choice");
var choiceLedger = new CommandLedger();
choiceLedger.SetPaused(false);
var parentRequest = Request("parent");
choiceLedger.Submit(parentRequest);
choiceLedger.TryDequeue(out _);
choiceLedger.TryBeginDispatch("parent");
choiceLedger.Update(new("parent", "awaiting_choice", true), release: true);
Check(choiceLedger.Submit(parentRequest).Status == "awaiting_choice" && !choiceLedger.TryDequeue(out _), "retry while parent awaits choice does not replay card");
Check(choiceLedger.Submit(Request("child")).Status == "received", "yielded parent permits child command");
choiceLedger.TryDequeue(out _);
choiceLedger.TryBeginDispatch("child");
choiceLedger.Update(new("parent", "completed", true));
Check(choiceLedger.Busy && choiceLedger.Submit(Request("unrelated")).Error == "BUSY", "completing parent does not unlock active child command");
choiceLedger.Update(new("child", "completed", true), release: true);
Check(choiceLedger.Submit(parentRequest).Status == "completed" && !choiceLedger.TryDequeue(out _), "settled parent retry returns result without replay");

var tooltipCard = new CardPresentation("CARD.SETUP_STRIKE", "Setup Strike", 0, "Attack", "AnyEnemy", 1, false, "Deal 7. Gain 3 Strength this turn.");
var tooltipOptions = new System.Text.Json.JsonSerializerOptions { PropertyNamingPolicy = System.Text.Json.JsonNamingPolicy.SnakeCaseLower };
var tooltipJson = System.Text.Json.JsonSerializer.Serialize(tooltipCard, tooltipOptions);
Check(tooltipJson == System.Text.Json.JsonSerializer.Serialize(tooltipCard with { }, tooltipOptions), "recreated tooltip models with identical content do not change snapshot payload");
Check(!tooltipJson.Contains("instance_id"), "tooltip card cannot masquerade as an actionable card instance");
Check(tooltipJson != System.Text.Json.JsonSerializer.Serialize(tooltipCard with { UpgradeLevel = 1, Description = "Deal 10. Gain 3 Strength this turn." }, tooltipOptions), "real tooltip content changes still invalidate snapshot version");

var recovery = new RecoveryContext(true, true, true, false, false, false, true, false, true);
Check(RecoveryRules.CanCloseMap(recovery), "combat map overlay can close normally");
Check(!RecoveryRules.CanCloseMap(recovery with { TravelEnabled = true }), "travel map is not mistaken for an overlay");
Check(!RecoveryRules.CanCloseMap(recovery with { Traveling = true }), "travel in progress blocks close");
Check(!RecoveryRules.CanCloseMap(recovery with { Modal = true }), "modal recovery must not bypass a choice");
Check(!RecoveryRules.CanCloseMap(recovery with { QueueIdle = false }), "busy gameplay cannot be interrupted by close");

sealed class RuntimeProbe
{
    public bool Charged { get; } = true;
    public object Rng = new();
    public int DangerousGetter => throw new InvalidOperationException("must not invoke");
}
