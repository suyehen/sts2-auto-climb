using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Entities.Multiplayer;
using MegaCrit.Sts2.Core.Multiplayer;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;

namespace AutoSlayBridge;

public static partial class Bridge
{
    private sealed record MenuView(string? Phase, string? ContextId, bool DecisionReady,
        bool HasSavedRun, bool SaveLoadable, string? SelectedCharacter, int? Ascension, int? MaxAscension,
        object[] Characters, string? NewRunBlockedReason);
    private sealed record MenuBinding(Node Screen, Node Control, string Kind, Func<Task?> Dispatch, Func<bool> Completed);
    private static readonly Dictionary<string, MenuBinding> MenuBindings = new();
    private static MenuBinding? activeMenu;
    private static Task? menuTask;

    private static bool MenuIdle() => !RunManager.Instance.IsInProgress && !RunManager.Instance.IsPaused
        && NGame.Instance?.Transition?.InTransition != true && NModalContainer.Instance?.OpenModal == null;

    private static bool ButtonAvailable(NButton button) => GodotObject.IsInstanceValid(button)
        && !button.IsQueuedForDeletion() && button.IsEnabled && button.IsVisibleInTree();

    private static bool ContinueSaveLoadable(NMainMenu main)
    {
        var result = ReadUiField<object?>(main, "_readRunSaveResult");
        if (result == null) return false;
        return result.GetType().GetProperty("Success")?.GetValue(result) is true
            && result.GetType().GetProperty("SaveData")?.GetValue(result) != null;
    }

