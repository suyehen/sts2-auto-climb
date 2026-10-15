using Godot;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Actions;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Runs;
using System.Reflection;

namespace AutoSlayBridge;
public static partial class Bridge
{
    private sealed record CombatChoiceBinding(string Kind, string ChoiceId, string Before, Action Dispatch, CardModel? SourceCard = null);
    private static readonly Dictionary<string, CombatChoiceBinding> CombatChoiceBindings = new();
    private static CombatChoiceBinding? activeCombatChoice;
    private static ActionRequest? suspendedCombatRequest;
    private static PotionBinding? suspendedCombatPotion;
    private static long combatChoiceYieldTick;
    private static string? currentCombatChoiceId;
    private static string? currentCombatChoiceFingerprint;
    private static bool currentCombatChoiceReady;

    private static bool CombatChoiceContext(RunState? run, Player? player) => run != null && player != null
        && !run.IsGameOver && CombatManager.Instance.IsInProgress && !CombatManager.Instance.IsOverOrEnding
        && !RunManager.Instance.IsPaused && !CombatManager.Instance.IsPaused
        && NModalContainer.Instance?.OpenModal == null && NGame.Instance?.Transition?.InTransition != true
        && executing?.State == GameActionState.GatheringPlayerChoice
        && (suspendedCombatRequest != null || activeRequest?.ActionId.StartsWith("play_") == true || activePotion is { Discard: false });

