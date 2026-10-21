using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Native.Combat.History;

/// <summary>Native history identity/time semantics. Pure formatting is not migrated.</summary>
public abstract class CombatHistoryEntry
{
    private readonly Dictionary<ulong, int> playerTurnNumbers = [];
    public Creature Actor { get; }
    private int RoundNumber { get; }
    private CombatSide CurrentSide { get; }
    public CombatHistory History { get; }
    protected CombatHistoryEntry(Creature actor, int roundNumber, CombatSide currentSide, CombatHistory history, IEnumerable<Player> players)
    {
        Actor = actor; RoundNumber = roundNumber; CurrentSide = currentSide; History = history;
        foreach (var player in players) playerTurnNumbers[player.NetId] = player.ReadObservedCombatTurn()
            ?? throw new RuleBoundaryException("invalid_state", "Native combat history requires player combat state");
    }
    public bool HappenedThisTurn(ICombatState? state)
    {
        if (state == null || state.RoundNumber != RoundNumber || state.CurrentSide != CurrentSide) return false;
        foreach (var entry in playerTurnNumbers)
        {
            var player = state.GetPlayer(entry.Key);
            if (player == null || player.ReadObservedCombatTurn() != entry.Value) return false;
        }
        return true;
    }
    public bool HappenedLastPlayerTurn(Player player)
    {
        if (!playerTurnNumbers.TryGetValue(player.NetId, out var saved)) return false;
        var turn = player.ReadObservedCombatTurn(); return turn.HasValue && saved == turn.Value - 1;
    }
}
