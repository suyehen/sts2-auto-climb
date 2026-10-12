namespace AutoSlayBridge;

internal static class MenuRules
{
    public static bool CanEmbark(bool hasSavedRun, bool singleplayer, bool idle, bool unlockedSelection)
        => !hasSavedRun && singleplayer && idle && unlockedSelection;
}
