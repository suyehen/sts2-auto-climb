using Godot;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.addons.mega_text;

namespace AutoSlayBridge;
public static partial class Bridge
{
    private sealed record SelectionView(string? ScreenId, string? ScreenType, string? Prompt,
        int? MinSelect, int? MaxSelect, bool Cancelable, bool Preview, bool DecisionReady, object[] Cards, object[] Controls,
        object? Enchantment = null);
    private sealed record SelectionBinding(NCardGridSelectionScreen Screen, string Kind, string Before,
        string RunId, Action Dispatch, Task Completion);
    private static readonly Dictionary<string, SelectionBinding> SelectionBindings = new();
    private static SelectionBinding? activeSelection;
    private static Task[] pendingSelectionEffects = [];

    private static bool SupportedSelection(NCardGridSelectionScreen screen) => screen is NDeckCardSelectScreen or NDeckUpgradeSelectScreen or NDeckEnchantSelectScreen or NSimpleCardSelectScreen;
    private static bool SelectionIdle(RunState? run, Player? player, NCardGridSelectionScreen screen) => UiIdle(run, player)
        && SupportedSelection(screen) && IsActive(screen) && screen.IsNodeReady()
        && screen.MouseFilter == Control.MouseFilterEnum.Stop && NGame.Instance?.Transition?.InTransition != true
        && !ReadUiField<TaskCompletionSource<IEnumerable<CardModel>>>(screen, "_completionSource").Task.IsCompleted;
    private static bool SelectionPreview(NCardGridSelectionScreen screen) => screen is not NSimpleCardSelectScreen && (screen is NDeckCardSelectScreen
        ? ReadUiField<Control>(screen, "_previewContainer").Visible
        : screen is NDeckEnchantSelectScreen
            ? ReadUiField<Control>(screen, "_enchantSinglePreviewContainer").Visible
                || ReadUiField<Control>(screen, "_enchantMultiPreviewContainer").Visible
        : ReadUiField<Control>(screen, "_upgradeSinglePreviewContainer").Visible
            || ReadUiField<Control>(screen, "_upgradeMultiPreviewContainer").Visible);
    private static string SelectionFingerprint(NCardGridSelectionScreen screen) => SelectionPreview(screen) + ":"
        + string.Join(",", ReadUiField<HashSet<CardModel>>(screen, "_selectedCards").Select(Id).Order());
    private static bool SelectionButtonReady(NButton button) => GodotObject.IsInstanceValid(button)
        && button.IsNodeReady() && !button.IsQueuedForDeletion() && button.IsEnabled && button.IsVisibleInTree();

