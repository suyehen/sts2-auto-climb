using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Hooks;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Models.Relics;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;

internal static class CardMechanismTests
{
    private static void Assert(bool condition) { if (!condition) throw new Exception("Card mechanism assertion failed"); }
    private static void Error(string code, Action action)
    {
        try { action(); throw new Exception("Expected boundary " + code); }
        catch (RuleBoundaryException e) { Assert(e.Code == code); }
    }
    private static void Execute(SimulationWorld world, Action action) => world.ExecuteAsync(() => { action(); return Task.CompletedTask; }).GetAwaiter().GetResult();
    private static SimulationWorld World(bool combat = true)
    {
        var world = new SimulationWorld(CompiledRules.LoadDefaultParameters(), new ModelRegistry(typeof(Mango).Assembly, typeof(CostCardFixture).Assembly));
        world.Add(new("p", 30, 80)); var player = world.RestorePlayer(7, "p", true);
        if (combat) world.RestoreCombat("cost-combat", CombatPhase.InProgress, "p");
        world.RestorePlayerCombatPresence(player, combat ? ObservationStatus.ObservedPresent : ObservationStatus.ObservedAbsent);
        return world;
    }
    private static CardModel Card<T>(SimulationWorld world, string id, PileType pile = PileType.Hand, int cost = 5,
        CardEnergyCostObservation? observation = null) where T : CardModel
    {
        var card = world.RestoreCardCostScope(world.RunState.Players[0], id, ModelDb.GetId(typeof(T)), pile,
            observation ?? new(cost, 0, false, []), ObservationStatus.ObservedAbsent, ObservationStatus.ObservedAbsent);
        world.RestoreCardKeywords(card, []); world.RestoreCardUpgradeLevel(card, 0);
        world.RestoreCardDynamicVars(card, []); world.RestoreCardStarCosts(card, new(-1, false, false, 0, [])); return card;
    }
    private static CostLedgerRelic Relic(SimulationWorld world, string id) => (CostLedgerRelic)world.RestoreRelic(world.RunState.Players[0],
        new(id, ModelDb.GetId(typeof(CostLedgerRelic)), false, false, 0, 1, RelicStatus.Normal));
    private static CombatHookObservation Observation(SimulationWorld world) => new(["p"], [],
        [new(7, world.RunState.Players[0].Deck.Cards.Select(c => c.InstanceId!).ToArray(),
            world.RunState.Players[0].Relics.Select(r => r.InstanceId!).ToArray(), [], ObservationStatus.ObservedPresent, [],
            world.RunState.Players[0].PlayerCombatState!.AllPiles.Select(p => p.Cards.Select(c => c.InstanceId!).ToArray()).ToArray())],
        [], [], [], [], ObservationStatus.ObservedAbsent, new() { ["p"] = [] });
    public static void Run(Action<string, Action> check)
    {
        check("native cost early exits preserve canonical, negative and X semantics without unused hook data", () => {
            var world = World(); var canonical = world.Models.GetById<CostCardFixture>(ModelDb.GetId(typeof(CostCardFixture)));
            Assert(canonical.EnergyCost.Canonical == 5 && canonical.EnergyCost.GetWithModifiers(CostModifiers.All) == 5);
            var negative = Card<UnplayableCostFixture>(world, "negative", cost: -2);
            negative.EnergyCost.SetThisTurn(0); Assert(!negative.EnergyCost.HasLocalModifiers);
            negative.EnergyCost.SetUntilPlayed(3); Assert(negative.EnergyCost.HasLocalModifiers);
            Assert(negative.EnergyCost.GetWithModifiers(CostModifiers.All) == -2);
            var x = Card<XCostFixture>(world, "x", cost: 0); x.EnergyCost.AddThisCombat(20);
            Assert(x.EnergyCost.Canonical == 0 && x.EnergyCost.GetWithModifiers(CostModifiers.All) == 0);
            x.EnergyCost.CapturedXValue = 4; Assert(x.EnergyCost.GetResolved() == 4);
            x.EnergyCost.UpgradeBy(-1); Assert(!x.EnergyCost.WasJustUpgraded);
            try { _ = negative.EnergyCost.CapturedXValue; throw new Exception("Non-X captured value accepted"); } catch (InvalidOperationException) { }
        });
        check("ordered local costs preserve reduce-only rules and distinct turn/played expiration", () => {
            var world = World(); var card = Card<CostCardFixture>(world, "c"); var cost = card.EnergyCost; var events = 0;
            card.EnergyCostChanged += () => events++;
            cost.SetThisCombat(2); cost.AddThisCombat(1); cost.SetThisTurn(0); cost.AddThisCombat(1);
            Assert(cost.GetWithModifiers(CostModifiers.Local) == 1 && cost.GetWithModifiers(CostModifiers.None) == 5);
            cost.SetUntilPlayed(3, reduceOnly: true); Assert(cost.GetWithModifiers(CostModifiers.Local) == 1);
            Assert(cost.EndOfTurnCleanup() && cost.GetWithModifiers(CostModifiers.Local) == 3);
            Assert(cost.AfterCardPlayedCleanup() && cost.GetWithModifiers(CostModifiers.Local) == 4);
            Assert(!cost.AfterCardPlayedCleanup() && !cost.EndOfTurnCleanup() && events == 0);
            cost.AddThisTurnOrUntilPlayed(-2); Assert(cost.GetWithModifiers(CostModifiers.Local) == 2);
            Assert(cost.AfterCardPlayedCleanup() && !cost.EndOfTurnCleanup());
            cost.AddThisCombat(100, reduceOnly: true); Assert(cost.GetWithModifiers(CostModifiers.Local) == 4);
        });
        check("energy cost upgrade lowers absolute modifiers and keeps native preview/event/reset behavior", () => {
            var world = World(); var card = Card<CostCardFixture>(world, "c"); var cost = card.EnergyCost; var events = 0;
            card.EnergyCostChanged += () => events++;
            cost.SetThisCombat(7); cost.AddThisCombat(1); cost.UpgradeBy(-3);
            Assert(cost.Canonical == 5 && cost.GetWithModifiers(CostModifiers.None) == 2);
            Assert(cost.GetWithModifiers(CostModifiers.Local) == 3 && cost.WasJustUpgraded && events == 1);
            cost.FinalizeUpgrade(); Assert(!cost.WasJustUpgraded && events == 1);
            cost.ResetForDowngrade(); Assert(cost.GetWithModifiers(CostModifiers.None) == 5 && cost.GetWithModifiers(CostModifiers.Local) == 3 && events == 2);
            cost.UpgradeBy(0); Assert(events == 2 && !cost.WasJustUpgraded);
            cost.SetCustomBaseCost(-1); Assert(cost.GetWithModifiers(CostModifiers.Local) == -1 && events == 3);
        });
        check("X spend requires observed energy and energy arithmetic truncates after decimal addition/subtraction", () => {
            var world = World(); var player = world.RunState.Players[0]; var x = Card<XCostFixture>(world, "x", cost: 0);
            Error("state_incomplete", () => x.EnergyCost.GetAmountToSpend());
            world.RestorePlayerEnergy(player, 3); Assert(x.EnergyCost.GetAmountToSpend() == 3);
            var energy = player.PlayerCombatState!; var events = new List<(int, int)>(); energy.EnergyChanged += (a, b) => events.Add((a, b));
            energy.GainEnergy(.9m); energy.LoseEnergy(.9m); energy.GainEnergy(999999999); energy.LoseEnergy(1000000000);
            Assert(events.SequenceEqual(new[] { (3, 2), (2, 999999999), (999999999, 0) }));
            try { energy.GainEnergy(-1); throw new Exception("Negative gain accepted"); } catch (ArgumentException) { }
            var outside = World(false); var outsideX = Card<XCostFixture>(outside, "x", PileType.Deck, 0);
            Assert(outsideX.EnergyCost.GetAmountToSpend() == 0);
        });
        check("card cost hydration owns ordered modifier data and rejects unknown or present attachments", () => {
            var world = World(); var entries = new[] { new LocalCostObservation(2, LocalCostType.Absolute, LocalCostModifierExpiration.WhenPlayed, false) };
            var card = Card<CostCardFixture>(world, "c", observation: new(7, 8, true, entries));
            entries[0] = entries[0] with { Amount = 90 };
            Assert(card.EnergyCost.GetWithModifiers(CostModifiers.Local) == 2 && card.EnergyCost.WasJustUpgraded);
            Error("state_incomplete", () => world.RestoreCardCostScope(world.RunState.Players[0], "bad1", card.Id, PileType.Hand,
                new(5, 0, false, []), ObservationStatus.Unobserved, ObservationStatus.ObservedAbsent));
            Error("mechanism_not_implemented", () => world.RestoreCardCostScope(world.RunState.Players[0], "bad2", card.Id, PileType.Hand,
                new(5, 0, false, []), ObservationStatus.ObservedPresent, ObservationStatus.ObservedAbsent));
            Error("invalid_state", () => world.RestoreCardCostScope(world.RunState.Players[0], "bad3", card.Id, PileType.Hand,
                new(5, 0, false, [new(1, LocalCostType.Absolute, (LocalCostModifierExpiration)1, false)]), ObservationStatus.ObservedAbsent, ObservationStatus.ObservedAbsent));
            Assert(world.RunState.Players[0].PlayerCombatState!.Hand.Cards.Count == 1);
        });
        check("global cost uses regular then late native hook passes including out values from false-returning hooks", () => {
            var world = World(); var first = Relic(world, "a"); var second = Relic(world, "b"); var card = Card<CostCardFixture>(world, "c");
            card.EnergyCost.AddThisCombat(-1); first.Add = 1; first.LateAdd = 10; second.Multiplier = 2; second.LateMultiplier = .5m;
            world.RestoreCombatHookInventory(Observation(world));
            Execute(world, () => Assert(card.EnergyCost.GetAmountToSpend() == 10));
            Assert(first.Calls.SequenceEqual(["normal:a:4", "card:c:normal", "late:a:10", "card:c:late"]));
            Assert(second.Calls.SequenceEqual(["normal:b:5", "late:b:20"]));
            Assert(card.EnergyCost.GetWithModifiers(CostModifiers.Local) == 4);
            Execute(world, () => Assert(Hook.ModifyEnergyCostInCombat(world.CombatState!, card, -1) == -1));
            Assert(second.Calls.Count == 2);
            first.Add = 0; first.LateAdd = 0; second.Multiplier = 1; second.LateMultiplier = .725m;
            Execute(world, () => Assert(card.EnergyCost.GetResolved() == 2));
            second.LateMultiplier = -1;
            Execute(world, () => Assert(card.EnergyCost.GetAmountToSpend() == 0));
        });
        check("owned card listeners follow native deck/run and hand-draw-discard-exhaust-play combat order", () => {
            var world = World(); var player = world.RunState.Players[0]; var relic = Relic(world, "r");
            var deck = Card<CostCardFixture>(world, "deck", PileType.Deck);
            foreach (var (id, pile) in new[] { ("h", PileType.Hand), ("d", PileType.Draw), ("c", PileType.Discard), ("e", PileType.Exhaust), ("p", PileType.Play) })
                Card<CostCardFixture>(world, id, pile);
            world.RestoreCombatHookInventory(Observation(world));
            Assert(world.RunState.IterateHookListeners(world.CombatState).Select(m => m.InstanceId).SequenceEqual(["deck", "r", "h", "d", "c", "e", "p"]));
            Assert(deck.CombatState == null && !deck.ShouldReceiveCombatHooks);
            Assert(ReferenceEquals(((CardModel)world.RequireModel("h")).CombatState, world.CombatState));
            deck.UpgradePreviewType = CardUpgradePreviewType.Combat; Assert(ReferenceEquals(deck.CombatState, world.CombatState));
            try { deck.UpgradePreviewType = CardUpgradePreviewType.None; throw new Exception("Preview reset accepted"); } catch (InvalidOperationException) { }
            Execute(world, () => ((CardModel)world.RequireModel("h")).EnergyCost.GetWithModifiers(CostModifiers.All));
            Assert(relic.Calls.Where(c => c.StartsWith("card:")).SequenceEqual(["card:h:normal", "card:d:normal", "card:c:normal", "card:e:normal", "card:p:normal", "card:h:late", "card:d:late", "card:c:late", "card:e:late", "card:p:late"]));
            Assert(player.PlayerCombatState!.AllPiles.Select(p => p.Type).SequenceEqual([PileType.Hand, PileType.Draw, PileType.Discard, PileType.Exhaust, PileType.Play]));
        });
        check("pile primitives retain event ordering, silent behavior, identity and native top-bottom semantics", () => {
            var world = World(); var first = Card<CostCardFixture>(world, "a"); var second = Card<CostCardFixture>(world, "b");
            var player = world.RunState.Players[0]; var hand = player.PlayerCombatState!.Hand; var discard = player.PlayerCombatState.DiscardPile;
            world.RestoreCombatHookInventory(Observation(world));
            var events = new List<string>(); hand.CardRemoved += _ => events.Add("removed"); hand.ContentsChanged += () => events.Add("contents"); hand.CardRemoveFinished += () => events.Add("finished");
            discard.CardAdded += _ => events.Add("added"); discard.ContentsChanged += () => events.Add("discard-contents"); discard.CardAddFinished += () => events.Add("add-finished");
            hand.MoveToTopInternal(second); Assert(hand.Cards.SequenceEqual(new[] { second, first }) && events.Count == 0);
            hand.MoveToBottomInternal(second); hand.RemoveInternal(first); discard.AddInternal(first);
            Assert(events.SequenceEqual(["removed", "contents", "finished", "added", "discard-contents"]));
            Assert(ReferenceEquals(first.Pile, discard) && ReferenceEquals(CardPile.Get(PileType.Hand, player), hand));
            Assert(CardPile.GetCards(player, PileType.Hand, PileType.Discard).SequenceEqual(new[] { second, first }));
            discard.InvokeCardAddFinished(); Assert(events[^1] == "add-finished");
            var count = events.Count; discard.RemoveInternal(first, silent: true); hand.AddInternal(first, index: -5, silent: true);
            Assert(events.Count == count && hand.Cards.SequenceEqual(new[] { second, first }));
            try { hand.AddInternal(first); throw new Exception("Duplicate accepted"); } catch (InvalidOperationException) { }
            var other = World(); var foreign = Card<CostCardFixture>(other, "foreign");
            Error("foreign_state", () => hand.AddInternal(foreign));
        });
        check("cost hook candidate snapshots retain moved cards in current pass and rebuild candidates for late pass", () => {
            var world = World(); var relic = Relic(world, "r"); var card = Card<CostCardFixture>(world, "c");
            relic.MoveCardToDeck = card; world.RestoreCombatHookInventory(Observation(world));
            Execute(world, () => Assert(card.EnergyCost.GetWithModifiers(CostModifiers.All) == 5));
            Assert(relic.Calls.SequenceEqual(["normal:r:5", "card:c:normal", "late:r:5"]));
            Assert(card.Pile == relic.Owner.Deck && card.CombatState == null);
            var deactivated = World(); var r = Relic(deactivated, "r"); var c = Card<CostCardFixture>(deactivated, "c"); r.Deactivate = true;
            deactivated.RestoreCombatHookInventory(Observation(deactivated));
            Execute(deactivated, () => c.EnergyCost.GetWithModifiers(CostModifiers.All));
            Assert(r.Calls.SequenceEqual(["normal:r:5"]));
        });
        check("generic world fork preserves card cost owner/alias cycles and isolates modifiers, energy and callbacks", () => {
            var world = World(); var player = world.RunState.Players[0]; world.RestorePlayerEnergy(player, 4);
            var card = (CostCardFixture)Card<CostCardFixture>(world, "c"); var x = Card<XCostFixture>(world, "x", cost: 0);
            card.EnergyCost.SetThisCombat(7); card.EnergyCost.AddUntilPlayed(-1); card.ObserveOwnCost(); x.EnergyCost.CapturedXValue = 3;
            var externalEvents = 0; card.EnergyCostChanged += () => externalEvents++;
            world.RestoreCombatHookInventory(Observation(world)); var sibling = world.Clone(); var branch = world.Clone();
            var forked = (CostCardFixture)branch.RequireModel("c"); var forkX = (CardModel)branch.RequireModel("x");
            forked.EnergyCost.UpgradeBy(-3); forked.EnergyCost.AfterCardPlayedCleanup(); forkX.EnergyCost.CapturedXValue = 8;
            branch.RunState.Players[0].PlayerCombatState!.LoseEnergy(1);
            Assert(forked.EnergyCost.GetWithModifiers(CostModifiers.Local) == 2 && card.EnergyCost.GetWithModifiers(CostModifiers.Local) == 6);
            Assert(((CardModel)sibling.RequireModel("c")).EnergyCost.GetWithModifiers(CostModifiers.Local) == 6);
            Assert(forked.CostChanges == 1 && card.CostChanges == 0 && externalEvents == 0);
            Assert(forkX.EnergyCost.CapturedXValue == 8 && x.EnergyCost.CapturedXValue == 3 && forkX.EnergyCost.GetAmountToSpend() == 3 && x.EnergyCost.GetAmountToSpend() == 4);
            Assert(ReferenceEquals(forked.Pile, branch.RunState.Players[0].PlayerCombatState!.Hand) && !ReferenceEquals(forked.CanonicalInstance, card.CanonicalInstance));
            forked.Pile!.RemoveInternal(forked); branch.RunState.Players[0].PlayerCombatState!.DiscardPile.AddInternal(forked);
            Assert(forked.Pile.Type == PileType.Discard && card.Pile!.Type == PileType.Hand);
        });
        check("unknown card inventories stay unknown through pile changes and missing entries stop cost hooks", () => {
            foreach (var unknown in new[] { true, false })
            {
                var world = World(); var r = Relic(world, "r"); var c = Card<CostCardFixture>(world, "c"); var observed = Observation(world);
                var piles = observed.Players![0].CombatPiles!;
                piles[0] = unknown ? null! : ["different-card"];
                world.RestoreCombatHookInventory(observed);
                if (unknown) { var hand = world.RunState.Players[0].PlayerCombatState!.Hand; hand.MoveToTopInternal(c); }
                Error(unknown ? "state_incomplete" : "invalid_state", () => Execute(world, () => c.EnergyCost.GetWithModifiers(CostModifiers.All)));
                Assert(r.Calls.Count == 0);
            }
            var duplicate = World(); var card = Card<CostCardFixture>(duplicate, "c");
            duplicate.RunState.Players[0].PlayerCombatState!.DrawPile.AddInternal(card);
            duplicate.RestoreCombatHookInventory(Observation(duplicate));
            Error("invalid_state", () => duplicate.CombatState!.IterateHookListeners().ToArray());
            var repeated = World(); var inventory = Observation(repeated);
            Error("invalid_state", () => repeated.RestoreCombatHookInventory(inventory with { Players = [inventory.Players![0], inventory.Players[0]] }));
        });
        check("resource checkpoints reject energy/pile observations and outside-combat costs require known deck membership", () => {
            var world = World(); world.RestorePlayerEnergy(world.RunState.Players[0], 3);
            Error("mechanism_not_implemented", () => WorldSnapshotCodec.Export(world));
            var outside = World(false); var card = Card<CostCardFixture>(outside, "deck", PileType.Deck);
            Error("state_incomplete", () => card.EnergyCost.GetAmountToSpend());
            outside.RestoreDeckCardInventory(outside.RunState.Players[0], ["deck"]);
            Assert(card.EnergyCost.GetAmountToSpend() == 5 && card.CombatState == null);
            var mutableClone = (CardModel)card.MutableClone();
            Assert(mutableClone.EnergyCost.GetWithModifiers(CostModifiers.Local) == 5);
        });
    }
}

