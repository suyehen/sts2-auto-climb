using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;
using AutoSlay.Rules.Native.Rooms;
using AutoSlay.Rules.Native.Models.Singleton;

namespace AutoSlay.Rules.Native.Runs;

public enum ObservationStatus { Unobserved, ObservedAbsent, ObservedPresent }

public sealed class RunState
{
    private readonly List<Player> players = [];
    public SimulationWorld World { get; }
    public IReadOnlyList<Player> Players => players.AsReadOnly();
    public RoomState? CurrentRoom { get; internal set; }
    public int TotalFloor { get; internal set; }
    internal int? ObservedActIndex { get; set; }
    public int CurrentActIndex => ObservedActIndex ?? throw new RuleBoundaryException("state_incomplete", "Run act index was not observed");
    public MultiplayerScalingModel? MultiplayerScalingModel { get; internal set; }
    public MapPointHistoryEntry? CurrentMapPointHistoryEntry { get; internal set; }
    public ObservationStatus HistoryStatus { get; internal set; }
    // This is an observed projection of native iteration order, not an inferred listener whitelist.
    // A full state hydrator must populate it; null is unknown, and cannot become an empty listener set.
    internal string[]? ObservedHookOrder { get; set; }
    internal RunState(SimulationWorld world) => World = world;
    internal void Add(Player player) => players.Add(player);
    internal void RequireHistory()
    {
        if (HistoryStatus == ObservationStatus.Unobserved)
            throw new RuleBoundaryException("state_incomplete", "run.current_map_point_history_entry was not observed");
    }
    public IEnumerable<AbstractModel> IterateHookListeners(CombatState? combatState)
    {
        if (combatState?.HookInventory is { } inventory)
            return inventory.RunListeners().Concat(combatState.IterateHookListeners());
        // Full run/combat iteration has not been ported. An explicitly supplied, validated ordered
        // projection is usable in offline fixtures; production hydration does not yet expose it.
        if (ObservedHookOrder == null || combatState != null && combatState.ObservedHookOrder == null)
            throw new RuleBoundaryException("state_incomplete", "Complete ordered run/combat hook inventory required");
        return ObservedHookOrder.Concat(combatState?.ObservedHookOrder ?? [])
            .Select(World.RequireModel).ToArray();
    }
}

/// <summary>Observed room identity/state context only; no room entry, reward or event flow is implemented.</summary>
public sealed class RoomState(string instanceId, RoomType type)
{
    public string InstanceId { get; } = instanceId;
    public RoomType Type { get; } = type;
    internal RoomState Clone() => new(InstanceId, Type);
}

/// <summary>Initial resource-history projection. Other native history fields are not defaulted or claimed supported.</summary>
public sealed class PlayerMapPointHistoryEntry(ulong playerId)
{
    public ulong PlayerId { get; } = playerId;
    public int HpHealed { get; internal set; }
    public int MaxHpGained { get; internal set; }
    public int MaxHpLost { get; internal set; }
    private readonly List<ModelId> upgradedCards = [];
    private readonly List<ModelId> downgradedCards = [];
    internal bool CardUpgradeHistoryObserved { get; set; }
    public IReadOnlyList<ModelId> UpgradedCards { get { RequireCardUpgradeHistory(); return upgradedCards.AsReadOnly(); } }
    public IReadOnlyList<ModelId> DowngradedCards { get { RequireCardUpgradeHistory(); return downgradedCards.AsReadOnly(); } }
    internal void RequireCardUpgradeHistory()
    { if (!CardUpgradeHistoryObserved) throw new RuleBoundaryException("state_incomplete", "Map-entry upgrade/downgrade history was not observed"); }
    internal void RecordCardUpgrade(ModelId card, bool downgrade)
    { RequireCardUpgradeHistory(); (downgrade ? downgradedCards : upgradedCards).Add(card); }
    internal PlayerMapPointHistoryEntry Clone() => new(PlayerId) { HpHealed = HpHealed, MaxHpGained = MaxHpGained, MaxHpLost = MaxHpLost };
}
public sealed class MapPointHistoryEntry
{
    private readonly Dictionary<ulong, PlayerMapPointHistoryEntry> stats;
    public IReadOnlyCollection<PlayerMapPointHistoryEntry> PlayerStats => stats.Values;
    public MapPointHistoryEntry(IEnumerable<ulong> ids) => stats = ids.ToDictionary(id => id, id => new PlayerMapPointHistoryEntry(id));
    public PlayerMapPointHistoryEntry GetEntry(ulong playerId) => stats.TryGetValue(playerId, out var value) ? value
        : throw new RuleBoundaryException("state_incomplete", "Missing player history entry: " + playerId);
    internal MapPointHistoryEntry Clone()
    {
        var copy = new MapPointHistoryEntry([]);
        foreach (var entry in stats) copy.stats.Add(entry.Key, entry.Value.Clone());
        return copy;
    }
}