    private static SelectionView? ReadCombatChoice(RunState? run, Player? player, bool bind)
    {
        CombatChoiceBindings.Clear();
        currentCombatChoiceReady = false;
        currentCombatChoiceId = currentCombatChoiceFingerprint = null;
        if (!CombatChoiceContext(run, player)) return null;
        var screen = ActiveScreenContext.Instance.GetCurrentScreen();
        if (screen is NChooseACardSelectionScreen choose && choose.IsNodeReady() && IsActive(choose))
            return ReadGeneratedCombatChoice(choose, bind);
        var hand = NPlayerHand.Instance;
        CardSelectorPrefs prefs;
        IReadOnlyCollection<CardModel> selected;
        IReadOnlyList<CardModel> candidates;
        string choiceId;
        string screenType;
        bool preview;
        Action<CardModel> select;
        Func<CardModel, bool> available;
        MegaCrit.Sts2.Core.Nodes.GodotExtensions.NButton confirm;
        if (screen is NCardGridSelectionScreen gridScreen
            && gridScreen is NSimpleCardSelectScreen or NCombatPileCardSelectScreen
            && gridScreen.IsNodeReady() && IsActive(gridScreen)
            && gridScreen.MouseFilter == Control.MouseFilterEnum.Stop
            && !ReadUiField<TaskCompletionSource<IEnumerable<CardModel>>>(gridScreen, "_completionSource").Task.IsCompleted)
        {
            prefs = ReadUiField<CardSelectorPrefs>(gridScreen, "_prefs");
            selected = ReadUiField<HashSet<CardModel>>(gridScreen, "_selectedCards");
            choiceId = Id(ReadUiField<TaskCompletionSource<IEnumerable<CardModel>>>(gridScreen, "_completionSource"));
            screenType = gridScreen.GetType().Name;
            preview = false;
            var grid = ReadUiField<NCardGrid>(gridScreen, "_grid");
            // Combat-pile screens populate the grid directly; their inherited _cards stays empty.
            candidates = gridScreen is NCombatPileCardSelectScreen
                ? grid.CurrentlyDisplayedCards.ToArray()
                : ReadUiField<IReadOnlyList<CardModel>>(gridScreen, "_cards");
            available = card => grid.GetCardHolder(card) is { } holder && holder.IsNodeReady()
                && !holder.IsQueuedForDeletion() && ReadUiField<bool>(holder, "_isClickable")
                && (selected.Contains(card) || selected.Count < prefs.MaxSelect);
            select = card => grid.EmitSignal(NCardGrid.SignalName.HolderPressed, grid.GetCardHolder(card)!);
            confirm = ReadUiField<MegaCrit.Sts2.Core.Nodes.GodotExtensions.NButton>(gridScreen, "_confirmButton");
        }
        else if (screen is NCombatRoom && hand != null && hand.IsNodeReady() && hand.IsVisibleInTree()
            && hand.CurrentMode is NPlayerHand.Mode.SimpleSelect or NPlayerHand.Mode.UpgradeSelect
            && !hand.PeekButton.IsPeeking && (NOverlayStack.Instance?.ScreenCount ?? 0) == 0
            && !ReadUiField<TaskCompletionSource<IEnumerable<CardModel>>>(hand, "_selectionCompletionSource").Task.IsCompleted)
        {
            prefs = ReadUiField<CardSelectorPrefs>(hand, "_prefs");
            selected = ReadUiField<List<CardModel>>(hand, "_selectedCards");
            var filter = ReadUiField<Func<CardModel, bool>?>(hand, "_currentSelectionFilter");
            candidates = player!.PlayerCombatState!.Hand.Cards.Where(c => filter?.Invoke(c) ?? true).ToArray();
            choiceId = Id(ReadUiField<TaskCompletionSource<IEnumerable<CardModel>>>(hand, "_selectionCompletionSource"));
            screenType = "NPlayerHand." + hand.CurrentMode;
            preview = ReadUiField<Control>(hand, "_upgradePreviewContainer").Visible;
            available = card => !selected.Contains(card) && hand.ActiveHolders.Any(h => h.CardNode?.Model == card
                && h.IsVisibleInTree() && h.IsNodeReady() && !h.IsQueuedForDeletion());
            // Use the same handler as an actual hand-holder press, including upgrade preview and replacement.
            var method = typeof(NPlayerHand).GetMethod("OnHolderPressed", BindingFlags.Instance | BindingFlags.NonPublic)
                ?? throw new MissingMethodException("NPlayerHand.OnHolderPressed");
            select = card => method.Invoke(hand, [hand.ActiveHolders.First(h => h.CardNode?.Model == card)]);
            confirm = ReadUiField<MegaCrit.Sts2.Core.Nodes.GodotExtensions.NButton>(hand, "_selectModeConfirmButton");
        }
        else return null;
        var before = preview + ":" + string.Join(",", selected.Select(Id).Order());
        var cards = new List<object>();
        var controls = new List<object>();
        foreach (var card in candidates)
        {
            var enabled = available(card);
            var actionId = "combat_select_" + choiceId + "_" + Id(card);
            cards.Add(new { card = Card(card), selected = selected.Contains(card), available = enabled,
                action_id = enabled ? actionId : null });
            if (enabled && bind) CombatChoiceBindings[actionId] = new("select_combat_card", choiceId, before, () => select(card), card);
            currentCombatChoiceReady |= enabled;
        }
        var minSelect = screen is NCombatPileCardSelectScreen ? Math.Min(prefs.MinSelect, candidates.Count) : prefs.MinSelect;
        var canConfirm = SelectionButtonReady(confirm) && selected.Count >= minSelect && selected.Count <= prefs.MaxSelect;
        var confirmId = "confirm_combat_selection_" + choiceId;
        controls.Add(new { type = "confirm_combat_selection", available = canConfirm, action_id = canConfirm ? confirmId : null });
        if (canConfirm && bind) CombatChoiceBindings[confirmId] = new("confirm_combat_selection", choiceId, before, confirm.ForceClick);
        currentCombatChoiceReady |= canConfirm;
        currentCombatChoiceId = choiceId;
        currentCombatChoiceFingerprint = before;
        return new(choiceId, screenType, prefs.Prompt.GetFormattedText(), minSelect, prefs.MaxSelect,
            prefs.Cancelable, preview, currentCombatChoiceReady, cards.ToArray(), controls.ToArray());
    }

