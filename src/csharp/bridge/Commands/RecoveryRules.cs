namespace AutoSlayBridge;
public sealed record RecoveryContext(bool SinglePlayer, bool MapOpen, bool Active,
    bool TravelEnabled, bool Traveling, bool Transition, bool QueueIdle, bool Modal, bool Alive);
public static class RecoveryRules
{
    public static bool CanCloseMap(RecoveryContext value) => value.SinglePlayer && value.MapOpen && value.Active
        && !value.TravelEnabled && !value.Traveling && !value.Transition && value.QueueIdle && !value.Modal && value.Alive;
}
