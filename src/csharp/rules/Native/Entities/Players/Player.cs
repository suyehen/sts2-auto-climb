using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Entities.Cards;

namespace AutoSlay.Rules.Native.Entities.Players;

public sealed class Player
{
    private readonly List<RelicModel> relics = [];
    private PlayerCombatState? combatState;
    internal ObservationStatus CombatStateStatus { get; private set; }
    internal PlayerCombatState? OwnedCombatState => combatState;
    internal string[]? ObservedDeckCards { get; set; }
    public CardPile Deck { get; } = new(PileType.Deck);
    public PlayerCombatState? PlayerCombatState => CombatStateStatus == ObservationStatus.Unobserved
        ? throw new RuleBoundaryException("state_incomplete", "Player combat-state presence was not observed") : combatState;
    public IEnumerable<CardPile> Piles => (PlayerCombatState?.AllPiles ?? Array.Empty<CardPile>()).Concat(new[] { Deck });
    public ulong NetId { get; }
    public Creature Creature { get; }
    public RunState RunState { get; }
    public bool IsActiveForHooks { get; internal set; }
    public IReadOnlyList<RelicModel> Relics => relics.AsReadOnly();
    internal Player(ulong id, Creature creature, RunState runState, bool active)
    { NetId = id; Creature = creature; RunState = runState; IsActiveForHooks = active; creature.Player = this; Deck.Bind(this); }
    internal void RestoreCombatPresence(ObservationStatus status)
    {
        if (!Enum.IsDefined(status)) throw new RuleBoundaryException("invalid_state", "Invalid combat-state observation status");
        if (status == ObservationStatus.Unobserved) return;
        if (CombatStateStatus != ObservationStatus.Unobserved && CombatStateStatus != status)
            throw new RuleBoundaryException("invalid_state", "Conflicting player combat-state presence observations");
        if (status == ObservationStatus.ObservedPresent) combatState ??= new(this);
        CombatStateStatus = status;
    }
    internal void RequireCardInventory()
    {
        if (Creature.CombatState?.HookInventory is { } inventory) { inventory.RequirePlayerCards(this); return; }
        _ = PlayerCombatState;
        if (combatState != null) throw new RuleBoundaryException("state_incomplete", "Native combat card inventory was not observed");
        if (ObservedDeckCards == null) throw new RuleBoundaryException("state_incomplete", "Deck card inventory was not observed");
        if (!ObservedDeckCards.SequenceEqual(Deck.Cards.Select(c => c.InstanceId)))
            throw new RuleBoundaryException("invalid_state", "Deck card inventory differs from owned order");
    }
    internal void CardListChanged(CardPile pile)
    {
        if (pile == Deck && ObservedDeckCards != null) ObservedDeckCards = Deck.Cards.Select(c => c.InstanceId!).ToArray();
        Creature.CombatState?.HookInventory?.CardListChanged(this, pile);
        RunState.World.InvalidateHookProjection();
    }
    internal void AddRelicInternal(RelicModel relic, int index = -1)
    {
        relic.AssertMutable();
        if (relics.Contains(relic)) throw new RuleBoundaryException("invalid_state", "Duplicate relic instance");
        relic.Owner = this;
        if (index == -1) relics.Add(relic); else relics.Insert(index, relic);
    }
    public void ActivateHooks() { IsActiveForHooks = true; RunState.World.InvalidateHookProjection(); }
    public void DeactivateHooks() { IsActiveForHooks = false; RunState.World.InvalidateHookProjection(); }
    internal int? ReadObservedCombatTurn()
    {
        var combat = RunState.World.CombatState;
        if (combat == null) return null;
        var domains = combat.HookInventory?.Observation.Players?.SingleOrDefault(p => p.PlayerId == NetId);
        if (domains?.CombatStateStatus == ObservationStatus.ObservedAbsent) return null;
        if (domains?.CombatStateStatus != ObservationStatus.ObservedPresent)
            throw new RuleBoundaryException("state_incomplete", "Player combat-state presence was not observed");
        return combat.RequireTimeline().PlayerTurnNumbers.TryGetValue(NetId, out var turn) ? turn
            : throw new RuleBoundaryException("state_incomplete", "Player combat turn number was not observed");
    }
}