    private static LegalAction[] CombatChoiceActions() => CombatChoiceBindings.Select(p =>
        new LegalAction(p.Key, p.Value.Kind, null, [], true,
            new(ActionCatalog.Require(p.Value.Kind).SourceKinds[0], p.Value.SourceCard?.Id.ToString(),
                p.Value.SourceCard == null ? p.Value.ChoiceId : Id(p.Value.SourceCard)))).ToArray();

    private static SelectionView? ReadGeneratedCombatChoice(NChooseACardSelectionScreen screen, bool bind)
    {
        var completion = ReadUiField<TaskCompletionSource<IEnumerable<CardModel>>>(screen, "_completionSource");
        if (completion.Task.IsCompleted || ReadUiField<bool>(screen, "_screenComplete")
            || ReadUiField<bool>(screen, "_cardSelected") || ReadUiField<NPeekButton>(screen, "_peekButton").IsPeeking) return null;
        var choiceId = Id(completion);
        var row = ReadUiField<Control>(screen, "_cardRow");
        var delayField = typeof(NChooseACardSelectionScreen).GetField("_noSelectionTimeMsec", BindingFlags.Static | BindingFlags.NonPublic)
            ?? throw new MissingFieldException(typeof(NChooseACardSelectionScreen).FullName, "_noSelectionTimeMsec");
        var selectionDelay = (ulong)delayField.GetRawConstantValue()!;
        var openedTicks = ReadUiField<ulong>(screen, "_openedTicks");
        var cards = new List<object>();
        foreach (var card in ReadUiField<IReadOnlyList<CardModel>>(screen, "_cards"))
        {
            var holder = row.GetChildren().OfType<NCardHolder>().FirstOrDefault(h => ReferenceEquals(h.CardModel, card));
            var enabled = holder != null && holder.IsNodeReady() && holder.IsVisibleInTree()
                && !holder.IsQueuedForDeletion() && ReadUiField<bool>(holder, "_isClickable")
                && Time.GetTicksMsec() >= openedTicks + selectionDelay;
            var actionId = "combat_select_" + choiceId + "_" + Id(card);
            cards.Add(new { card = Card(card), selected = false, available = enabled, action_id = enabled ? actionId : null });
            if (enabled && bind) CombatChoiceBindings[actionId] = new("select_combat_card", choiceId, "generated", () => {
                // Invoke the game's choice handler; it performs synchronization and resolves the source task.
                var method = typeof(NChooseACardSelectionScreen).GetMethod("SelectHolder", BindingFlags.Instance | BindingFlags.NonPublic)
                    ?? throw new MissingMethodException("NChooseACardSelectionScreen.SelectHolder");
                method.Invoke(screen, [holder]);
            }, card);
            currentCombatChoiceReady |= enabled;
        }
        var skip = ReadUiField<MegaCrit.Sts2.Core.Nodes.GodotExtensions.NButton>(screen, "_skipButton");
        var canSkip = ReadUiField<bool>(screen, "_canSkip") && SelectionButtonReady(skip);
        var skipId = "skip_combat_choice_" + choiceId;
        if (canSkip && bind) CombatChoiceBindings[skipId] = new("skip_combat_choice", choiceId, "generated", skip.ForceClick);
        currentCombatChoiceReady |= canSkip;
        currentCombatChoiceId = choiceId;
        currentCombatChoiceFingerprint = "generated";
        return new(choiceId, screen.GetType().Name, "Choose one generated card", canSkip ? 0 : 1, 1,
            canSkip, false, currentCombatChoiceReady, cards.ToArray(),
            [new { type = "skip_combat_choice", available = canSkip, action_id = canSkip ? skipId : null }]);
    }

