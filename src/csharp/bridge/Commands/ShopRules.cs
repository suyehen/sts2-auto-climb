namespace AutoSlayBridge;
internal enum ShopOutcome { Waiting, Completed, SelectionOpened, NeedsUserInput, Failed }
internal sealed record ShopProgress(bool SameRun, bool TaskFailed, bool TaskDone, bool Purchased,
    bool SelectionReady, bool NextReady, bool Idle, bool Modal, bool TimedOut);
internal static class ShopRules
{
    public static ShopOutcome Evaluate(ShopProgress p)
    {
        if (!p.SameRun || p.TaskFailed) return ShopOutcome.Failed;
        if (p.Modal) return ShopOutcome.NeedsUserInput;
        if (p.SelectionReady) return ShopOutcome.SelectionOpened;
        if (p.TaskDone && p.Purchased && p.NextReady && p.Idle) return ShopOutcome.Completed;
        if (p.TaskDone && !p.Purchased) return ShopOutcome.Failed;
        return p.TimedOut ? ShopOutcome.NeedsUserInput : ShopOutcome.Waiting;
    }
}
