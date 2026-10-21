using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Hooks;
using AutoSlay.Rules.Native.Models;

namespace AutoSlay.Rules.Native.Entities.Players;

/// <summary>
/// Observed native energy/stars and five-pile state. Presence does not invent resource values,
/// orb capacity, phase or a turn number. Resource history must be observed before star mutation.
/// </summary>
public class PlayerCombatState
{
    private int? energy;
    private int? stars;
    private readonly Player player;
    public int Stars
    {
        get => stars ?? throw new RuleBoundaryException("state_incomplete", "Player stars were not observed");
        set
        {
            var old = Stars;
            if (old == value) return;
            var combat = player.Creature.CombatState ?? throw new RuleBoundaryException("state_incomplete", "Star mutation requires owned combat context");
            combat.History.RequireResourcesObserved(combat);
            stars = value;
            combat.History.StarsModified(combat, value - old, player);
            StarsChanged?.Invoke(old, value);
        }
    }
    private CardPile[]? piles;
    public CardPile Hand { get; } = new(PileType.Hand);
    public CardPile DrawPile { get; } = new(PileType.Draw);
    public CardPile DiscardPile { get; } = new(PileType.Discard);
    public CardPile ExhaustPile { get; } = new(PileType.Exhaust);
    public CardPile PlayPile { get; } = new(PileType.Play);
    public IReadOnlyList<CardPile> AllPiles => Array.AsReadOnly(piles ??= [Hand, DrawPile, DiscardPile, ExhaustPile, PlayPile]);
    public IEnumerable<AutoSlay.Rules.Native.Models.CardModel> AllCards => AllPiles.SelectMany(p => p.Cards);
    public int Energy
    {
        get => energy ?? throw new RuleBoundaryException("state_incomplete", "Player energy was not observed");
        set
        {
            var old = Energy;
            if (old != value) { energy = value; EnergyChanged?.Invoke(old, value); }
        }
    }
    public event Action<int, int>? EnergyChanged;
    public event Action<int, int>? StarsChanged;
    internal PlayerCombatState(Player player)
    { this.player = player; foreach (var pile in AllPiles) pile.Bind(player); }
    internal void RestoreEnergy(int observed) => energy = observed;
    internal void RestoreStars(int observed) => stars = observed;
    public void LoseEnergy(decimal amount)
    {
        if (amount < 0) throw new ArgumentException("Must not be negative.", nameof(amount));
        Energy = (int)Math.Clamp((decimal)Energy - amount, 0m, 999999999m);
    }
    public void GainEnergy(decimal amount)
    {
        if (amount < 0) throw new ArgumentException("Must not be negative.", nameof(amount));
        Energy = (int)Math.Clamp((decimal)Energy + amount, 0m, 999999999m);
    }
    public void LoseStars(decimal amount)
    {
        if (amount < 0) throw new ArgumentException("Must not be negative.", nameof(amount));
        Stars = (int)Math.Max((decimal)Stars - amount, 0m);
    }
    public void GainStars(decimal amount)
    {
        if (amount < 0) throw new ArgumentException("Must not be negative.", nameof(amount));
        Stars = (int)Math.Max((decimal)Stars + amount, 0m);
    }
    public bool HasEnoughResourcesFor(CardModel card, out UnplayableReason reason)
    {
        if (card.World != player.RunState.World || !ReferenceEquals(card.Owner, player))
            throw new RuleBoundaryException("foreign_state", "Resource query card belongs to another player/world");
        var energyCost = Math.Max(0, card.EnergyCost.GetWithModifiers(CostModifiers.All));
        var starCost = Math.Max(0, card.GetStarCostWithModifiers());
        if (energyCost > Energy && card.CombatState != null && Hook.ShouldPayExcessEnergyCostWithStars(card.CombatState, player))
        { starCost += (energyCost - Energy) * 2; energyCost = Energy; }
        reason = UnplayableReason.None;
        if (energyCost > Energy) reason |= UnplayableReason.EnergyCostTooHigh;
        if (starCost > Stars) reason |= UnplayableReason.StarCostTooHigh;
        return reason == UnplayableReason.None;
    }
}