    private static bool YieldCombatChoice()
    {
        if (activeRequest == null || executing?.State != GameActionState.GatheringPlayerChoice
            || !(activeRequest.ActionId.StartsWith("play_") || activePotion is { Discard: false }) || !currentCombatChoiceReady) return false;
        suspendedCombatRequest = activeRequest;
        suspendedCombatPotion = activePotion;
        activePotion = null;
        combatChoiceYieldTick = tick;
        activeRequest = null;
        Ledger.Update(new(suspendedCombatRequest.RequestId, "awaiting_choice", true,
            Message: "Read card_selection and submit one legal selection action; the parent game action remains pending."), release: true);
        Capture();
        Ledger.Update(new(suspendedCombatRequest.RequestId, "awaiting_choice", true, SnapshotJson: snapshot));
        return true;
    }

    private static bool CombatParentSettled(RunState? run, Player? player) => executing?.State == GameActionState.Finished
        && !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty
        && (run == null || run.IsGameOver || !CombatManager.Instance.IsInProgress
            || CanDecide(run, CombatManager.Instance.DebugOnlyGetState(), player));

    private static void ObserveCombatParent()
    {
        if (suspendedCombatRequest == null || activeCombatChoice != null) return;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        var failed = run == null || Id(run) != suspendedCombatRequest.RunId
            || executing?.Exception != null || executing?.State == GameActionState.Canceled;
        if (!failed && !CombatParentSettled(run, player))
        {
            if (!currentCombatChoiceReady && tick - combatChoiceYieldTick > 180)
                Ledger.Update(new(suspendedCombatRequest.RequestId, "needs_user_input", true,
                    "UNSUPPORTED_COMBAT_CHOICE_FLOW", "The pending card play has not reached a supported decision; query this request after resolving the flow.", snapshot));
            return;
        }
        var id = suspendedCombatRequest.RequestId;
        var potionNotConsumed = suspendedCombatPotion != null && player?.Potions.Contains(suspendedCombatPotion.Potion) == true;
        failed |= potionNotConsumed;
        suspendedCombatRequest = null; suspendedCombatPotion = null; executing = null;
        Capture();
        Ledger.Update(new(id, failed ? "failed" : "completed", true, failed ? "ACTION_FAILED" : null, SnapshotJson: snapshot));
    }

    private static void ObserveCombatChoiceCompletion()
    {
        if (activeRequest == null || activeCombatChoice == null || tick <= submittedTick || suspendedCombatRequest == null) return;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        var outcome = CombatChoiceRules.Evaluate(new(run != null && Id(run) == suspendedCombatRequest.RunId,
            executing?.Exception != null || executing?.State == GameActionState.Canceled,
            executing?.State == GameActionState.Finished,
            !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty,
            CombatParentSettled(run, player), currentCombatChoiceReady,
            currentCombatChoiceId == activeCombatChoice.ChoiceId,
            currentCombatChoiceFingerprint != activeCombatChoice.Before,
            NModalContainer.Instance?.OpenModal != null, tick - submittedTick > 180));
        if (outcome is CombatChoiceOutcome.LocalCompleted or CombatChoiceOutcome.NextChoice or CombatChoiceOutcome.Completed or CombatChoiceOutcome.Failed)
        {
            var id = activeRequest.RequestId;
            var failed = outcome == CombatChoiceOutcome.Failed;
            Ledger.Update(new(id, failed ? "failed" : "completed", true, failed ? "COMBAT_CHOICE_FAILED" : null), release: true);
            activeRequest = null; activeCombatChoice = null;
            ObserveCombatParent();
            Capture();
            Ledger.Update(new(id, failed ? "failed" : "completed", true, failed ? "COMBAT_CHOICE_FAILED" : null, SnapshotJson: snapshot));
        }
        else if (outcome == CombatChoiceOutcome.NeedsUserInput)
            Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_COMBAT_CHOICE_FLOW",
                "Query the child and parent request IDs after resolving this unsupported flow.", snapshot));
    }
}
