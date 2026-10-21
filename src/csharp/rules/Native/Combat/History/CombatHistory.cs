using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Combat.History.Entries;
using AutoSlay.Rules.Native.Entities.Players;

namespace AutoSlay.Rules.Native.Combat.History;

/// <summary>Explicit power-received and resource history domains; other native entry families are absent.</summary>
public sealed class CombatHistory
{
    private readonly List<PowerReceivedEntry> powerReceipts = [];
    private bool observed;
    private bool resourcesObserved;
    private readonly List<CombatHistoryEntry> resourceEntries = [];
    public IReadOnlyList<CombatHistoryEntry> ResourceEntries => resourcesObserved ? resourceEntries.AsReadOnly()
        : throw new RuleBoundaryException("state_incomplete", "Combat resource history was not observed");
    public IReadOnlyList<PowerReceivedEntry> PowerReceipts => observed ? powerReceipts.AsReadOnly()
        : throw new RuleBoundaryException("state_incomplete", "Combat power-received history was not observed");
    public event Action? Changed;
    internal bool IsObserved => observed || resourcesObserved;
    internal void ObserveEmpty()
    {
        if (observed) throw new RuleBoundaryException("invalid_state", "Power history was already restored");
        observed = true;
    }
    internal void RequireObserved(CombatState combat)
    {
        _ = PowerReceipts; RequireEntryContext(combat);
    }
    internal void ObserveEmptyResources()
    {
        if (resourcesObserved) throw new RuleBoundaryException("invalid_state", "Resource history was already restored");
        resourcesObserved = true;
    }
    internal void RequireResourcesObserved(CombatState combat)
    { _ = ResourceEntries; RequireEntryContext(combat); }
    private static void RequireEntryContext(CombatState combat)
    {
        _ = combat.RequireTimeline();
        foreach (var player in combat.Players)
            if (player.ReadObservedCombatTurn() == null) throw new RuleBoundaryException("invalid_state", "Native history requires player combat state");
    }
    public void PowerReceived(CombatState combat, PowerModel power, decimal amount, Creature? applier)
    {
        RequireObserved(combat);
        powerReceipts.Add(new(power, amount, applier, combat.RoundNumber, combat.CurrentSide, this, combat.Players)); Changed?.Invoke();
    }
    public void EnergySpent(CombatState combat, int amount, Player player)
    {
        RequireResourcesObserved(combat);
        resourceEntries.Add(new EnergySpentEntry(amount, player, combat.RoundNumber, combat.CurrentSide, this, combat.Players)); Changed?.Invoke();
    }
    public void StarsModified(CombatState combat, int amount, Player player)
    {
        RequireResourcesObserved(combat);
        resourceEntries.Add(new StarsModifiedEntry(amount, player, combat.RoundNumber, combat.CurrentSide, this, combat.Players)); Changed?.Invoke();
    }
}
public sealed record CombatTimeline(int RoundNumber, CombatSide CurrentSide, IReadOnlyDictionary<ulong, int> PlayerTurnNumbers);
