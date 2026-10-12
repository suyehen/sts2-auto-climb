using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;

namespace AutoSlayBridge;

public static partial class Bridge
{
    private sealed record MapCoordinate(int Column, int Row);
    private sealed record MapVisualPosition(float X, float Y);
    private sealed record MapNodeView(string NodeId, MapCoordinate Coordinate, string Type, string State,
        bool Visited, bool Available, string? ActionId, string[] Children, MapVisualPosition VisualPosition,
        string? IconAsset);
    private sealed record MapView(bool Open, bool Active, bool DecisionReady, string? ScreenId, int? ActIndex,
        long? Generation, MapCoordinate? CurrentPosition, MapCoordinate[] Visited, MapNodeView[] Nodes);
    private sealed record MapBinding(NMapScreen Screen, NMapPoint Node, MapCoord Coordinate,
        int ActIndex, long Generation, string RunId, int BeforeFloor, object? BeforeRoom);
    private static readonly Dictionary<string, MapBinding> MapBindings = new();
    private static MapBinding? activeMap;

    private static MapCoordinate Coordinate(MapCoord coord) => new(coord.col, coord.row);
    private static string MapNodeId(int act, long generation, MapCoord coord) => $"map_{act}_{generation}_{coord.col}_{coord.row}";

    private static bool CanSelectMapPoint(RunState run, NMapScreen screen, NMapPoint node)
    {
        var animation = ReadUiField<Tween?>(screen, "_actAnimTween");
        return MapRules.CanSelect(new(
            Singleplayer: run.Players.Count == 1 && !run.IsGameOver,
            ActiveMap: screen.IsOpen && IsActive(screen), TravelEnabled: screen.IsTravelEnabled,
            Traveling: screen.IsTraveling, DebugTravel: screen.IsDebugTravelEnabled,
            InputBlocked: ReadUiField<bool>(screen, "_isInputDisabled") || animation?.IsRunning() == true,
            Drawing: screen.Drawings.GetLocalDrawingMode() != DrawingMode.None,
            Transition: NGame.Instance?.Transition?.InTransition == true,
            Paused: RunManager.Instance.IsPaused,
            QueueIdle: !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty,
            TravelablePoint: node.State == MapPointState.Travelable,
            NodeEnabled: GodotObject.IsInstanceValid(node) && !node.IsQueuedForDeletion() && node.IsEnabled,
            TutorialReady: node.Point.coord.row != 0 || SaveManager.Instance.SeenFtue("map_select_ftue")));
    }

    private static MapView ReadMap(RunState? run, bool bind)
    {
        MapBindings.Clear();
        var screen = NMapScreen.Instance;
        var open = screen != null && screen.IsOpen;
        var active = open && IsActive(screen!);
        var current = run?.CurrentMapCoord is MapCoord coord ? Coordinate(coord) : null;
        var visited = run?.VisitedMapCoords.Select(Coordinate).ToArray() ?? [];
        long? generation = run == null ? null : Convert.ToInt64(RunManager.Instance.MapSelectionSynchronizer.MapGenerationCount);
        if (run == null || screen == null || !open)
            return new(open, active, false, screen == null ? null : Id(screen), run?.CurrentActIndex, generation, current, visited, []);
        var dictionary = ReadUiField<Dictionary<MapCoord, NMapPoint>>(screen, "_mapPointDictionary");
        var rendered = dictionary.Values.Where(n => GodotObject.IsInstanceValid(n) && !n.IsQueuedForDeletion()).ToArray();
        var coordinates = rendered.Select(n => n.Point.coord).ToHashSet();
        var nodes = new List<MapNodeView>();
        var ready = false;
        foreach (var node in rendered.OrderBy(n => n.Point.coord.row).ThenBy(n => n.Point.coord.col))
        {
            var point = node.Point;
            var available = CanSelectMapPoint(run, screen, node);
            var nodeId = MapNodeId(run.CurrentActIndex, generation!.Value, point.coord);
            var actionId = "travel_" + nodeId;
            nodes.Add(new(nodeId, Coordinate(point.coord), point.PointType.ToString(), node.State.ToString(),
                run.VisitedMapCoords.Contains(point.coord), available, available ? actionId : null,
                point.Children.Where(c => coordinates.Contains(c.coord)).OrderBy(c => c.coord.col)
                    .Select(c => MapNodeId(run.CurrentActIndex, generation.Value, c.coord)).ToArray(),
                // Actual game layout, including its seeded offsets; excludes hover/scroll transforms.
                new(node.Position.X + node.Size.X / 2, node.Position.Y + node.Size.Y / 2), ReadMapIconAsset(node, run)));
            if (available)
            {
                ready = true;
                if (bind) MapBindings[actionId] = new(screen, node, point.coord, run.CurrentActIndex, generation.Value,
                    Id(run), run.TotalFloor, run.CurrentRoom);
            }
        }
        return new(open, active, ready, Id(screen), run.CurrentActIndex, generation, current, visited, nodes.ToArray());
    }

