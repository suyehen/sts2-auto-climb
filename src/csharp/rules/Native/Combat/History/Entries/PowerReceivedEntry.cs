using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Entities.Players;

namespace AutoSlay.Rules.Native.Combat.History.Entries;

/// <summary>Native power-received fields and turn check; other history families/queries are absent.</summary>
public sealed class PowerReceivedEntry : CombatHistoryEntry
{
    public PowerModel Power { get; }
    public decimal Amount { get; }
    public Creature? Applier { get; }
    public PowerReceivedEntry(PowerModel power, decimal amount, Creature? applier, int roundNumber, CombatSide currentSide, CombatHistory history, IEnumerable<Player> players)
        : base(power.Owner, roundNumber, currentSide, history, players)
    {
        Power = power; Amount = amount; Applier = applier;
    }
}
