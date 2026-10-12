namespace AutoSlayBridge;
internal enum CombatChoiceOutcome { Waiting, LocalCompleted, NextChoice, Completed, Failed, NeedsUserInput }
internal sealed record CombatChoiceProgress(bool SameRun, bool Failed, bool ParentFinished,
    bool Idle, bool NextReady, bool ChoiceReady, bool SameChoice, bool Changed, bool Modal, bool TimedOut);
internal static class CombatChoiceRules
{
    public static CombatChoiceOutcome Evaluate(CombatChoiceProgress p)
    {
        if (!p.SameRun || p.Failed) return CombatChoiceOutcome.Failed;
        if (p.Modal) return CombatChoiceOutcome.NeedsUserInput;
        if (p.ParentFinished && p.Idle && p.NextReady) return CombatChoiceOutcome.Completed;
        if (p.ChoiceReady && !p.SameChoice) return CombatChoiceOutcome.NextChoice;
        if (p.ChoiceReady && p.SameChoice && p.Changed) return CombatChoiceOutcome.LocalCompleted;
        return p.TimedOut ? CombatChoiceOutcome.NeedsUserInput : CombatChoiceOutcome.Waiting;
    }
}
