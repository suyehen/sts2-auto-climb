namespace AutoSlayBridge;
internal enum EventOutcome { Waiting, Completed, NeedsUserInput, Failed }
internal sealed record EventProgress(bool SameRun, bool TaskFailed, bool TasksSettled, bool ChoiceChanged,
    bool NextReady, bool Idle, bool UnsupportedScreen, bool Modal, bool TimedOut);
internal static class EventRules
{
    public static EventOutcome Evaluate(EventProgress p)
    {
        if (!p.SameRun || p.TaskFailed) return EventOutcome.Failed;
        if (p.UnsupportedScreen || p.Modal) return EventOutcome.NeedsUserInput;
        if (p.TasksSettled && p.ChoiceChanged && p.NextReady && p.Idle) return EventOutcome.Completed;
        return p.TimedOut ? EventOutcome.NeedsUserInput : EventOutcome.Waiting;
    }
}
