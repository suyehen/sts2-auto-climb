using AutoSlay.Rules.Native.Entities.Players;

namespace AutoSlay.Rules.Native.Combat.History.Entries;

/// <summary>Native signed actual star delta, actor and turn identity; display formatting is omitted.</summary>
public sealed class StarsModifiedEntry : CombatHistoryEntry
{
    public int Amount { get; }
    public StarsModifiedEntry(int amount, Player player, int roundNumber, CombatSide currentSide, CombatHistory history, IEnumerable<Player> players)
        : base(player.Creature, roundNumber, currentSide, history, players) => Amount = amount;
}
