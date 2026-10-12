namespace AutoSlayBridge;

internal enum RewardOutcome { Waiting, Completed, SelectionOpened, NextSelection, Failed, NeedsUserInput }

internal sealed record RewardProgress(
    string Kind, bool TaskFailed = false, bool TaskCompleted = false, bool HasTask = true,
    bool SelectionReady = false, bool HasChoiceToken = false, bool ChoiceTokenChanged = false,
    bool OldChoiceCompleted = false, bool ScreenChanged = false, bool Idle = false,
    bool ModalOpen = false, bool TimedOut = false);

internal static class RewardCompletion
{
    public static RewardOutcome Evaluate(RewardProgress progress)
    {
        if (progress.TaskFailed) return RewardOutcome.Failed;
        if (progress.Kind == "claim_reward" && progress.SelectionReady) return RewardOutcome.SelectionOpened;
        if (progress.HasChoiceToken && progress.SelectionReady && progress.ChoiceTokenChanged) return RewardOutcome.NextSelection;
        if (progress.Kind == "proceed_rewards")
        {
            if (progress.ScreenChanged && progress.Idle && !progress.ModalOpen) return RewardOutcome.Completed;
        }
        else if (progress.TaskCompleted && progress.Idle) return RewardOutcome.Completed;
        else if (!progress.HasTask && progress.HasChoiceToken && progress.OldChoiceCompleted
                 && progress.ScreenChanged && progress.Idle) return RewardOutcome.Completed;
        if (progress.ModalOpen || progress.TimedOut) return RewardOutcome.NeedsUserInput;
        return RewardOutcome.Waiting;
    }
}
