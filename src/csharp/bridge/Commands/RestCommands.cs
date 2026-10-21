using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.RestSite;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Runs;

namespace AutoSlayBridge;
public static partial class Bridge
{
    private sealed record RestView(string? ScreenId, bool DecisionReady, object[] Options, string? ProceedActionId);
    private sealed record RestBinding(NRestSiteRoom Room, NRestSiteButton? Button, string RunId);
    private static readonly Dictionary<string, RestBinding> RestBindings = new();
    private static RestBinding? activeRest;
    private static Task? restTask;
    private static bool RestIdle(RunState? run, Player? player, NRestSiteRoom room) => UiIdle(run, player)
        && IsActive(room) && room.IsNodeReady() && NGame.Instance?.Transition?.InTransition != true
        && !ReadUiField<bool>(room, "_roomExiting")
        && ReadUiField<Tween?>(room, "_choicesTween")?.IsRunning() != true;
    private static bool RestButtonReady(NRestSiteButton button) => SelectionButtonReady(button)
        && button.MouseFilter == Control.MouseFilterEnum.Stop && button.Option.IsEnabled
        && !ReadUiField<bool>(button, "_isUnclickable") && !ReadUiField<bool>(button, "_executingOption");
    private static RestView ReadRest(RunState? run, Player? player, bool bind)
    {
        RestBindings.Clear();
        var room = ActiveScreenContext.Instance.GetCurrentScreen() as NRestSiteRoom;
        if (room == null || !room.IsNodeReady()) return new(null, false, [], null);
        var idle = RestIdle(run, player, room);
        var options = new List<object>();
        var ready = false;
        var container = ReadUiField<Control>(room, "_choicesContainer");
        foreach (var button in container.GetChildren().OfType<NRestSiteButton>().Where(b => b.IsNodeReady() && !b.IsQueuedForDeletion()))
        {
            var option = button.Option;
            // Every option uses the same game-owned SelectOption callback.
            var supported = option is HealRestSiteOption or SmithRestSiteOption
                or LiftRestSiteOption or DigRestSiteOption or KindleRestSiteOption
                or HatchRestSiteOption or CookRestSiteOption or CloneRestSiteOption or MendRestSiteOption;
            var enabled = idle && supported && RestButtonReady(button);
            var id = "rest_" + Id(room) + "_" + Id(button);
            options.Add(new { option_id = option.OptionId, title = option.Title.GetFormattedText(),
                description = option.Description.GetFormattedText(), model_type = option.GetType().FullName,
                heal_amount = option is HealRestSiteOption && player != null ? (decimal?)HealRestSiteOption.GetHealAmount(player) : null,
                smith_count = option is SmithRestSiteOption smith ? (int?)smith.SmithCount : null,
                selection_purpose = option is SmithRestSiteOption ? "upgrade" : null,
                supported, available = enabled, action_id = enabled ? id : null });
            if (enabled)
            {
                ready = true;
                if (bind) RestBindings[id] = new(room, button, Id(run!));
            }
        }
        var proceed = idle && SelectionButtonReady(room.ProceedButton);
        var proceedId = proceed ? "proceed_rest_" + Id(room) : null;
        if (proceed)
        {
            ready = true;
            if (bind) RestBindings[proceedId!] = new(room, null, Id(run!));
        }
        return new(Id(room), ready, options.ToArray(), proceedId);
    }
    private static LegalAction[] RestActions() => RestBindings.Select(p => new LegalAction(p.Key,
        p.Value.Button == null ? "proceed_rest" : "choose_rest_option", null, [], true,
        new(p.Value.Button == null ? "rest_screen" : "rest_option", null, Id((object?)p.Value.Button ?? p.Value.Room)))).ToArray();
    private static void DispatchRest(RestBinding binding)
    {
        activeRest = binding;
        if (binding.Button == null) { binding.Room.ProceedButton.ForceClick(); restTask = null; }
        else restTask = (Task)(typeof(NRestSiteButton).GetMethod("SelectOption", BindingFlags.Instance | BindingFlags.NonPublic)
            ?? throw new MissingMethodException("NRestSiteButton.SelectOption")).Invoke(binding.Button, [binding.Button.Option])!;
    }
    private static void ObserveRestCompletion()
    {
        if (activeRequest == null || activeRest == null || tick <= submittedTick) return;
        var binding = activeRest;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        if (run == null || Id(run) != binding.RunId || restTask?.IsFaulted == true || restTask?.IsCanceled == true)
            { FinishRest("failed", "REST_ACTION_FAILED"); return; }
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        if (current is NCardGridSelectionScreen child && SelectionIdle(run, player, child))
        {
            pendingSelectionEffects = restTask == null ? [] : [restTask];
            FinishRest("completed", message: "Rest option opened a card selector; resolve its selection separately."); return;
        }
        var nextReady = binding.Button == null
            ? current is NMapScreen map && IsActive(map) && map.IsTravelEnabled
            : RestIdle(run, player, binding.Room) && (SelectionButtonReady(binding.Room.ProceedButton)
                || ReadUiField<Control>(binding.Room, "_choicesContainer").GetChildren().OfType<NRestSiteButton>().Any(RestButtonReady));
        if ((restTask == null || restTask.IsCompletedSuccessfully) && nextReady)
            { FinishRest("completed"); return; }
        if (NModalContainer.Instance?.OpenModal != null || tick - submittedTick > 180)
            Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_REST_FLOW",
                "Resolve the rest tutorial or unsupported selection, then query this request ID.", snapshot));
    }
    private static void FinishRest(string status, string? error = null, string? message = null)
    {
        var id = activeRequest!.RequestId;
        Ledger.Update(new(id, status, true, error, message), release: true);
        activeRequest = null; activeRest = null; restTask = null;
        Capture(); Ledger.Update(new(id, status, true, error, message, snapshot));
    }
}
