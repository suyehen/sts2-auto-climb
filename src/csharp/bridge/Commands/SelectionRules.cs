namespace AutoSlayBridge;
internal enum SelectionOutcome { Waiting, Completed, NeedsUserInput, Failed }
internal sealed record SelectionProgress(bool SameRun, bool Failed, bool ScreenChanged, bool LocalChanged,
    bool SelectionReady, bool EffectsSettled, bool NextReady, bool Idle, bool Modal, bool TimedOut);
internal static class SelectionRules
{
    public static SelectionOutcome Evaluate(SelectionProgress p)
    {
        if (!p.SameRun || p.Failed) return SelectionOutcome.Failed;
        if (p.Modal) return SelectionOutcome.NeedsUserInput;
        if (!p.ScreenChanged && p.LocalChanged && p.SelectionReady && p.Idle) return SelectionOutcome.Completed;
        if (p.ScreenChanged && p.EffectsSettled && p.NextReady && p.Idle) return SelectionOutcome.Completed;
        return p.TimedOut ? SelectionOutcome.NeedsUserInput : SelectionOutcome.Waiting;
    }
}