    private static SelectionView ReadSelection(RunState? run, Player? player, bool bind)
    {
        SelectionBindings.Clear();
        var screen = ActiveScreenContext.Instance.GetCurrentScreen() as NCardGridSelectionScreen;
        if (screen == null || !SupportedSelection(screen) || !screen.IsNodeReady())
            return new(null, screen?.GetType().Name, null, null, null, false, false, false, [], []);
        var prefs = ReadUiField<CardSelectorPrefs>(screen, "_prefs");
        var preview = SelectionPreview(screen);
        var selected = ReadUiField<HashSet<CardModel>>(screen, "_selectedCards");
        var completion = ReadUiField<TaskCompletionSource<IEnumerable<CardModel>>>(screen, "_completionSource").Task;
        var grid = ReadUiField<NCardGrid>(screen, "_grid");
        var idle = SelectionIdle(run, player, screen);
        var before = SelectionFingerprint(screen);
        var cards = new List<object>();
        var controls = new List<object>();
        var ready = false;
        foreach (var card in ReadUiField<IReadOnlyList<CardModel>>(screen, "_cards"))
        {
            var holder = grid.GetCardHolder(card);
            var enabled = idle && !preview && holder != null && holder.IsNodeReady() && !holder.IsQueuedForDeletion()
                && ReadUiField<bool>(holder, "_isClickable")
                && (screen is not NSimpleCardSelectScreen || selected.Contains(card) || selected.Count < prefs.MaxSelect);
            var actionId = "select_" + Id(screen) + "_" + Id(card);
            cards.Add(new { card = Card(card), selected = selected.Contains(card), available = enabled, action_id = enabled ? actionId : null });
            if (enabled)
            {
                ready = true;
                if (bind) SelectionBindings[actionId] = new(screen, "toggle_selection_card", before, Id(run!),
                    () => grid.EmitSignal(NCardGrid.SignalName.HolderPressed, holder!), completion);
            }
        }
        var fields = screen is NSimpleCardSelectScreen
            ? new[] { ("_confirmButton", "confirm_card_selection") }
            : screen is NDeckCardSelectScreen
            ? (preview ? new[] { ("_previewConfirmButton", "confirm_card_selection"), ("_previewCancelButton", "cancel_selection_preview") }
                : new[] { ("_confirmButton", "preview_card_selection"), ("_closeButton", "close_card_selection") })
            : (preview ? (prefs.MaxSelect == 1
                ? new[] { ("_singlePreviewConfirmButton", "confirm_card_selection"), ("_singlePreviewCancelButton", "cancel_selection_preview") }
                : new[] { ("_multiPreviewConfirmButton", "confirm_card_selection"), ("_multiPreviewCancelButton", "cancel_selection_preview") })
                : screen is NDeckEnchantSelectScreen
                    ? new[] { ("_confirmButton", "preview_card_selection"), ("_closeButton", "close_card_selection") }
                    : new[] { ("_closeButton", "close_card_selection") });
        foreach (var (field, kind) in fields)
        {
            var button = ReadUiField<NButton>(screen, field);
            var enabled = idle && SelectionButtonReady(button) && (kind != "close_card_selection" || prefs.Cancelable);
            var actionId = kind + "_" + Id(screen);
            controls.Add(new { type = kind, available = enabled, action_id = enabled ? actionId : null });
            if (enabled)
            {
                ready = true;
                if (bind) SelectionBindings[actionId] = new(screen, kind, before, Id(run!), button.ForceClick, completion);
            }
        }
        return new(Id(screen), screen.GetType().Name, prefs.Prompt.GetFormattedText(), prefs.MinSelect, prefs.MaxSelect,
            prefs.Cancelable, preview, ready, cards.ToArray(), controls.ToArray(), screen is NDeckEnchantSelectScreen
                ? new { entity_id = ReadUiField<EnchantmentModel>(screen, "_enchantment").Id.ToString(),
                    amount = ReadUiField<int>(screen, "_enchantmentAmount"),
                    title = ReadUiField<EnchantmentModel>(screen, "_enchantment").Title.GetFormattedText(),
                    description = ReadUiField<MegaRichTextLabel>(screen, "_enchantmentDescription").Text }
                : null);
    }
    private static LegalAction[] SelectionActions() => SelectionBindings.Select(p => new LegalAction(p.Key, p.Value.Kind, null, [], true)).ToArray();
    private static void ObserveSelectionCompletion()
    {
        if (activeRequest == null || activeSelection == null || tick <= submittedTick) return;
        var binding = activeSelection;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        var sameScreen = GodotObject.IsInstanceValid(binding.Screen) && !binding.Screen.IsQueuedForDeletion() && IsActive(binding.Screen);
        var newSelection = ActiveScreenContext.Instance.GetCurrentScreen() as NCardGridSelectionScreen;
        var nextReady = newSelection != null && SelectionIdle(run, player, newSelection)
            || ReadRest(run, player, false).DecisionReady || ReadShop(run, player, false).DecisionReady || ReadEvent(run, player, false).DecisionReady || ReadMap(run, false).DecisionReady || ReadRewards(run, player, false).DecisionReady;
        var idle = run != null && !RunManager.Instance.IsPaused && !RunManager.Instance.ActionExecutor.IsRunning
            && RunManager.Instance.ActionQueueSet.IsEmpty && NGame.Instance?.Transition?.InTransition != true;
        var outcome = SelectionRules.Evaluate(new(run != null && Id(run) == binding.RunId,
            pendingSelectionEffects.Any(t => t.IsFaulted || t.IsCanceled) || binding.Completion.IsFaulted || binding.Completion.IsCanceled,
            !sameScreen, sameScreen && SelectionFingerprint(binding.Screen) != binding.Before,
            sameScreen && SelectionIdle(run, player, binding.Screen), pendingSelectionEffects.All(t => t.IsCompletedSuccessfully)
                || !sameScreen && binding.Completion.IsCompletedSuccessfully && newSelection != null && SelectionIdle(run, player, newSelection),
            nextReady, idle, NModalContainer.Instance?.OpenModal != null, tick - submittedTick > 180));
        if (outcome == SelectionOutcome.Completed || outcome == SelectionOutcome.Failed)
        {
            var id = activeRequest.RequestId;
            var status = outcome == SelectionOutcome.Completed ? "completed" : "failed";
            var error = outcome == SelectionOutcome.Failed ? "SELECTION_ACTION_FAILED" : null;
            Ledger.Update(new(id, status, true, error), release: true);
            activeRequest = null; activeSelection = null;
            if (pendingSelectionEffects.All(t => t.IsCompleted)) pendingSelectionEffects = [];
            Capture();
            Ledger.Update(new(id, status, true, error, SnapshotJson: snapshot));
        }
        else if (outcome == SelectionOutcome.NeedsUserInput)
            Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_SELECTION_FLOW",
                "Resolve the unsupported selection or modal, then query this request ID.", snapshot));
    }
}
