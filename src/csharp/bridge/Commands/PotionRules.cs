namespace AutoSlayBridge;

public enum PotionOutcome { Waiting, Completed, Failed, NeedsUserInput }
public readonly record struct PotionProgress(bool SameRun, bool Failed, bool Choice, bool Finished,
    bool Removed, bool Idle, bool NextReady, bool Modal, bool TimedOut);
public static class PotionRules
{
    public static bool CanUse(string usage, bool inCombat, bool idle, bool owned, bool queued,
        bool ownerAllowed, bool customAllowed, bool supportedTarget) => idle && owned && !queued
        && ownerAllowed && customAllowed && supportedTarget
        && (usage == "AnyTime" || usage == "CombatOnly" && inCombat);
    public static bool CanDiscard(bool idle, bool owned, bool queued, bool ownerAllowed) =>
        idle && owned && !queued && ownerAllowed;
    public static PotionOutcome Evaluate(PotionProgress p) => !p.SameRun || p.Failed ? PotionOutcome.Failed
        : p.Choice || p.Modal ? PotionOutcome.NeedsUserInput
        : p.Finished && p.Removed && p.Idle && p.NextReady ? PotionOutcome.Completed
        : p.TimedOut ? PotionOutcome.NeedsUserInput : PotionOutcome.Waiting;
}
