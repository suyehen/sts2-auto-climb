using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.HoverTips;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Events;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Localization.DynamicVars;

namespace AutoSlayBridge;
public static partial class Bridge
{
    private sealed record EventView(string? EntityId, string? Title, string? Description, string? ScreenId,
        string? LayoutType, bool Supported, bool Finished, bool DecisionReady, object[] Options,
        object[]? DynamicVariables = null, object? RuntimeState = null);
    private sealed record EventBinding(NEventRoom Room, NEventOptionButton Button, EventOption Option,
        EventModel Model, string RunId);
    private static readonly Dictionary<string, EventBinding> EventBindings = new();
    private static EventBinding? activeEvent;
    private static Task[] eventTasks = [];
    private static ActionRequest? suspendedEventRequest;
    private static EventBinding? suspendedEvent;
    private static Task[] suspendedEventTasks = [];

    private static bool EventRewardChildAction(string actionId) => UiBindings.ContainsKey(actionId)
        || SelectionBindings.ContainsKey(actionId)
        || PotionBindings.TryGetValue(actionId, out var potion) && potion.Discard;

    private static void ObserveEventParent()
    {
        if (suspendedEventRequest == null || suspendedEvent == null) return;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var combat = CombatManager.Instance.IsInProgress ? CombatManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        var failed = run == null || Id(run) != suspendedEvent.RunId
            || suspendedEventTasks.Any(t => t.IsFaulted || t.IsCanceled);
        var roomActive = IsActive(suspendedEvent.Room);
        var buttons = roomActive ? suspendedEvent.Room.Layout?.OptionButtons.Where(EventButtonReady).ToArray() ?? [] : [];
        var changed = !roomActive || buttons.Length > 0 && !buttons.Any(b => ReferenceEquals(b, suspendedEvent.Button));
        var nextReady = roomActive && buttons.Length > 0
            || NMapScreen.Instance is { IsOpen: true } map && IsActive(map) && map.IsTravelEnabled
            || CanDecide(run, combat, player);
        var idle = !RunManager.Instance.IsPaused && !RunManager.Instance.ActionExecutor.IsRunning
            && RunManager.Instance.ActionQueueSet.IsEmpty && NGame.Instance?.Transition?.InTransition != true
            && NModalContainer.Instance?.OpenModal == null;
        if (!failed && !(suspendedEventTasks.All(t => t.IsCompletedSuccessfully) && changed && nextReady && idle)) return;
        var requestId = suspendedEventRequest.RequestId;
        suspendedEventRequest = null; suspendedEvent = null; suspendedEventTasks = [];
        Capture();
        // Parent completion must not release a child command still settling.
        Ledger.Update(new(requestId, failed ? "failed" : "completed", true,
            failed ? "EVENT_ACTION_FAILED" : null, SnapshotJson: snapshot));
    }

    private static bool EventIdle(RunState? run, Player? player, NEventRoom room) => UiIdle(run, player)
        && IsActive(room) && room.Layout != null && NGame.Instance?.Transition?.InTransition != true
        && !ReadUiField<List<Task>>(RunManager.Instance.EventSynchronizer, "_pendingOptionTasks").Any(t => !t.IsCompleted);
    private static bool EventButtonReady(NEventOptionButton button) => GodotObject.IsInstanceValid(button)
        && !button.IsQueuedForDeletion() && button.IsVisibleInTree() && button.IsNodeReady() && button.IsEnabled
        && button.MouseFilter == Control.MouseFilterEnum.Stop && !button.Option.IsLocked
        && ReadUiField<Tween?>(button, "_animInTween")?.IsRunning() != true;

    private static object EventTip(IHoverTip tip) => tip switch {
        HoverTip text => new { type = "text", id = tip.Id, title = text.Title, description = text.Description,
            entity_id = tip.CanonicalModel?.Id.ToString(), supported = true },
        CardHoverTip card => new { type = "card", id = tip.Id, card = TooltipCard(card.Card), supported = true },
        _ => new { type = tip.GetType().Name, id = tip.Id, entity_id = tip.CanonicalModel?.Id.ToString(), supported = false }
    };

    private static EventView ReadEvent(RunState? run, Player? player, bool bind)
    {
        EventBindings.Clear();
        var room = NEventRoom.Instance;
        if (room == null || !GodotObject.IsInstanceValid(room) || !room.IsNodeReady() || room.IsQueuedForDeletion())
            return new(null, null, null, null, null, false, false, false, []);
        var model = ReadUiField<EventModel>(room, "_event");
        var layout = room.Layout;
        var idle = EventIdle(run, player, room);
        var options = new List<object>();
        var ready = false;
        if (layout != null && layout.IsNodeReady())
        {
            foreach (var button in layout.OptionButtons.Where(b => b.IsNodeReady() && !b.IsQueuedForDeletion()))
            {
                var option = button.Option;
                var enabled = idle && EventButtonReady(button);
                var tips = option.HoverTips.ToArray();
                var actionId = "event_" + Id(room) + "_" + Id(button);
                options.Add(new { option_id = Id(option), text_key = option.TextKey,
                    title = option.Title.GetFormattedText(), description = option.Description.GetFormattedText(),
                    hover_tips = tips.Select(EventTip).ToArray(), hover_tips_complete = tips.All(t => t is HoverTip or CardHoverTip),
                    locked = option.IsLocked, proceed = option.IsProceed,
                    will_kill_player = player != null && (option.WillKillPlayer?.Invoke(player) ?? false),
                    relic_id = option.Relic?.Id.ToString(), available = enabled, action_id = enabled ? actionId : null });
                if (enabled)
                {
                    ready = true;
                    if (bind) EventBindings[actionId] = new(room, button, option, model, Id(run!));
                }
            }
        }
        return new(model.Id.ToString(), model.Title.GetFormattedText(), model.Description?.GetFormattedText(),
            Id(room), model.LayoutType.ToString(), layout != null, model.IsFinished, ready, options.ToArray(),
            model.DynamicVars.Select(kv => (object)new { name = kv.Key, kind = kv.Value.GetType().Name,
                value = kv.Value is StringVar text ? (object)text.StringValue : kv.Value.BaseValue }).ToArray(),
            RuntimeFields.Read(model));
    }
    private static LegalAction[] EventActions() => EventBindings.Select(p => new LegalAction(p.Key,
        p.Value.Option.IsProceed ? "proceed_event" : "choose_event_option", null, [], true,
        new("event_option", p.Value.Model.Id.ToString(), Id(p.Value.Option)))).ToArray();

