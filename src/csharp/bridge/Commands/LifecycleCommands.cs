using Godot;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using MegaCrit.Sts2.Core.Runs;

namespace AutoSlayBridge;
public static partial class Bridge
{
    private static readonly Dictionary<string, string> LifecycleBindings = new();
    private static string? activeLifecycle;
    private static Task? lifecycleTask;
    private static bool quitScheduled;
    private static bool CanReturnToMenu(RunState? run, CombatState? combat, Player? player)
    {
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        return !quitScheduled && run?.Players.Count == 1 && !run.IsGameOver
            && (CanDecide(run, combat, player) || UiIdle(run, player)
                && NGame.Instance?.Transition?.InTransition != true && current is Node screen && IsActive(screen)
                && current is NMapScreen or NEventRoom or NRestSiteRoom or NMerchantRoom or NMerchantInventory or NRewardsScreen or NTreasureRoom);
    }
    private static bool CanQuitGame() => !quitScheduled && MenuIdle()
        && ActiveScreenContext.Instance.GetCurrentScreen() is NMainMenu main && IsActive(main)
        && ButtonAvailable(ReadUiField<MegaCrit.Sts2.Core.Nodes.Screens.MainMenu.NMainMenuTextButton>(main, "_quitButton"));
    private static bool CanCloseMap(RunState? run, Player? player)
    {
        var map = NMapScreen.Instance;
        return map != null && RecoveryRules.CanCloseMap(new(run?.Players.Count == 1 && !run.IsGameOver && CombatManager.Instance.IsInProgress,
            map.IsOpen, IsActive(map), map.IsTravelEnabled, map.IsTraveling,
            NGame.Instance?.Transition?.InTransition == true,
            !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty,
            NModalContainer.Instance?.OpenModal != null, player?.Creature.CurrentHp > 0));
    }
    private static bool GameOverButtonReady(string kind, RunState? run)
    {
        if (run?.Players.Count != 1 || ActiveScreenContext.Instance.GetCurrentScreen() is not NGameOverScreen ended
            || !IsActive(ended) || NGame.Instance?.Transition?.InTransition == true || NModalContainer.Instance?.OpenModal != null)
            return false;
        return kind == "continue_game_over"
            ? SelectionButtonReady(ReadUiField<NGameOverContinueButton>(ended, "_continueButton"))
            : SelectionButtonReady(ReadUiField<NReturnToMainMenuButton>(ended, "_mainMenuButton"));
    }
    private static bool LifecycleReady(string kind, RunState? run, CombatState? combat, Player? player) => kind switch
    {
        "quit_game" => CanQuitGame(), "close_map_overlay" => CanCloseMap(run, player),
        "continue_game_over" or "return_after_game_over" => GameOverButtonReady(kind, run),
        "return_to_main_menu" => CanReturnToMenu(run, combat, player), _ => false
    };
    private static LegalAction[] BuildLifecycleActions(RunState? run, CombatState? combat, Player? player, bool bind)
    {
        LifecycleBindings.Clear();
        if (!bind) return [];
        if (CanReturnToMenu(run, combat, player)) LifecycleBindings["return_to_main_menu"] = "return_to_main_menu";
        if (CanQuitGame()) LifecycleBindings["quit_game"] = "quit_game";
        foreach (var kind in new[] { "close_map_overlay", "continue_game_over", "return_after_game_over" })
            if (LifecycleReady(kind, run, combat, player)) LifecycleBindings[kind] = kind;
        return LifecycleBindings.Keys.Select(id => new LegalAction(id, id, null, [], true)).ToArray();
    }
    private static void DispatchLifecycle(string kind)
    {
        activeLifecycle = kind;
        if (kind == "return_to_main_menu") lifecycleTask = NGame.Instance!.ReturnToMainMenu();
        else if (kind == "close_map_overlay") { NMapScreen.Instance!.Close(); lifecycleTask = null; }
        else if (kind is "continue_game_over" or "return_after_game_over")
        {
            if (ActiveScreenContext.Instance.GetCurrentScreen() is not NGameOverScreen ended)
                throw new InvalidOperationException("Game-over screen changed before dispatch.");
            if (kind == "continue_game_over") ReadUiField<NGameOverContinueButton>(ended, "_continueButton").ForceClick();
            else ReadUiField<NReturnToMainMenuButton>(ended, "_mainMenuButton").ForceClick();
            lifecycleTask = null;
        }
        else
        {
            // Allow the caller to receive the scheduling receipt before HTTP disappears.
            quitScheduled = true;
            ((SceneTree)Engine.GetMainLoop()).CreateTimer(1.5).Timeout += () => NGame.Instance!.Quit();
            lifecycleTask = null;
        }
    }
    private static void ObserveLifecycleCompletion()
    {
        if (activeRequest == null || activeLifecycle == null || tick <= submittedTick) return;
        if (lifecycleTask?.IsFaulted == true || lifecycleTask?.IsCanceled == true)
            { FinishLifecycle("failed", "LIFECYCLE_FAILED"); return; }
        if (activeLifecycle == "close_map_overlay")
        {
            if (NMapScreen.Instance?.IsOpen != true && CombatManager.Instance.IsInProgress
                && !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty)
                FinishLifecycle("completed", message: "Closed map overlay; re-read combat state.");
            else if (tick - submittedTick > 180) FinishLifecycle("failed", "MAP_OVERLAY_CLOSE_WAIT");
        }
        else if (activeLifecycle == "continue_game_over")
        {
            if (ActiveScreenContext.Instance.GetCurrentScreen() is NGameOverScreen ended
                && SelectionButtonReady(ReadUiField<NReturnToMainMenuButton>(ended, "_mainMenuButton")))
                FinishLifecycle("completed", message: "Game-over summary ready.");
            else if (tick - submittedTick > 180) FinishLifecycle("failed", "GAME_OVER_SUMMARY_WAIT");
        }
        else if (activeLifecycle == "return_after_game_over")
        {
            if (MenuIdle() && ActiveScreenContext.Instance.GetCurrentScreen() is NMainMenu main && IsActive(main))
                FinishLifecycle("completed", message: "Returned normally after game over.");
            else if (tick - submittedTick > 180) FinishLifecycle("failed", "GAME_OVER_RETURN_WAIT");
        }
        else if (activeLifecycle == "quit_game")
        {
            Ledger.SetPaused(true);
            FinishLifecycle("completed", message: "Normal exit scheduled. Verify OS process termination before deployment or restart.");
        }
        else if (lifecycleTask?.IsCompletedSuccessfully == true && MenuIdle()
            && ActiveScreenContext.Instance.GetCurrentScreen() is NMainMenu main && IsActive(main))
            FinishLifecycle("completed", message: "Returned to main menu; the game retains its normal room checkpoint.");
        else if (tick - submittedTick > 180)
            Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "LIFECYCLE_WAIT",
                "Normal menu transition did not settle. Query this request ID; do not force terminate.", snapshot));
    }
    private static void FinishLifecycle(string status, string? error = null, string? message = null)
    {
        var id = activeRequest!.RequestId;
        Ledger.Update(new(id, status, true, error, message), release: true);
        activeRequest = null; activeLifecycle = null; lifecycleTask = null;
        Capture(); Ledger.Update(new(id, status, true, error, message, snapshot));
    }
}
