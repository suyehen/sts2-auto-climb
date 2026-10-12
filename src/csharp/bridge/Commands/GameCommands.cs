using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Actions;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Runs;
using System.Text.Json;

namespace AutoSlayBridge;

public static partial class Bridge
{
    private static readonly CommandLedger Ledger = new();
    private sealed record LegalAction(string ActionId, string Type, string? CardInstanceId, string[] AllowedTargetIds, bool AllowsNoTarget);
    private static readonly Dictionary<string, (CardModel? Card, Creature?[] Targets)> ActionBindings = new();
    private static GameAction? executing;
    private static ActionRequest? activeRequest;
    private static int submittedTurn;
    private static long submittedTick;
    private static long tick;

    private static bool CanDecide(RunState? run, CombatState? combat, Player? player)
    {
        var manager = CombatManager.Instance;
        var hand = NPlayerHand.Instance;
        return run != null && combat != null && player?.PlayerCombatState?.Phase == PlayerTurnPhase.Play
            && !run.IsGameOver && !RunManager.Instance.IsPaused && !manager.IsPaused
            && !manager.IsStarting && !manager.IsOverOrEnding && !manager.PlayerActionsDisabled
            && !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty
            && !manager.IsPlayerReadyToEndTurn(player)
            && hand != null && hand.CurrentMode == NPlayerHand.Mode.Play && !hand.InCardPlay
            && NCombatRoom.Instance != null && NCombatRoom.Instance.IsVisibleInTree()
            && MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext.ActiveScreenContext.Instance.IsCurrent(NCombatRoom.Instance)
            && NModalContainer.Instance?.OpenModal == null && (NOverlayStack.Instance?.ScreenCount ?? 0) == 0;
    }

    private static LegalAction[] BuildActions(CombatState? combat, Player? player, bool decisionReady)
    {
        ActionBindings.Clear();
        if (!decisionReady || combat == null || player?.PlayerCombatState == null) return [];
        var actions = new List<LegalAction>();
        foreach (var card in player.PlayerCombatState.Hand.Cards)
        {
            if (!card.CanPlay()) continue;
            var targets = combat.Creatures.Where(c => card.CanPlayTargeting(c)).Cast<Creature?>().ToList();
            if (card.CanPlayTargeting(null)) targets.Add(null);
            if (targets.Count == 0) continue;
            var actionId = "play_" + Id(card);
            ActionBindings[actionId] = (card, targets.ToArray());
            actions.Add(new(actionId, "play_card", Id(card), targets.Where(c => c != null).Select(c => Id(c!)).ToArray(), targets.Contains(null)));
        }
        ActionBindings["end_turn"] = (null, [null]);
        actions.Add(new("end_turn", "end_turn", null, [], true));
        return actions.ToArray();
    }

    private static void Tick()
    {
        tick++;
        CaptureEncyclopedia();
        Capture(); // Refresh against manual input before validating queued commands.
        try { ProcessCommands(); }
        catch (Exception e)
        {
            if (activeRequest != null)
            {
                // UI dispatch may have changed inventory before an exception. Keep its slot locked.
                var dispatched = executing != null || activeTreasure != null || activeLifecycle != null || activePotion != null || activeUi != null || activeMenu != null || activeMap != null || activeEvent != null || activeSelection != null || activeShop != null || activeRest != null;
                Ledger.Update(new(activeRequest.RequestId, "failed", dispatched ? true : null, "EXECUTION_ERROR", e.Message, snapshot), release: !dispatched);
                if (!dispatched) activeRequest = null;
            }
        }
        Capture();
    }