    private static void DispatchEvent(EventBinding binding)
    {
        activeEvent = binding;
        var pending = ReadUiField<List<Task>>(RunManager.Instance.EventSynchronizer, "_pendingOptionTasks");
        var previous = pending.ToHashSet();
        // Normal button release goes through EventSynchronizer, choice history and BeforeChosen.
        binding.Button.ForceClick();
        eventTasks = pending.Where(t => !previous.Contains(t)).ToArray();
    }
    private static void ObserveEventCompletion()
    {
        if (activeRequest == null || activeEvent == null || tick <= submittedTick) return;
        var binding = activeEvent;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var combat = CombatManager.Instance.IsInProgress ? CombatManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        if (current is MegaCrit.Sts2.Core.Nodes.Screens.NRewardsScreen
            && run != null && Id(run) == binding.RunId && !eventTasks.Any(t => t.IsFaulted || t.IsCanceled)
            && ReadRewards(run, player, false).DecisionReady)
        {
            suspendedEventRequest = activeRequest;
            suspendedEvent = activeEvent;
            suspendedEventTasks = eventTasks;
            activeRequest = null; activeEvent = null; eventTasks = [];
            Ledger.Update(new(suspendedEventRequest.RequestId, "awaiting_choice", true,
                Message: "Event opened rewards; use listed child actions, then query this original event request."), release: true);
            Capture();
            Ledger.Update(new(suspendedEventRequest.RequestId, "awaiting_choice", true, SnapshotJson: snapshot));
            return;
        }
        if (current is MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NCardGridSelectionScreen child
            && SelectionIdle(run, player, child))
        {
            pendingSelectionEffects = eventTasks;
            FinishEvent("completed", message: "Event opened a card selector; choose one listed action from a fresh snapshot.");
            return;
        }
        var roomActive = IsActive(binding.Room);
        var buttons = roomActive ? binding.Room.Layout?.OptionButtons.Where(EventButtonReady).ToArray() ?? [] : [];
        var changed = !roomActive || buttons.Length > 0 && !buttons.Any(b => ReferenceEquals(b, binding.Button));
        var nextReady = roomActive && buttons.Length > 0
            || NMapScreen.Instance is { IsOpen: true } map && IsActive(map) && map.IsTravelEnabled
            || CanDecide(run, combat, player);
        var idle = !RunManager.Instance.IsPaused && !RunManager.Instance.ActionExecutor.IsRunning
            && RunManager.Instance.ActionQueueSet.IsEmpty && NGame.Instance?.Transition?.InTransition != true;
        var unsupported = current != null && !ReferenceEquals(current, binding.Room)
            && current is not NMapScreen && !CanDecide(run, combat, player)
            && !(current is MegaCrit.Sts2.Core.Nodes.Screens.CardSelection.NCardGridSelectionScreen opening
                && SupportedSelection(opening));
        // Combat startup is a supported transition; wait for its first decision rather than report an overlay.
        if (combat != null && current is NCombatRoom) unsupported = false;
        var outcome = EventRules.Evaluate(new(run != null && Id(run) == binding.RunId,
            eventTasks.Any(t => t.IsFaulted || t.IsCanceled), eventTasks.All(t => t.IsCompletedSuccessfully),
            changed, nextReady, idle, unsupported, NModalContainer.Instance?.OpenModal != null, tick - submittedTick > 180));
        switch (outcome)
        {
            case EventOutcome.Completed: FinishEvent("completed"); break;
            case EventOutcome.Failed: FinishEvent("failed", "EVENT_ACTION_FAILED"); break;
            case EventOutcome.NeedsUserInput:
                Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_EVENT_FLOW",
                    "This event sub-selection needs a backend adapter; query this original request ID without replaying it.", snapshot)); break;
        }
    }
    private static void FinishEvent(string status, string? error = null, string? message = null)
    {
        var requestId = activeRequest!.RequestId;
        Ledger.Update(new(requestId, status, true, error, message), release: true);
        activeRequest = null; activeEvent = null; eventTasks = [];
        Capture();
        Ledger.Update(new(requestId, status, true, error, message, SnapshotJson: snapshot));
    }
}