    private static MenuView ReadMenu(bool bind)
    {
        MenuBindings.Clear();
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = current as Node;
        var idle = screen != null && IsActive(screen) && MenuIdle();
        var hasSave = SaveManager.Instance.HasRunSave;
        var loadable = false;
        var ready = false;
        string? phase = null;
        string? selected = null;
        int? ascension = null, maximum = null;
        var characters = new List<object>();

        void BindButton(string actionId, NButton button, string kind, Func<bool> completed, Func<Task?>? dispatch = null)
        {
            if (!idle || !ButtonAvailable(button)) return;
            ready = true;
            if (bind) MenuBindings[actionId] = new(screen!, button, kind,
                dispatch ?? (() => { button.ForceClick(); return null; }), completed);
        }

        if (screen is NMainMenu main)
        {
            phase = "main_menu";
            loadable = ContinueSaveLoadable(main);
            if (hasSave)
                BindButton("abandon_run", ReadUiField<NMainMenuTextButton>(main, "_abandonRunButton"), "abandon_run",
                    () => !SaveManager.Instance.HasRunSave,
                    () => { main.AbandonRun(); return null; });
            if (hasSave && loadable)
            {
                var button = ReadUiField<NMainMenuTextButton>(main, "_continueButton");
                BindButton("continue_game", button, "continue_game", () => RunManager.Instance.IsInProgress,
                    () => {
                        var method = typeof(NMainMenu).GetMethod("OnContinueButtonPressedAsync", BindingFlags.Instance | BindingFlags.NonPublic)
                            ?? throw new MissingMethodException("NMainMenu.OnContinueButtonPressedAsync");
                        return (Task)method.Invoke(main, null)!;
                    });
            }
            if (!hasSave)
                BindButton("new_game", ReadUiField<NMainMenuTextButton>(main, "_singleplayerButton"), "new_game",
                    () => !ReferenceEquals(ActiveScreenContext.Instance.GetCurrentScreen(), main));
        }
        else if (screen is NSingleplayerSubmenu submenu)
        {
            phase = "singleplayer_menu";
            if (!hasSave)
                BindButton("standard_run", ReadUiField<NSubmenuButton>(submenu, "_standardButton"), "standard_run",
                    () => ActiveScreenContext.Instance.GetCurrentScreen() is NCharacterSelectScreen);
        }
        else if (screen is NCharacterSelectScreen selection && selection.Lobby != null
                 && selection.Lobby.NetService.Type == NetGameType.Singleplayer)
        {
            phase = "character_selection";
            var lobby = selection.Lobby;
            selected = lobby.LocalPlayer.character?.Id.ToString();
            ascension = lobby.Ascension;
            maximum = lobby.MaxAscension;
            var container = ReadUiField<Control>(selection, "_charButtonContainer");
            foreach (var button in container.GetChildren().OfType<NCharacterSelectButton>())
            {
                if (button.IsQueuedForDeletion() || !button.IsVisibleInTree()) continue;
                var character = button.Character;
                var available = idle && !hasSave && !button.IsLocked && ButtonAvailable(button);
                var actionId = "character_" + Id(button);
                characters.Add(new { entity_id = character.Id.ToString(), unlocked = !button.IsLocked,
                    selected = button.IsSelected, random = button.IsRandom, available,
                    starting_hp = button.IsLocked || button.IsRandom ? (int?)null : character.StartingHp,
                    starting_gold = button.IsLocked || button.IsRandom ? (int?)null : character.StartingGold,
                    action_id = available && !button.IsSelected ? actionId : null });
                if (available && !button.IsSelected)
                {
                    ready = true;
                    if (bind) MenuBindings[actionId] = new(selection, button, "select_character", () => {
                        button.Select(); return null;
                    }, () => button.IsSelected && selection.Lobby.LocalPlayer.character == character);
                }
            }
            var panel = ReadUiField<NAscensionPanel>(selection, "_ascensionPanel");
            if (!hasSave && lobby.Ascension > 0)
            {
                var prior = lobby.Ascension;
                BindButton("ascension_down", ReadUiField<NButton>(panel, "_leftArrow"), "ascension_down", () => selection.Lobby.Ascension == prior - 1);
            }
            if (!hasSave && lobby.Ascension < lobby.MaxAscension)
            {
                var prior = lobby.Ascension;
                BindButton("ascension_up", ReadUiField<NButton>(panel, "_rightArrow"), "ascension_up", () => selection.Lobby.Ascension == prior + 1);
            }
            if (MenuRules.CanEmbark(hasSave, lobby.NetService.Type == NetGameType.Singleplayer,
                    idle, lobby.LocalPlayer.character != null && ReadUiField<NCharacterSelectButton?>(selection, "_selectedButton") is { IsLocked: false }))
                BindButton("embark", ReadUiField<NConfirmButton>(selection, "_embarkButton"), "embark",
                    () => RunManager.Instance.IsInProgress && NGame.Instance?.MainMenu == null);
        }
        return new(phase, phase == null ? null : "menu_" + Id(screen!), ready, hasSave, loadable,
            selected, ascension, maximum, characters.ToArray(), hasSave ? "EXISTING_RUN_MUST_BE_CONTINUED_OR_EXPLICITLY_ABANDONED" : null);
    }

    private static LegalAction[] MenuActions() => MenuBindings.Select(pair => new LegalAction(pair.Key, pair.Value.Kind, null, [], true)).ToArray();

    private static void ObserveMenuCompletion()
    {
        if (activeRequest == null || activeMenu == null || tick <= submittedTick) return;
        if (menuTask?.IsFaulted == true || menuTask?.IsCanceled == true)
        {
            FinishMenu("failed", "MENU_ACTION_FAILED", menuTask?.Exception?.GetBaseException().Message); return;
        }
        var modal = NModalContainer.Instance?.OpenModal != null;
        var transition = NGame.Instance?.Transition?.InTransition == true;
        if (!modal && !transition && (menuTask == null || menuTask.IsCompletedSuccessfully) && activeMenu.Completed())
            FinishMenu("completed");
        else if (modal || tick - submittedTick > 180)
            Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_MENU_FLOW",
                "Resolve the load error/tutorial or unsupported menu manually, then query this request ID.", snapshot));
    }

    private static void FinishMenu(string status, string? error = null, string? message = null)
    {
        var requestId = activeRequest!.RequestId;
        Ledger.Update(new(requestId, status, true, error, message), release: true);
        activeRequest = null; activeMenu = null; menuTask = null;
        Capture();
        Ledger.Update(new(requestId, status, true, error, message, snapshot));
    }
}
