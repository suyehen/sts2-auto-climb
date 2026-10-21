using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Entities.Players;

namespace AutoSlay.Rules.Native.Combat;

/// <summary>
/// Observed source domains, not an ordered list of effect callbacks. Null collections remain unknown.
/// Domains whose models/state are not migrated can currently be proved empty only; present entries
/// fail explicitly. Powers/relics/cards are read from live owned state on every native dispatch.
/// </summary>
public sealed record PlayerHookDomains(ulong PlayerId, string[]? DeckCards, string[]? Relics,
    // CombatPiles follows native AllPiles: Hand, Draw, Discard, Exhaust, Play.
    string?[]? PotionSlots, ObservationStatus CombatStateStatus, string[]? Orbs, string[][]? CombatPiles);
public sealed record CombatHookObservation(string[]? Allies, string[]? Enemies, PlayerHookDomains[]? Players,
    string[]? RunSubscribers, string[]? CombatSubscribers, string[]? Modifiers, string[]? Badges,
    ObservationStatus ScalingModelStatus, Dictionary<string, string[]?>? CreaturePowers);

internal sealed class CombatHookInventory(CombatState combat, CombatHookObservation observation)
{
    internal CombatHookObservation Observation { get; } = observation;
    private RunState Run => combat.RunState;
    internal void RequireResolved()
    {
        var observed = Observation;
        if (observed.Allies == null || observed.Enemies == null || observed.Players == null)
            Missing("combat.allies/enemies/players");
        var ids = observed.Allies!.Concat(observed.Enemies!).ToArray();
        if (ids.Distinct(StringComparer.Ordinal).Count() != ids.Length
            || !ids.Order(StringComparer.Ordinal).SequenceEqual(combat.Creatures.Select(c => c.InstanceId).Order(StringComparer.Ordinal)))
            Invalid("Observed ally/enemy order must cover combat membership exactly");
        if (observed.Players!.Select(p => p.PlayerId).Distinct().Count() != observed.Players!.Length
            || !observed.Players.Select(p => p.PlayerId).Order().SequenceEqual(Run.Players.Select(p => p.NetId).Order()))
            Invalid("Hook domains must cover the run players exactly");
        if (observed.CreaturePowers == null) Missing("combat.creature_powers");
        if (!observed.CreaturePowers!.Keys.Order(StringComparer.Ordinal).SequenceEqual(ids.Order(StringComparer.Ordinal)))
            Invalid("Power inventories must cover combat creatures exactly");
        foreach (var creature in OrderedCreatures())
        {
            var powers = observed.CreaturePowers[creature.InstanceId];
            if (powers == null) Missing("creature.powers: " + creature.InstanceId);
            if (!powers!.SequenceEqual(creature.Powers.Select(p => p.InstanceId))) Invalid("Power inventory differs from owned power order");
        }
        RequireEmpty(observed.RunSubscribers, "run.mod_subscribers");
        RequireEmpty(observed.CombatSubscribers, "combat.mod_subscribers");
        RequireEmpty(observed.Modifiers, "combat.modifiers"); RequireEmpty(observed.Badges, "combat.badges");
        if (observed.ScalingModelStatus == ObservationStatus.Unobserved) Missing("combat.multiplayer_scaling_model");
        if (observed.ScalingModelStatus == ObservationStatus.ObservedPresent && combat.MultiplayerScalingModel == null)
            Missing("combat.multiplayer_scaling_model.instance");
        if (observed.ScalingModelStatus == ObservationStatus.ObservedAbsent && combat.MultiplayerScalingModel != null)
            Invalid("Scaling model is present in owned state but observed absent");
        if (!Enum.IsDefined(observed.ScalingModelStatus)) Invalid("Invalid scaling-model observation status");
        foreach (var player in Run.Players)
        {
            var domains = observed.Players.Single(p => p.PlayerId == player.NetId);
            if (domains.Relics == null) Missing("player.relics");
            if (!domains.Relics!.SequenceEqual(player.Relics.Select(r => r.InstanceId)))
                Invalid("Observed relic inventory differs from owned relic order");
            RequirePlayerCards(player);
            if (domains.PotionSlots == null) Missing("player.potion_slots");
            if (domains.PotionSlots!.Any(id => id != null)) Unported("player.potion_slots");
            if (!Enum.IsDefined(domains.CombatStateStatus)) Invalid("Invalid player combat-state observation status");
            if (domains.CombatStateStatus == ObservationStatus.Unobserved) Missing("player.combat_state");
            if (domains.CombatStateStatus == ObservationStatus.ObservedPresent)
            {
                RequireEmpty(domains.Orbs, "player.combat_state.orbs");
                if (domains.CombatPiles == null) Missing("player.combat_state.all_piles");
                if (domains.CombatPiles!.Length != 5) Invalid("Native player combat state has five ordered card piles");
            }
            else if (domains.Orbs != null || domains.CombatPiles != null)
                Invalid("Absent player combat state cannot contain orb/pile observations");
        }
        foreach (var creature in OrderedCreatures())
        {
            if (!creature.IsPlayer) Unported("creature.monster");
            if (creature.Side != CombatSide.Player) Invalid("Native player creatures belong to the player side");
        }
    }
    internal IEnumerable<AbstractModel> RunListeners()
    {
        RequireResolved();
        var candidates = Run.Players.Where(p => p.IsActiveForHooks).SelectMany(p => p.Deck.Cards).ToArray();
        foreach (var card in candidates)
            if (!card.HasBeenRemovedFromState && card.Owner.IsActiveForHooks) yield return card;
    }