    private static string? ReadMapIconAsset(NMapPoint node, RunState run)
    {
        // Read the texture actually assigned by the game (including revealed unknowns
        // and Ancient portraits), rather than deriving a replacement from room type.
        var icon = node.GetNodeOrNull<TextureRect>("%Icon")
            ?? node.GetNodeOrNull<TextureRect>("Icon")
            ?? node.GetNodeOrNull<TextureRect>("%PlaceholderImage");
        var path = icon?.Texture?.ResourcePath;
        // Model paths also cover atlas textures whose ResourcePath is empty.
        if (string.IsNullOrEmpty(path) && node is NAncientMapPoint)
            return "MAP.ANCIENT_NODE_" + run.Act.Ancient.Id.Entry.ToUpperInvariant();
        if (string.IsNullOrEmpty(path) && node is NBossMapPoint)
        {
            var encounter = node.Point == run.Map.SecondBossMapPoint
                ? run.Act.SecondBossEncounter : run.Act.BossEncounter;
            if (encounter?.BossNodeSpineResource == null) path = encounter?.BossNodePath;
        }
        if (string.IsNullOrEmpty(path) || !path.StartsWith("res://images/", StringComparison.Ordinal))
            return null; // Animated Spine boss nodes have no standalone texture.
        var name = System.IO.Path.GetFileNameWithoutExtension(path).ToUpperInvariant();
        return name.Length > 0 && name.All(c => char.IsAsciiLetterOrDigit(c) || c == '_')
            ? "MAP." + name : null;
    }

    private static LegalAction[] MapActions() => MapBindings.Select(pair => new LegalAction(pair.Key, "choose_map_node", null, [], true)).ToArray();

    private static void ObserveMapCompletion()
    {
        if (activeRequest == null || activeMap == null || tick <= submittedTick) return;
        var binding = activeMap;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var manager = CombatManager.Instance;
        var combat = manager.IsInProgress ? manager.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        var screen = NMapScreen.Instance;
        var current = ActiveScreenContext.Instance.GetCurrentScreen() as Control;
        var roomReady = combat != null ? CanDecide(run, combat, player)
            : current != null && current.IsNodeReady() && current.IsVisibleInTree();
        var outcome = MapRules.Evaluate(new(
            SameRun: run != null && Id(run) == binding.RunId && run.CurrentActIndex == binding.ActIndex,
            AtTarget: run?.CurrentMapCoord == binding.Coordinate,
            RoomChanged: run?.CurrentRoom != null && !ReferenceEquals(run.CurrentRoom, binding.BeforeRoom),
            FloorAdvanced: run?.TotalFloor > binding.BeforeFloor,
            Traveling: screen?.IsTraveling == true, MapOpen: screen?.IsOpen == true,
            Transition: NGame.Instance?.Transition?.InTransition == true,
            QueueIdle: !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty,
            RoomReady: roomReady, ModalOpen: NModalContainer.Instance?.OpenModal != null,
            TimedOut: tick - submittedTick > 180));
        switch (outcome)
        {
            case MapOutcome.Completed: FinishMap("completed"); break;
            case MapOutcome.Failed: FinishMap("failed", "RUN_CHANGED_DURING_TRAVEL"); break;
            case MapOutcome.NeedsUserInput:
                Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_MAP_FLOW",
                    "Inspect or resolve the open tutorial/room transition manually, then query this request ID.", snapshot)); break;
        }
    }

    private static void FinishMap(string status, string? error = null)
    {
        var requestId = activeRequest!.RequestId;
        Ledger.Update(new(requestId, status, true, error), release: true);
        activeRequest = null; activeMap = null;
        Capture();
        Ledger.Update(new(requestId, status, true, error, SnapshotJson: snapshot));
    }
}
