using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;

namespace AutoSlayBridge;

public static partial class Bridge
{
    private sealed record RewardView(string? Phase, string? ScreenId, string? ScreenType,
        object[] Rewards, object[] Cards, object[] Alternatives, bool DecisionReady);
    private sealed record UiBinding(Node Screen, Node Control, string Kind, Func<Task?> Dispatch, object? ChoiceToken = null);
    private static readonly Dictionary<string, UiBinding> UiBindings = new();
    private static UiBinding? activeUi;
    private static Task? uiTask;
    private static Task? pendingRewardTask;

    // These private UI members are version-specific. Fail closed if the pinned game changes.
    private static T ReadUiField<T>(object instance, string name)
    {
        for (var type = instance.GetType(); type != null; type = type.BaseType)
        {
            var field = type.GetField(name, BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.DeclaredOnly);
            if (field != null) return (T)field.GetValue(instance)!;
        }
        throw new MissingFieldException(instance.GetType().FullName, name);
    }

    private static Task StartReward(NRewardButton button)
    {
        var method = typeof(NRewardButton).GetMethod("GetReward", BindingFlags.Instance | BindingFlags.NonPublic)
            ?? throw new MissingMethodException("NRewardButton.GetReward");
        // Same async routine as a normal button release, including synchronization and UI signals.
        return (Task)method.Invoke(button, null)!;
    }

    private static bool UiIdle(RunState? run, Player? player) => run != null && player != null && !run.IsGameOver
        && !RunManager.Instance.IsPaused && !CombatManager.Instance.IsInProgress
        && !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty
        && NModalContainer.Instance?.OpenModal == null;

    private static bool IsActive(Node screen) => GodotObject.IsInstanceValid(screen) && !screen.IsQueuedForDeletion()
        && screen is Control control && control.IsVisibleInTree()
        && ReferenceEquals(ActiveScreenContext.Instance.GetCurrentScreen(), screen);

    private static RewardView ReadRewards(RunState? run, Player? player, bool bind)
    {
        UiBindings.Clear();
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = current as Node;
        var rewards = new List<object>();
        var cards = new List<object>();
        var alternatives = new List<object>();
        var idle = UiIdle(run, player) && screen != null && IsActive(screen);
        var ready = false;
        string? phase = null;
        if (screen is NRewardsScreen rewardScreen)
        {
            phase = "rewards";
            var buttons = ReadUiField<List<Control>>(rewardScreen, "_rewardButtons");
            foreach (var button in buttons.OfType<NRewardButton>())
            {
                if (!GodotObject.IsInstanceValid(button) || button.IsQueuedForDeletion()) continue;
                var reward = button.Reward;
                if (reward == null) continue;
                // Linked/removal/special rewards are observable, but require later adapters.
                var supported = reward.GetType() == typeof(GoldReward) || reward.GetType() == typeof(CardReward)
                    || reward.GetType() == typeof(RelicReward) || reward.GetType() == typeof(PotionReward);
                var enabled = idle && button.IsEnabled && button.IsVisibleInTree() && supported
                    && (reward is not PotionReward || player?.HasOpenPotionSlots == true)
                    && (pendingRewardTask == null || pendingRewardTask.IsCompletedSuccessfully);
                var actionId = "claim_" + Id(reward);
                rewards.Add(new { reward_id = Id(reward), type = reward.GetType().Name,
                    description = reward.Description.GetFormattedText(), supported, available = enabled,
                    gold_amount = reward is GoldReward gold ? (int?)gold.Amount : null,
                    entity_id = reward is RelicReward relic ? relic.Relic?.Id.ToString()
                        : reward is PotionReward potion ? potion.Potion?.Id.ToString() : null,
                    action_id = enabled ? actionId : null });
                if (enabled)
                {
                    ready = true;
                    if (bind) UiBindings[actionId] = new(rewardScreen, button, "claim_reward", () => {
                        pendingRewardTask = StartReward(button);
                        return pendingRewardTask;
                    });
                }
            }
            // Report linked sets without exposing constituent cards before the player opens them.
            foreach (var button in buttons.Where(b => b is not NRewardButton))
                rewards.Add(new { reward_id = Id(button), type = button.GetType().Name, supported = false, available = false });
            var proceed = ReadUiField<NProceedButton>(rewardScreen, "_proceedButton");
            if (idle && proceed.IsEnabled && proceed.IsVisibleInTree()
                && (pendingRewardTask == null || pendingRewardTask.IsCompletedSuccessfully))
            {
                ready = true;
                var actionId = "proceed_" + Id(rewardScreen);
                alternatives.Add(new { option_id = "proceed", description = proceed.IsSkip ? "Skip remaining rewards and proceed" : "Proceed", action_id = actionId });
                if (bind) UiBindings[actionId] = new(rewardScreen, proceed, "proceed_rewards", () => { proceed.ForceClick(); return null; });
            }
        }
        else if (screen is NCardRewardSelectionScreen choiceScreen)
        {
            phase = "card_reward_selection";
            var completion = ReadUiField<TaskCompletionSource<int?>?>(choiceScreen, "_completionSource");
            var available = idle && completion != null && !completion.Task.IsCompleted;
            var row = ReadUiField<Control>(choiceScreen, "_cardRow");
            foreach (var holder in row.GetChildren().OfType<NGridCardHolder>())
            {
                if (holder.CardModel == null || holder.IsQueuedForDeletion()) continue;
                var clickable = ReadUiField<bool>(holder, "_isClickable");
                var enabled = available && clickable && holder.IsVisibleInTree();
                var card = holder.CardModel;
                var actionId = "choose_" + Id(choiceScreen) + "_" + Id(card);
                cards.Add(new { card = Card(card), available = enabled, action_id = enabled ? actionId : null });
                if (enabled)
                {
                    ready = true;
                    if (bind) UiBindings[actionId] = new(choiceScreen, holder, "choose_reward_card", () => {
                        holder.EmitSignal(NCardHolder.SignalName.Pressed, holder);
                        return pendingRewardTask;
                    }, completion);
                }
            }
            var options = ReadUiField<IReadOnlyList<CardRewardAlternative>>(choiceScreen, "_extraOptions");
            var container = ReadUiField<Control>(choiceScreen, "_rewardAlternativesContainer");
            var buttons = container.GetChildren().OfType<NCardRewardAlternativeButton>().Where(b => !b.IsQueuedForDeletion()).ToArray();
            for (var i = 0; i < options.Count; i++)
            {
                var option = options[i];
                var button = i < buttons.Length ? buttons[i] : null;
                var enabled = available && button != null && button.IsEnabled && button.IsVisibleInTree();
                var actionId = "alternative_" + Id(choiceScreen) + "_" + i;
                alternatives.Add(new { option_id = option.OptionId, description = option.Title.GetFormattedText(),
                    after_selected = option.AfterSelected.ToString(), available = enabled, action_id = enabled ? actionId : null });
                if (enabled)
                {
                    ready = true;
                    if (bind) UiBindings[actionId] = new(choiceScreen, button!, "reward_alternative", () => {
                        button!.ForceClick(); return pendingRewardTask;
                    }, completion);
                }
            }
        }
        return new(phase, screen == null ? null : Id(screen), current?.GetType().Name,
            rewards.ToArray(), cards.ToArray(), alternatives.ToArray(), ready);
    }

