using AutoSlay.Rules.Native.Entities.Players;

namespace AutoSlay.Rules.Native.Entities.Cards;

public static class PileTypeExtensions
{
    public static CardPile GetPile(this PileType pileType, Player player)
    {
        ArgumentNullException.ThrowIfNull(player);
        return CardPile.Get(pileType, player)
            ?? throw new InvalidOperationException($"Tried to get {pileType} pile while out of combat.");
    }
    public static bool IsCombatPile(this PileType pileType) => (uint)(pileType - 1) <= 4u;
}
