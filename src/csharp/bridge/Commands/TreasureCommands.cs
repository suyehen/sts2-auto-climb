using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.Screens.TreasureRoomRelic;
using MegaCrit.Sts2.Core.Nodes.TreasureRooms;
using MegaCrit.Sts2.Core.Runs;

namespace AutoSlayBridge;
public static partial class Bridge
{
    private sealed record TreasureView(string? ScreenId, bool DecisionReady, bool Opened, bool Picking, object[] Relics, object[] Controls);
    private sealed record TreasureBinding(NTreasureRoom Room, string Kind, NButton Button, string RunId);
    private static readonly Dictionary<string, TreasureBinding> TreasureBindings = new();
    private static TreasureBinding? activeTreasure;
    private static Task? treasureOpenTask;
    private static NTreasureRoom? treasureTaskRoom;
    private static bool TreasureIdle(RunState? run, Player? player, NTreasureRoom room) => UiIdle(run, player)
        && room.IsNodeReady() && IsActive(room) && NGame.Instance?.Transition?.InTransition != true;
    private static bool TreasureButtonReady(NButton button) => SelectionButtonReady(button)
        && button.MouseFilter == Control.MouseFilterEnum.Stop;
    private static TreasureView ReadTreasure(RunState? run, Player? player, bool bind)
    {
        TreasureBindings.Clear();
        var room = ActiveScreenContext.Instance.GetCurrentScreen() as NTreasureRoom;
        if (room == null || !room.IsNodeReady()) return new(null, false, false, false, [], []);
        var idle = TreasureIdle(run, player, room);
        var opened = ReadUiField<bool>(room, "_hasChestBeenOpened");
        var picking = ReadUiField<bool>(room, "_isRelicCollectionOpen");
        var controls = new List<object>();
        var relics = new List<object>();
        var ready = false;
        void Add(string kind, NButton button)
        {
            var enabled = idle && TreasureButtonReady(button);
            var id = kind + "_" + Id(room);
            controls.Add(new { type = kind, available = enabled, action_id = enabled ? id : null });
            if (!enabled) return;
            ready = true;
            if (bind) TreasureBindings[id] = new(room, kind, button, Id(run!));
        }
        if (!opened && !picking) Add("open_chest", ReadUiField<NTreasureButton>(room, "_chestButton"));
        if (picking)
        {
            var collection = ReadUiField<NTreasureRoomRelicCollection>(room, "_relicCollection");
            var holder = collection.SingleplayerRelicHolder;
            if (holder.IsVisibleInTree() && holder.Relic?.Model is {} relic)
            {
                var enabled = idle && TreasureButtonReady(holder);
                var id = "take_treasure_relic_" + Id(room) + "_" + Id(holder);
                relics.Add(new { entity_id = relic.Id.ToString(), name = relic.Title.GetFormattedText(),
                    description = relic.DynamicDescription.GetFormattedText(),
                    hover_tips = relic.HoverTipsExcludingRelic.Select(EventTip).ToArray(), available = enabled, action_id = enabled ? id : null });
                if (enabled) { ready = true; if (bind) TreasureBindings[id] = new(room, "take_treasure_relic", holder, Id(run!)); }
            }
        }
        if (opened) Add(picking ? "skip_treasure_relic" : "proceed_treasure", room.ProceedButton);
        return new(Id(room), ready, opened, picking, relics.ToArray(), controls.ToArray());
    }
    private static LegalAction[] TreasureActions() => TreasureBindings.Select(p => new LegalAction(p.Key, p.Value.Kind, null, [], true)).ToArray();
    private static void DispatchTreasure(TreasureBinding binding)
    {
        activeTreasure = binding;
        if (binding.Kind == "open_chest")
        {
            // Invoke the same asynchronous routine as the chest release handler, and retain its task across relic selection.
            treasureTaskRoom = binding.Room;
            treasureOpenTask = (Task)(typeof(NTreasureRoom).GetMethod("OpenChest", BindingFlags.Instance | BindingFlags.NonPublic)
                ?? throw new MissingMethodException("NTreasureRoom.OpenChest")).Invoke(binding.Room, null)!;
            binding.Button.Disable();
        }
        else binding.Button.ForceClick();
    }
    private static void ObserveTreasureCompletion()
    {
        if (activeRequest == null || activeTreasure == null || tick <= submittedTick) return;
        var binding = activeTreasure;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        if (run == null || Id(run) != binding.RunId || treasureOpenTask?.IsFaulted == true || treasureOpenTask?.IsCanceled == true)
            { FinishTreasure("failed", "TREASURE_ACTION_FAILED"); return; }
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        bool complete;
        if (binding.Kind == "proceed_treasure") complete = current is NMapScreen map && IsActive(map) && map.IsTravelEnabled;
        else
        {
            var view = ReadTreasure(run, player, false);
            complete = TreasureIdle(run, player, binding.Room) && view.DecisionReady &&
                (binding.Kind == "open_chest" ? view.Opened : view.Opened && !view.Picking
                    && (treasureTaskRoom != binding.Room || treasureOpenTask?.IsCompletedSuccessfully == true));
        }
        if (complete) { FinishTreasure("completed"); return; }
        if (tick - submittedTick > 180)
            Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_TREASURE_FLOW",
                "Treasure transition or nested choice did not settle. Query this request ID before further actions.", snapshot));
    }
    private static void FinishTreasure(string status, string? error = null)
    {
        var id = activeRequest!.RequestId;
        Ledger.Update(new(id, status, true, error), release: true);
        activeRequest = null; activeTreasure = null;
        Capture(); Ledger.Update(new(id, status, true, error, SnapshotJson: snapshot));
    }
}
