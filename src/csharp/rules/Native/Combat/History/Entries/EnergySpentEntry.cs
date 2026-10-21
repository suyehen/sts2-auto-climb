using AutoSlay.Rules.Native.Entities.Players;

namespace AutoSlay.Rules.Native.Combat.History.Entries;

/// <summary>Native resource history fields and turn identity; display formatting is omitted.</summary>
public sealed class EnergySpentEntry : CombatHistoryEntry
{
    public int Amount { get; }
    public EnergySpentEntry(int amount, Player player, int roundNumber, CombatSide currentSide, CombatHistory history, IEnumerable<Player> players)
        : base(player.Creature, roundNumber, currentSide, history, players) => Amount = amount;
}