public class CostCardFixture : CardModel
{
    public CostCardFixture() : base(5, CardType.Skill, CardRarity.Common, TargetType.Self) { }
    public int CostChanges { get; private set; }
    public void ObserveOwnCost() => EnergyCostChanged += OnCostChanged;
    private void OnCostChanged() => CostChanges++;
    public override bool TryModifyEnergyCostInCombat(CardModel card, decimal originalCost, out decimal modifiedCost)
    {
        Owner.Relics.OfType<CostLedgerRelic>().FirstOrDefault()?.Calls.Add("card:" + InstanceId + ":normal");
        modifiedCost = originalCost; return false;
    }
    public override bool TryModifyEnergyCostInCombatLate(CardModel card, decimal originalCost, out decimal modifiedCost)
    {
        Owner.Relics.OfType<CostLedgerRelic>().FirstOrDefault()?.Calls.Add("card:" + InstanceId + ":late");
        modifiedCost = originalCost; return false;
    }
}
public sealed class XCostFixture : CardModel
{
    public XCostFixture() : base(99, CardType.Skill, CardRarity.Common, TargetType.Self) { }
    protected override bool HasEnergyCostX => true;
}
public sealed class UnplayableCostFixture : CardModel
{
    public UnplayableCostFixture() : base(-2, CardType.Status, CardRarity.Status, TargetType.Self) { }
}
public sealed class CostLedgerRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public List<string> Calls { get; private set; } = [];
    public decimal Add { get; set; }
    public decimal Multiplier { get; set; } = 1;
    public decimal LateAdd { get; set; }
    public decimal LateMultiplier { get; set; } = 1;
    public CardModel? MoveCardToDeck { get; set; }
    public bool Deactivate { get; set; }
    protected override void DeepCloneFields() { base.DeepCloneFields(); Calls = new(Calls); }
    public override bool TryModifyEnergyCostInCombat(CardModel card, decimal originalCost, out decimal modifiedCost)
    {
        Calls.Add("normal:" + InstanceId + ":" + originalCost);
        if (MoveCardToDeck is { } moving) { Owner.PlayerCombatState!.Hand.RemoveInternal(moving); Owner.Deck.AddInternal(moving); MoveCardToDeck = null; }
        if (Deactivate) Owner.DeactivateHooks();
        modifiedCost = (originalCost + Add) * Multiplier; return false;
    }
    public override bool TryModifyEnergyCostInCombatLate(CardModel card, decimal originalCost, out decimal modifiedCost)
    { Calls.Add("late:" + InstanceId + ":" + originalCost); modifiedCost = (originalCost + LateAdd) * LateMultiplier; return false; }
}
