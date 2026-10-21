using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Native.Entities.Cards;

/// <summary>
/// Native ordered-pile primitives. Shuffle, draw/discard/exhaust command pipelines and UI tracker
/// subscriptions are separate scopes. There are no enchantments in the hydrated card scope yet.
/// </summary>
public class CardPile
{
    private readonly List<CardModel> cards = [];
    private Player? player;
    public static int MaxCardsInHand => 10;
    public PileType Type { get; }
    public IReadOnlyList<CardModel> Cards => cards.AsReadOnly();
    public bool IsEmpty => !Cards.Any();
    public bool IsCombatPile => Type.IsCombatPile();
    public int UpgradableCardCount => cards.Count(card => card.IsUpgradable);
    public event Action? ContentsChanged;
    public event Action<CardModel>? CardAdded;
    public event Action<CardModel>? CardRemoved;
    public event Action? CardAddFinished;
    public event Action? CardRemoveFinished;
    public CardPile(PileType type) => Type = type;
    internal void Bind(Player owner) => player = owner;
    private void RequireOwn(CardModel card)
    {
        if (player != null && (card.World != player.RunState.World || !ReferenceEquals(card.Owner, player)
            || card.InstanceId == null || !ReferenceEquals(card.World.RequireModel(card.InstanceId), card)))
            throw new RuleBoundaryException("foreign_state", "Card and pile must have the same owned player");
    }
    private void Changed() => player?.CardListChanged(this);
    public static CardPile? Get(PileType type, Player player) => type switch {
        PileType.None => null,
        PileType.Draw => player.PlayerCombatState?.DrawPile,
        PileType.Hand => player.PlayerCombatState?.Hand,
        PileType.Discard => player.PlayerCombatState?.DiscardPile,
        PileType.Exhaust => player.PlayerCombatState?.ExhaustPile,
        PileType.Play => player.PlayerCombatState?.PlayPile,
        PileType.Deck => player.Deck,
        _ => throw new ArgumentOutOfRangeException(nameof(type), type, null)
    };
    public static IEnumerable<CardModel> GetCards(Player player, params PileType[] piles) =>
        piles.SelectMany(p => p.GetPile(player).Cards);
    public void AddInternal(CardModel card, int index = -1, bool silent = false)
    {
        card.AssertMutable(); RequireOwn(card);
        if (Cards.Contains(card)) throw new InvalidOperationException($"Card pile already contains {card}.");
        if (index >= 0) cards.Insert(index, card); else cards.Add(card);
        Changed();
        if (!silent) { CardAdded?.Invoke(card); InvokeContentsChanged(); }
    }
    public void RemoveInternal(CardModel card, bool silent = false)
    {
        RequireOwn(card);
        if (!Cards.Contains(card)) throw new InvalidOperationException($"Card pile does not contain {card}.");
        cards.Remove(card); Changed();
        if (!silent) { InvokeCardRemoved(card); InvokeContentsChanged(); InvokeCardRemoveFinished(); }
    }
    public void MoveToBottomInternal(CardModel card)
    {
        RequireOwn(card);
        if (!Cards.Contains(card)) throw new InvalidOperationException($"Card pile does not contain {card}.");
        cards.Remove(card); cards.Add(card); Changed();
    }
    public void MoveToTopInternal(CardModel card)
    {
        RequireOwn(card);
        if (!Cards.Contains(card)) throw new InvalidOperationException($"Card pile does not contain {card}.");
        cards.Remove(card); cards.Insert(0, card); Changed();
    }
    public void Clear(bool silent = false)
    {
        foreach (var card in Cards.ToList()) RemoveInternal(card, silent);
        cards.Clear(); Changed();
    }
    public void InvokeCardRemoved(CardModel card) => CardRemoved?.Invoke(card);
    public void InvokeCardAddFinished() => CardAddFinished?.Invoke();
    public void InvokeCardRemoveFinished() => CardRemoveFinished?.Invoke();
    public void InvokeContentsChanged() => ContentsChanged?.Invoke();
}
