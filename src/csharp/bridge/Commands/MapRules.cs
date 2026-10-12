namespace AutoSlayBridge;

internal sealed record MapReadiness(bool Singleplayer, bool ActiveMap, bool TravelEnabled,
    bool Traveling, bool DebugTravel, bool InputBlocked, bool Drawing, bool Transition,
    bool Paused, bool QueueIdle, bool TravelablePoint, bool NodeEnabled, bool TutorialReady);

internal enum MapOutcome { Waiting, Completed, NeedsUserInput, Failed }
internal sealed record MapProgress(bool SameRun, bool AtTarget, bool RoomChanged, bool FloorAdvanced,
    bool Traveling, bool MapOpen, bool Transition, bool QueueIdle, bool RoomReady, bool ModalOpen, bool TimedOut);

internal static class MapRules
{
    public static bool CanSelect(MapReadiness value) => value.Singleplayer && value.ActiveMap && value.TravelEnabled
        && !value.Traveling && !value.DebugTravel && !value.InputBlocked && !value.Drawing && !value.Transition
        && !value.Paused && value.QueueIdle && value.TravelablePoint && value.NodeEnabled && value.TutorialReady;

    public static MapOutcome Evaluate(MapProgress value)
    {
        if (!value.SameRun) return MapOutcome.Failed;
        if (value.AtTarget && value.RoomChanged && value.FloorAdvanced && !value.Traveling && !value.MapOpen
            && !value.Transition && value.QueueIdle && value.RoomReady && !value.ModalOpen) return MapOutcome.Completed;
        if (value.ModalOpen || value.TimedOut) return MapOutcome.NeedsUserInput;
        return MapOutcome.Waiting;
    }
}