    private static void ProcessCommands()
    {
        if (activeRequest != null)
        {
            if (activeCombatChoice != null) ObserveCombatChoiceCompletion();
            else if (activeLifecycle != null) ObserveLifecycleCompletion();
            else if (activeTreasure != null) ObserveTreasureCompletion();
            else if (activePotion != null) ObservePotionCompletion();
            else if (activeRest != null) ObserveRestCompletion();
            else if (activeShop != null) ObserveShopCompletion();
            else if (activeSelection != null) ObserveSelectionCompletion();
            else if (activeEvent != null) ObserveEventCompletion();
            else if (activeMap != null) ObserveMapCompletion();
            else if (activeMenu != null) ObserveMenuCompletion();
            else if (activeUi != null) ObserveUiCompletion();
            else ObserveCompletion();
            return;
        }
        ObserveCombatParent();
        ObserveEventParent();
        if (!Ledger.TryDequeue(out var request) || request == null) return;
        activeRequest = request;
        using var state = JsonDocument.Parse(snapshot);
        var root = state.RootElement;
        if (root.TryGetProperty("error", out _)) { Reject("STATE_UNAVAILABLE"); return; }
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var combat = CombatManager.Instance.IsInProgress ? CombatManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        string? error = Ledger.IsExpired(request.RequestId) ? "REQUEST_EXPIRED"
            : request.SessionEpoch != Epoch ? "SESSION_MISMATCH"
            : request.RunId != root.GetProperty("state").GetProperty("command_context_id").GetString() ? "RUN_MISMATCH"
            : request.ExpectedStateVersion != version ? "STALE_STATE"
            : Ledger.Paused ? "CONTROL_PAUSED"
            : !ActionBindings.ContainsKey(request.ActionId) && !UiBindings.ContainsKey(request.ActionId) && !CombatChoiceBindings.ContainsKey(request.ActionId)
                && !MenuBindings.ContainsKey(request.ActionId) && !MapBindings.ContainsKey(request.ActionId) && !EventBindings.ContainsKey(request.ActionId) && !SelectionBindings.ContainsKey(request.ActionId) && !ShopBindings.ContainsKey(request.ActionId) && !RestBindings.ContainsKey(request.ActionId) && !PotionBindings.ContainsKey(request.ActionId) && !LifecycleBindings.ContainsKey(request.ActionId) && !TreasureBindings.ContainsKey(request.ActionId) ? "INVALID_ACTION" : null;
        if (error != null) { Reject(error); return; }
        if (suspendedCombatRequest != null && !CombatChoiceBindings.ContainsKey(request.ActionId))
            { Reject("COMBAT_CHOICE_PENDING"); return; }
        if (suspendedEventRequest != null && !EventRewardChildAction(request.ActionId))
            { Reject("EVENT_REWARD_PENDING"); return; }
        if (CombatChoiceBindings.TryGetValue(request.ActionId, out var combatChoice))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (suspendedCombatRequest == null || !currentCombatChoiceReady
                || combatChoice.ChoiceId != currentCombatChoiceId || combatChoice.Before != currentCombatChoiceFingerprint)
                { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            activeCombatChoice = combatChoice;
            combatChoice.Dispatch();
            return;
        }
        if (LifecycleBindings.TryGetValue(request.ActionId, out var lifecycle))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (!LifecycleReady(lifecycle, run, combat, player))
                { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            DispatchLifecycle(lifecycle); return;
        }
        if (TreasureBindings.TryGetValue(request.ActionId, out var treasureBinding))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (!TreasureIdle(run, player, treasureBinding.Room) || !TreasureButtonReady(treasureBinding.Button))
                { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            DispatchTreasure(treasureBinding); return;
        }
        if (PotionBindings.TryGetValue(request.ActionId, out var potionBinding))
        {
            var potionPlayer = player;
            if (potionPlayer == null || !PotionIdle(run, combat, potionPlayer) || !PotionHolderReady(potionBinding.Holder, potionBinding.Potion)
                || potionPlayer.GetPotionAtSlotIndex(potionBinding.Slot) != potionBinding.Potion || potionBinding.Potion.IsQueued
                || !potionPlayer.CanUseOrRemovePotions) { Reject("NOT_READY"); return; }
            var potionTarget = request.TargetId == null ? null : potionBinding.Targets.FirstOrDefault(t => t != null && Id(t) == request.TargetId);
            if (!potionBinding.Targets.Contains(potionTarget) || request.TargetId != null && potionTarget == null)
                { Reject("INVALID_TARGET"); return; }
            if (!potionBinding.Discard && (!PotionRules.CanUse(potionBinding.Potion.Usage.ToString(), combat != null,
                true, true, false, true, potionBinding.Potion.PassesCustomUsabilityCheck, SupportedPotionTarget(potionBinding.Potion))
                || !potionBinding.Potion.IsValidTarget(potionTarget ?? (potionBinding.Potion.TargetType == MegaCrit.Sts2.Core.Entities.Cards.TargetType.Self ? potionPlayer.Creature : null))))
                { Reject("INVALID_ACTION"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            DispatchPotion(potionBinding, potionTarget); return;
        }
        if (RestBindings.TryGetValue(request.ActionId, out var restBinding))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (!RestIdle(run, player, restBinding.Room)
                || (restBinding.Button == null ? !SelectionButtonReady(restBinding.Room.ProceedButton) : !RestButtonReady(restBinding.Button)))
                { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            DispatchRest(restBinding); return;
        }
        if (ShopBindings.TryGetValue(request.ActionId, out var shopBinding))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (!ShopIdle(run, player) || !IsActive(shopBinding.Screen)
                || shopBinding.Screen is MegaCrit.Sts2.Core.Nodes.Screens.Shops.NMerchantInventory inventory && !InventoryReady(inventory)
                || shopBinding.Entry != null && (!shopBinding.Entry.IsStocked || !shopBinding.Entry.EnoughGold))
                { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            DispatchShop(shopBinding);
            return;
        }
        if (SelectionBindings.TryGetValue(request.ActionId, out var selectionBinding))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (!SelectionIdle(run, player, selectionBinding.Screen)
                || SelectionFingerprint(selectionBinding.Screen) != selectionBinding.Before) { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            activeSelection = selectionBinding;
            selectionBinding.Dispatch();
            return;
        }
        if (EventBindings.TryGetValue(request.ActionId, out var eventBinding))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (!EventIdle(run, player, eventBinding.Room) || !EventButtonReady(eventBinding.Button)
                || !ReferenceEquals(eventBinding.Button.Option, eventBinding.Option)) { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            DispatchEvent(eventBinding);
            return;
        }
        if (MapBindings.TryGetValue(request.ActionId, out var mapBinding))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (run == null || run.CurrentActIndex != mapBinding.ActIndex
                || Convert.ToInt64(RunManager.Instance.MapSelectionSynchronizer.MapGenerationCount) != mapBinding.Generation
                || !CanSelectMapPoint(run, mapBinding.Screen, mapBinding.Node)) { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            activeMap = mapBinding;
            mapBinding.Screen.OnMapPointSelectedLocally(mapBinding.Node);
            return;
        }
        if (MenuBindings.TryGetValue(request.ActionId, out var menuBinding))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (!MenuIdle() || !IsActive(menuBinding.Screen)) { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            activeMenu = menuBinding;
            menuTask = menuBinding.Dispatch();
            return;
        }
        if (UiBindings.TryGetValue(request.ActionId, out var uiBinding))
        {
            if (request.TargetId != null) { Reject("INVALID_TARGET"); return; }
            if (!UiIdle(run, player) || !IsActive(uiBinding.Screen)) { Reject("NOT_READY"); return; }
            submittedTick = tick;
            if (!Ledger.TryBeginDispatch(request.RequestId)) { Reject("CONTROL_PAUSED"); return; }
            activeUi = uiBinding;
            uiTask = uiBinding.Dispatch();
            return;
        }
        if (!CanDecide(run, combat, player)) { Reject("NOT_READY"); return; }
        var combatPlayer = player!;
        var playerState = combatPlayer.PlayerCombatState!;
        var binding = ActionBindings[request.ActionId];
        var target = binding.Targets.FirstOrDefault(c => request.TargetId == null ? c == null : c != null && Id(c) == request.TargetId);
        if (!binding.Targets.Any(c => request.TargetId == null ? c == null : c != null && Id(c) == request.TargetId)) { Reject("INVALID_TARGET"); return; }
        if (binding.Card != null)
        {
            if (!playerState.Hand.Cards.Contains(binding.Card) || !binding.Card.CanPlayTargeting(target)) { Reject("INVALID_ACTION"); return; }
            executing = new PlayCardAction(binding.Card, target);
        }
        else executing = new EndPlayerTurnAction(combatPlayer, playerState.TurnNumber);
        submittedTurn = playerState.TurnNumber;
        submittedTick = tick;
        // Mark dispatch before calling game code: exceptions can follow side effects.
        if (!Ledger.TryBeginDispatch(request.RequestId)) { executing = null; Reject("CONTROL_PAUSED"); return; }
        if (binding.Card != null) TaskHelper.RunSafely(binding.Card.OnEnqueuePlayVfx(target));
        RunManager.Instance.ActionQueueSynchronizer.RequestEnqueue(executing);
    }

    private static void Reject(string error)
    {
        var requestId = activeRequest!.RequestId;
        Ledger.Update(new(requestId, "rejected", false, error), release: true);
        activeRequest = null;
        Capture();
        Ledger.Update(new(requestId, "rejected", false, error, SnapshotJson: snapshot));
    }

    private static void ObserveCompletion()
    {
        if (executing == null || activeRequest == null || tick <= submittedTick) return;
        var action = executing;
        var manager = CombatManager.Instance;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var combat = manager.IsInProgress ? manager.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        if (action.State == GameActionState.GatheringPlayerChoice)
        {
            if (YieldCombatChoice()) return;
            Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_CHOICE", "Resolve this selection manually, then query status.", snapshot));
            return;
        }
        if (action.Exception != null || action.State == GameActionState.Canceled)
        {
            Ledger.Update(new(activeRequest.RequestId, "failed", true, "ACTION_FAILED", action.Exception?.Message, snapshot), release: true);
            executing = null; activeRequest = null; return;
        }
        if (action.State != GameActionState.Finished) return;
        var idle = !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty;
        var stopped = run == null || run.IsGameOver || combat == null;
        var nextTurn = activeRequest.ActionId != "end_turn" || player?.PlayerCombatState?.TurnNumber > submittedTurn;
        if (!idle || (!stopped && !(nextTurn && CanDecide(run, combat, player)))) return;
        Ledger.Update(new(activeRequest.RequestId, "completed", true, SnapshotJson: snapshot), release: true);
        var completedId = activeRequest.RequestId;
        executing = null; activeRequest = null;
        Capture();
        Ledger.Update(new(completedId, "completed", true, SnapshotJson: snapshot));
    }
}