    internal IEnumerable<AbstractModel> CombatListeners()
    {
        RequireResolved();
        var candidates = new List<AbstractModel>();
        foreach (var creature in OrderedCreatures())
        {
            candidates.AddRange(creature.Powers);
            var player = creature.Player!;
            if (!player.IsActiveForHooks) continue;
            candidates.AddRange(player.Relics.Where(r => !r.IsMelted));
            if (player.OwnedCombatState != null)
                foreach (var pile in player.OwnedCombatState.AllPiles) candidates.AddRange(pile.Cards);
            // Potion/orb/modifier/badge/subscriber branches are observed absent/empty.
            // Their nonempty observations were rejected above, rather than skipped here.
        }
        if (combat.MultiplayerScalingModel != null) candidates.Add(combat.MultiplayerScalingModel);
        // Native iteration snapshots candidates once, but checks membership as each listener is
        // yielded. A previous callback can deactivate a player or remove an existing model.
        foreach (var model in candidates)
            if (model switch {
                PowerModel power => power.Owner.CombatState != null && (power.Owner.Player?.IsActiveForHooks ?? true),
                RelicModel relic => !relic.HasBeenRemovedFromState && relic.Owner.IsActiveForHooks,
                CardModel card => !card.HasBeenRemovedFromState && card.Owner.IsActiveForHooks,
                Models.Singleton.MultiplayerScalingModel => true,
                _ => throw new RuleBoundaryException("mechanism_not_implemented", "Unmigrated native listener membership")
            }) yield return model;
    }
    private IEnumerable<Creature> OrderedCreatures() => Observation.Allies!.Concat(Observation.Enemies!).Select(Run.World.Get);
    internal void RequirePlayerCards(Player player)
    {
        var matches = Observation.Players?.Where(p => p.PlayerId == player.NetId).ToArray();
        if (matches == null || matches.Length == 0) Missing("player.card_domains");
        if (matches!.Length != 1) Invalid("Duplicate player card-domain observations");
        var domains = matches[0];
        if (domains.DeckCards == null) Missing("player.deck.cards");
        ValidateCards(domains.DeckCards!, player.Deck.Cards, player);
        if (domains.CombatStateStatus == ObservationStatus.Unobserved) Missing("player.combat_state");
        if (!Enum.IsDefined(domains.CombatStateStatus)) Invalid("Invalid player combat-state observation status");
        if (domains.CombatStateStatus != player.CombatStateStatus) Invalid("Player combat-state presence differs from owned state");
        if (domains.CombatStateStatus == ObservationStatus.ObservedPresent)
        {
            if (domains.CombatPiles == null) Missing("player.combat_state.all_piles");
            if (domains.CombatPiles!.Length != 5) Invalid("Native player combat state has five ordered card piles");
            var piles = player.OwnedCombatState?.AllPiles ?? throw new RuleBoundaryException("state_incomplete", "Player combat piles were not restored");
            for (var i = 0; i < piles.Count; i++)
            {
                if (domains.CombatPiles[i] == null) Missing("player.combat_state.pile.cards");
                ValidateCards(domains.CombatPiles[i], piles[i].Cards, player);
            }
            var all = piles.SelectMany(p => p.Cards).Concat(player.Deck.Cards).ToArray();
            if (all.Distinct(ReferenceEqualityComparer.Instance).Count() != all.Length)
                Invalid("A card instance cannot belong to multiple owned piles");
        }
        else if (domains.Orbs != null || domains.CombatPiles != null)
            Invalid("Absent player combat state cannot contain orb/pile observations");
    }
    private void ValidateCards(string[] observed, IReadOnlyList<CardModel> owned, Player player)
    {
        if (!observed.SequenceEqual(owned.Select(c => c.InstanceId)))
            Invalid("Observed card inventory differs from owned pile order");
        foreach (var card in owned)
            if (card.IsCanonical || card.World != Run.World || card.Owner != player || card.InstanceId == null
                || !ReferenceEquals(Run.World.RequireModel(card.InstanceId), card))
                Invalid("Pile contains a foreign or unregistered card");
    }
    internal void CardListChanged(Player player, AutoSlay.Rules.Native.Entities.Cards.CardPile pile)
    {
        if (Observation.Players == null) return;
        var index = Array.FindIndex(Observation.Players, p => p.PlayerId == player.NetId);
        if (index < 0) return;
        var domains = Observation.Players[index];
        Observation.Players[index] = domains with {
            DeckCards = pile != player.Deck || domains.DeckCards == null ? domains.DeckCards : player.Deck.Cards.Select(c => c.InstanceId!).ToArray(),
            CombatPiles = domains.CombatPiles == null || domains.CombatPiles.Length != 5 || player.OwnedCombatState == null ? domains.CombatPiles
                : domains.CombatPiles.Select((known, i) => known == null || player.OwnedCombatState!.AllPiles[i] != pile
                    ? known! : pile.Cards.Select(c => c.InstanceId!).ToArray()).ToArray()
        };
    }
    internal void PowerListChanged(Creature creature)
    {
        if (Observation.CreaturePowers?.TryGetValue(creature.InstanceId, out var known) == true && known != null)
            Observation.CreaturePowers[creature.InstanceId] = creature.Powers.Any(p => p.InstanceId == null)
                ? null : creature.Powers.Select(p => p.InstanceId!).ToArray();
    }
    private static void RequireEmpty(string[]? entries, string path)
    { if (entries == null) Missing(path); if (entries!.Length != 0) Unported(path); }
    private static void Missing(string path) => throw new RuleBoundaryException("state_incomplete", "Native hook domain was not observed: " + path);
    private static void Unported(string path) => throw new RuleBoundaryException("mechanism_not_implemented", "Nonempty native hook domain is not migrated: " + path);
    private static void Invalid(string message) => throw new RuleBoundaryException("invalid_state", message);
}