    private static LegalAction[] RewardActions() => UiBindings.Select(pair => new LegalAction(
        pair.Key, pair.Value.Kind, pair.Value.Control is NCardHolder holder && holder.CardModel != null ? Id(holder.CardModel) : null,
        [], true)).ToArray();

    private static void ObserveUiCompletion()
    {
        if (activeRequest == null || activeUi == null || tick <= submittedTick) return;
        var binding = activeUi;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        var selection = current as NCardRewardSelectionScreen;
        var completion = selection == null ? null : ReadUiField<TaskCompletionSource<int?>?>(selection, "_completionSource");
        var selectionReady = selection != null && IsActive(selection) && UiIdle(run, player)
            && completion != null && !completion.Task.IsCompleted;
        var outcome = RewardCompletion.Evaluate(new(binding.Kind,
            TaskFailed: uiTask?.IsFaulted == true || uiTask?.IsCanceled == true,
            TaskCompleted: uiTask?.IsCompletedSuccessfully == true, HasTask: uiTask != null,
            SelectionReady: selectionReady, HasChoiceToken: binding.ChoiceToken != null,
            ChoiceTokenChanged: !ReferenceEquals(completion, binding.ChoiceToken),
            OldChoiceCompleted: binding.ChoiceToken is TaskCompletionSource<int?> chosen && chosen.Task.IsCompletedSuccessfully,
            ScreenChanged: !ReferenceEquals(current, binding.Screen),
            Idle: binding.Kind == "proceed_rewards"
                ? !RunManager.Instance.IsPaused && !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty
                : UiIdle(run, player),
            ModalOpen: NModalContainer.Instance?.OpenModal != null, TimedOut: tick - submittedTick > 120));
        switch (outcome)
        {
            case RewardOutcome.Failed:
                FinishUi("failed", "REWARD_ACTION_FAILED", uiTask?.Exception?.GetBaseException().Message); break;
            case RewardOutcome.SelectionOpened:
                FinishUi("completed", message: "Card reward opened; choose one listed action from a fresh snapshot."); break;
            case RewardOutcome.NextSelection:
                FinishUi("completed", message: "Choice resolved; another reward choice is available."); break;
            case RewardOutcome.Completed:
                FinishUi("completed"); break;
            case RewardOutcome.NeedsUserInput:
                Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_REWARD_FLOW",
                    "Resolve the open modal or unsupported reward selection manually, then query the same request ID.", snapshot)); break;
        }
    }

    private static void FinishUi(string status, string? error = null, string? message = null)
    {
        var requestId = activeRequest!.RequestId;
        Ledger.Update(new(requestId, status, true, error, message), release: true);
        activeRequest = null; activeUi = null; uiTask = null;
        if (pendingRewardTask?.IsCompletedSuccessfully == true) pendingRewardTask = null;
        Capture();
        Ledger.Update(new(requestId, status, true, error, message, snapshot));
    }
}
