using System.Reflection;
using AutoSlay.Rules.Native.Commands;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.GameActions.Multiplayer;
using AutoSlay.Rules.Native.Localization.DynamicVars;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Models.Cards;
using AutoSlay.Rules.Native.Models.Relics;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;

internal static class UpgradeMechanismTests
{
    private static void Assert(bool condition) { if (!condition) throw new Exception("Upgrade mechanism assertion failed"); }
    private static void Error(string code, Action action)
    {
        try { action(); throw new Exception("Expected boundary " + code); }
        catch (RuleBoundaryException error) { Assert(error.Code == code); }
    }
    private static SimulationWorld World(CombatPhase phase = CombatPhase.InProgress, bool combat = true)
    {
        var world = new SimulationWorld(CompiledRules.LoadDefaultParameters(), new ModelRegistry(typeof(Mango).Assembly, typeof(UpgradeProbeCard).Assembly));
        world.Add(new("p", 30, 80)); var player = world.RestorePlayer(7, "p", true);
        if (combat) { world.RestoreCombat("upgrade", phase, "p"); world.RestorePlayerCombatPresence(player, ObservationStatus.ObservedPresent); world.RestorePlayerEnergy(player, 3); }
        else world.RestorePlayerCombatPresence(player, ObservationStatus.ObservedAbsent);
        return world;
    }
    private static CardVariableObservation[] Vars(CardModel canonical) => canonical.DynamicVars.Values.Select(v =>
        new CardVariableObservation(v.Name, v.GetType().FullName!, v.BaseValue, v.EnchantedValue, v.PreviewValue, v.WasJustUpgraded,
            v is AutoSlay.Rules.Native.Localization.DynamicVars.BlockVar b ? (int)b.Props : v is DamageVar d ? (int)d.Props : null)).ToArray();
    private static T Card<T>(SimulationWorld world, string id, PileType pile = PileType.Hand, bool restoreVariables = true) where T : CardModel
    {
        var canonical = world.Models.GetById<CardModel>(ModelDb.GetId(typeof(T)));
        var card = (T)world.RestoreCardCostScope(world.RunState.Players[0], id, canonical.Id, pile,
            new(canonical.EnergyCost.Canonical, 0, false, []), ObservationStatus.ObservedAbsent, ObservationStatus.ObservedAbsent);
        world.RestoreCardKeywords(card, canonical.CanonicalKeywords.ToArray()); world.RestoreCardUpgradeLevel(card, 0);
        if (restoreVariables) world.RestoreCardDynamicVars(card, Vars(canonical));
        world.RestoreCardStarCosts(card, new(canonical.CanonicalStarCost, false, false, 0, [])); return card;
    }
    private static void Observe(SimulationWorld world)
    {
        var player = world.RunState.Players[0];
        if (world.CombatState == null) { world.RestoreDeckCardInventory(player, player.Deck.Cards.Select(c => c.InstanceId!).ToArray()); return; }
        world.RestoreCombatHookInventory(new(["p"], [], [new(7, player.Deck.Cards.Select(c => c.InstanceId!).ToArray(),
            player.Relics.Select(r => r.InstanceId!).ToArray(), [], ObservationStatus.ObservedPresent, [],
            player.PlayerCombatState!.AllPiles.Select(p => p.Cards.Select(c => c.InstanceId!).ToArray()).ToArray())],
            [], [], [], [], ObservationStatus.ObservedAbsent, new() { ["p"] = [] }));
    }
    private static void Execute(SimulationWorld world, Action action) => world.ExecuteAsync(() => { action(); return Task.CompletedTask; }).GetAwaiter().GetResult();
    private static Task Effect(CardModel card)
    {
        var play = new CardPlay { Card = card, Player = card.Owner, Target = null,
            Resources = new() { EnergySpent = 0, EnergyValue = 0, StarsSpent = 0, StarValue = 0 }, ResultPile = PileType.Exhaust,
            IsAutoPlay = false, PlayIndex = 0, PlayCount = 1 };
        return (Task)typeof(Enlightenment).GetMethod("OnPlay", BindingFlags.Instance | BindingFlags.NonPublic)!.Invoke(card, [new ThrowingPlayerChoiceContext(), play])!;
    }
    public static void Run(Action<string, Action> check)
    {
        check("upgrade callback and calculated recalc precede Upgraded; finalization clears flags without another notification", () => {
            var world = World(); var card = Card<UpgradeProbeCard>(world, "c"); Observe(world);
            var events = new List<string>(); card.EnergyCostChanged += () => events.Add("energy:" + card.CurrentUpgradeLevel);
            card.StarCostChanged += () => events.Add("star:" + card.BaseStarCost + ":" + card.WasStarCostJustUpgraded);
            card.Upgraded += () => {
                events.Add("upgraded:" + card.CurrentUpgradeLevel);
                Assert(card.DynamicVars["Calculated"].BaseValue == 7 && card.DynamicVars["Calculated"].WasJustUpgraded);
                Assert(card.EnergyCost.WasJustUpgraded && card.WasStarCostJustUpgraded);
            };
            Execute(world, () => CardCmd.Upgrade(card));
            Assert(events.SequenceEqual(["energy:1", "star:3:False", "upgraded:1"]));
            Assert(card.EnergyCost.GetWithModifiers(CostModifiers.None) == 2 && card.BaseStarCost == 3);
            Assert(!card.EnergyCost.WasJustUpgraded && !card.WasStarCostJustUpgraded && card.DynamicVars.Values.All(v => !v.WasJustUpgraded));
            Assert(card.DynamicVars["Calculated"].IntValue == 7 && Convert.ToDecimal(card.DynamicVars["Calculated"]) == 16);
        });
        check("downgrade replaces variables and local keywords while retaining temporary costs and native flag order", () => {
            var world = World(); var card = Card<UpgradeProbeCard>(world, "c"); Observe(world);
            card.EnergyCost.SetThisCombat(9); card.SetStarCostThisCombat(2); card.SetStarCostUntilPlayed(4);
            card.UpgradeInternal(); Assert(card.TemporaryStarCost!.Cost == 2 && card.WasStarCostJustUpgraded);
            card.AddKeyword(CardKeyword.Exhaust); card.DynamicVars.CalculationBase.PreviewValue = 99;
            var observations = new List<string>(); card.EnergyCostChanged += () => observations.Add("energy:" + card.BaseStarCost + ":" + card.GetKeywordsWithSources(KeywordSources.Local).Contains(CardKeyword.Exhaust));
            card.Upgraded += () => observations.Add("upgraded:" + card.CurrentUpgradeLevel);
            Execute(world, () => CardCmd.Downgrade(card));
            Assert(observations.SequenceEqual(["energy:3:True", "upgraded:0"]));
            Assert(card.DynamicVars.CalculationBase.BaseValue == 4 && card.DynamicVars.CalculationBase.PreviewValue == 4);
            Assert(card.EnergyCost.GetWithModifiers(CostModifiers.None) == 3 && card.EnergyCost.GetWithModifiers(CostModifiers.Local) == 2);
            Assert(card.BaseStarCost == 5 && card.TemporaryStarCost!.Cost == 2 && card.WasStarCostJustUpgraded);
            Assert(card.GetKeywordsWithSources(KeywordSources.Local).SetEquals([CardKeyword.Retain, CardKeyword.Innate]));
        });
        check("upgrade commands skip nonupgradable cards and gate Ending before bulk enumeration", () => {
            var world = World(); var card = Card<UpgradeProbeCard>(world, "c"); var injury = Card<Injury>(world, "injury"); Observe(world);
            var notifications = 0; card.Upgraded += () => notifications++;
            Execute(world, () => CardCmd.Upgrade(world, [injury, card, card, card]));
            Assert(card.CurrentUpgradeLevel == 2 && notifications == 2 && injury.CurrentUpgradeLevel == 0);
            var ending = World(CombatPhase.Ending);
            Execute(ending, () => CardCmd.Upgrade(ending, new ThrowingCardEnumerable()));
            var unknown = ending.RestoreCardCostScope(ending.RunState.Players[0], "unknown", ModelDb.GetId(typeof(UpgradeProbeCard)), PileType.Hand,
                new(3, 0, false, []), ObservationStatus.ObservedAbsent, ObservationStatus.ObservedAbsent);
            Execute(ending, () => { CardCmd.Upgrade(unknown); CardCmd.Downgrade(unknown); });
            var midDispatch = World(); var first = Card<UpgradeProbeCard>(midDispatch, "first"); var second = Card<UpgradeProbeCard>(midDispatch, "second"); Observe(midDispatch);
            first.Upgraded += () => typeof(CombatState).GetProperty(nameof(CombatState.Phase))!.SetValue(midDispatch.CombatState, CombatPhase.Ending);
            Execute(midDispatch, () => CardCmd.Upgrade(midDispatch, [first, second]));
            Assert(first.CurrentUpgradeLevel == 1 && second.CurrentUpgradeLevel == 1);
        });
        check("deck upgrades require explicit history domains and record ordered upgrades/downgrades", () => {
            var incomplete = World(combat: false); var bad = Card<UpgradeProbeCard>(incomplete, "deck", PileType.Deck); Observe(incomplete);
            incomplete.RestoreHistory(true);
            Error("state_incomplete", () => Execute(incomplete, () => CardCmd.Upgrade(bad)));
            Assert(bad.CurrentUpgradeLevel == 0);
            var world = World(combat: false); var card = Card<UpgradeProbeCard>(world, "deck", PileType.Deck); Observe(world);
            world.RestoreHistory(true); world.RestoreEmptyCardUpgradeHistory();
            Execute(world, () => { CardCmd.Upgrade(card); CardCmd.Downgrade(card); CardCmd.Upgrade(card); });
            var stats = world.RunState.CurrentMapPointHistoryEntry!.GetEntry(7);
            Assert(stats.UpgradedCards.SequenceEqual(new[] { card.Id, card.Id }) && stats.DowngradedCards.SequenceEqual(new[] { card.Id }));
            var clone = world.Clone(); Execute(clone, () => CardCmd.Upgrade((CardModel)clone.RequireModel("deck")));
            Assert(stats.UpgradedCards.Count == 2 && clone.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).UpgradedCards.Count == 3);
            Error("mechanism_not_implemented", () => WorldSnapshotCodec.Export(world));
        });
        check("failed upgrade callbacks retain only branch-local native history and mutation prefix", () => {
            var world = World(combat: false); var card = Card<UpgradeProbeCard>(world, "deck", PileType.Deck); Observe(world);
            card.ThrowInUpgrade = true; world.RestoreHistory(true); world.RestoreEmptyCardUpgradeHistory();
            var failed = world.Clone(); var sibling = world.Clone(); var failedCard = (UpgradeProbeCard)failed.RequireModel("deck");
            try { Execute(failed, () => CardCmd.Upgrade(failedCard)); throw new Exception("Failure ignored"); }
            catch (InvalidOperationException error) { Assert(error.Message == "upgrade fixture failure"); }
            Assert(failed.IsFaulted && failedCard.CurrentUpgradeLevel == 1 && failedCard.EnergyCost.GetWithModifiers(CostModifiers.None) == 2);
            Assert(failedCard.DynamicVars["Calculated"].BaseValue == 4 && failedCard.DynamicVars.CalculationBase.BaseValue == 7);
            Assert(failed.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).UpgradedCards.Count == 1);
            Assert(card.CurrentUpgradeLevel == 0 && ((CardModel)sibling.RequireModel("deck")).CurrentUpgradeLevel == 0 && world.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).UpgradedCards.Count == 0);
        });
        check("variable hydration is complete and atomic; missing variables stop upgrade before its callback", () => {
            var world = World(); var card = Card<UpgradeProbeCard>(world, "c", restoreVariables: false); Observe(world);
            Error("state_incomplete", () => _ = card.DynamicVars);
            var values = Vars(world.Models.Card<UpgradeProbeCard>()); var invalid = values.Select(v => v with { Base = 99 }).ToArray(); invalid[^1] = invalid[^1] with { Type = "unknown" };
            Error("invalid_state", () => world.RestoreCardDynamicVars(card, invalid));
            Error("state_incomplete", () => Execute(world, () => CardCmd.Upgrade(card)));
            Assert(card.CurrentUpgradeLevel == 0 && card.EnergyCost.GetWithModifiers(CostModifiers.None) == 3);
            var fresh = World(); var restored = Card<UpgradeProbeCard>(fresh, "c", restoreVariables: false); var correct = Vars(fresh.Models.Card<UpgradeProbeCard>());
            fresh.RestoreCardDynamicVars(restored, correct); correct[0] = correct[0] with { Base = 90 };
            Assert(restored.DynamicVars.CalculationBase.BaseValue == 4);
            Error("invalid_state", () => fresh.RestoreCardDynamicVars(restored, correct.Select(v => v with { Base = 1000000000 }).ToArray()));
            Assert(restored.DynamicVars.CalculationBase.BaseValue == 4);
        });
        check("calculated variables use owned combat context, native conversion and base-only upgrade detection", () => {
            foreach (var (phase, expected) in new[] { (CombatPhase.InProgress, 10m), (CombatPhase.Ending, 10m), (CombatPhase.Starting, 4m), (CombatPhase.Over, 4m) })
            {
                var world = World(phase); var card = Card<UpgradeProbeCard>(world, "c"); Observe(world);
                var calculated = (CalculatedVar)card.DynamicVars["Calculated"];
                Assert(calculated.Calculate(null) == expected && calculated.IntValue == 4 && Convert.ToDecimal(calculated) == expected);
            }
            var w = World(); var c = Card<UpgradeProbeCard>(w, "c"); Observe(w); var calc = (CalculatedVar)c.DynamicVars["Calculated"];
            c.DynamicVars.CalculationExtra.UpgradeValueBy(1); calc.RecalculateForUpgradeOrEnchant(); Assert(!calc.WasJustUpgraded);
            c.DynamicVars.CalculationBase.UpgradeValueBy(2); calc.RecalculateForUpgradeOrEnchant(); Assert(calc.WasJustUpgraded && calc.BaseValue == 6);
            var unconfigured = new CalculatedVar("other"); unconfigured.SetOwner(c);
            try { unconfigured.Calculate(null); throw new Exception("Missing multiplier accepted"); } catch (InvalidOperationException) { }
            try { unconfigured.WithMultiplier(c.InstanceMultiplier); throw new Exception("Bound model multiplier accepted"); } catch (InvalidOperationException) { }
        });
        check("star local costs preserve negative-zero display, first combat modifier and upgrade trimming", () => {
            var world = World(); var card = Card<UpgradeProbeCard>(world, "c"); var noStars = Card<Injury>(world, "no-stars"); Observe(world);
            noStars.SetStarCostUntilPlayed(0); Assert(noStars.CurrentStarCost == -1);
            noStars.SetStarCostThisTurn(2); Assert(noStars.CurrentStarCost == 2);
            card.SetStarCostThisCombat(4); card.SetStarCostThisCombat(2); Assert(card.CurrentStarCost == 2 && card.GetStarCostThisCombat() == 4);
            var events = new List<int>(); card.StarCostChanged += () => events.Add(card.CurrentStarCost);
            card.TestStarUpgrade(-2); Assert(card.BaseStarCost == 3 && card.CurrentStarCost == 2 && card.GetStarCostThisCombat() == 2 && events.Count == 1);
            var observed = new[] { new TemporaryStarCostObservation(1, false, true) };
            world.RestoreCardStarCosts(card, new(5, true, false, 7, observed)); observed[0] = observed[0] with { Cost = 8 };
            Assert(card.CurrentStarCost == 1 && card.LastStarsSpent == 7 && events.Count == 1);
            Error("invalid_state", () => world.RestoreCardStarCosts(card, new(5, true, false, 0, [new(1, true, false)])));
        });
        check("star global hooks preserve negative results and X costs require observed player stars", () => {
            var world = World(); var card = Card<UpgradeProbeCard>(world, "c"); var x = Card<XStarFixture>(world, "x");
            var relic = (StarCostFixtureRelic)world.RestoreRelic(world.RunState.Players[0], new("r", ModelDb.GetId(typeof(StarCostFixtureRelic)), false, false, 0, 1, RelicStatus.Normal));
            relic.Result = -1.9m; Observe(world);
            Execute(world, () => Assert(card.GetStarCostWithModifiers() == -1)); Assert(relic.Calls == 1);
            Error("state_incomplete", () => x.GetStarCostWithModifiers()); world.RestorePlayerStars(world.RunState.Players[0], 8);
            Assert(x.GetStarCostWithModifiers() == 8 && relic.Calls == 1);
            try { x.TestStarUpgrade(0); throw new Exception("X-star upgrade accepted"); } catch (InvalidOperationException) { }
        });
        check("canonical numeric setters match native writes, remain world-local and cannot be lost by a resource checkpoint", () => {
            var world = new SimulationWorld(CompiledRules.LoadDefaultParameters()); var canonical = world.Models.Relic<Mango>();
            canonical.DynamicVars.MaxHp.BaseValue = 17; var sibling = new SimulationWorld(CompiledRules.LoadDefaultParameters());
            Assert(canonical.DynamicVars.MaxHp.BaseValue == 17 && sibling.Models.Relic<Mango>().DynamicVars.MaxHp.BaseValue == 14);
            var branch = world.Clone(); branch.Models.Relic<Mango>().DynamicVars.MaxHp.BaseValue = 20;
            Assert(canonical.DynamicVars.MaxHp.BaseValue == 17 && branch.Models.Relic<Mango>().DynamicVars.MaxHp.BaseValue == 20);
            Error("mechanism_not_implemented", () => WorldSnapshotCodec.Export(world));
        });
        check("real Enlightenment callback uses upgraded state and existing cost mechanisms without a card-ID adapter", () => {
            var world = World(); var enlightenment = Card<Enlightenment>(world, "enlightenment"); var target = Card<UpgradeProbeCard>(world, "target"); Observe(world);
            world.ExecuteAsync(() => Effect(enlightenment), enlightenment).GetAwaiter().GetResult();
            Assert(target.EnergyCost.GetWithModifiers(CostModifiers.Local) == 1 && target.EnergyCost.EndOfTurnCleanup());
            Assert(target.EnergyCost.GetWithModifiers(CostModifiers.Local) == 3);
            var upgraded = world.Clone(); var copied = (CardModel)upgraded.RequireModel("enlightenment");
            Execute(upgraded, () => CardCmd.Upgrade(copied)); upgraded.ExecuteAsync(() => Effect(copied), copied).GetAwaiter().GetResult();
            var modifiedTarget = (CardModel)upgraded.RequireModel("target");
            Assert(modifiedTarget.EnergyCost.GetWithModifiers(CostModifiers.Local) == 1 && !modifiedTarget.EnergyCost.EndOfTurnCleanup() && !modifiedTarget.EnergyCost.AfterCardPlayedCleanup());
            Assert(enlightenment.CurrentUpgradeLevel == 0 && target.EnergyCost.GetWithModifiers(CostModifiers.Local) == 3);
            Assert(copied.GetKeywordsWithSources(KeywordSources.Local).Contains(CardKeyword.Exhaust));
        });
    }
}

public class UpgradeProbeCard : CardModel
{
    public UpgradeProbeCard() : base(3, CardType.Skill, CardRarity.Common, TargetType.Self) { }
    public override int MaxUpgradeLevel => 2;
    public override int CanonicalStarCost => 5;
    public override IEnumerable<CardKeyword> CanonicalKeywords => [CardKeyword.Retain];
    public bool ThrowInUpgrade { get; set; }
    protected override IEnumerable<DynamicVar> CanonicalVars => [new CalculationBaseVar(4), new CalculationExtraVar(2),
        new CalculatedVar("Calculated").WithMultiplier(static (card, target) => card.Owner.PlayerCombatState!.Energy)];
    public decimal InstanceMultiplier(CardModel card, Creature? target) => 1;
    public void TestStarUpgrade(int amount) => UpgradeStarCostBy(amount);
    protected override void OnUpgrade()
    {
        DynamicVars.CalculationBase.UpgradeValueBy(3); DynamicVars.CalculationExtra.UpgradeValueBy(1);
        EnergyCost.UpgradeBy(-1); UpgradeStarCostBy(-2); RemoveKeyword(CardKeyword.Retain);
        if (ThrowInUpgrade) throw new InvalidOperationException("upgrade fixture failure");
    }
    protected override void AfterDowngraded() => AddKeyword(CardKeyword.Innate);
}
public sealed class XStarFixture : CardModel
{
    public XStarFixture() : base(0, CardType.Skill, CardRarity.Common, TargetType.Self) { }
    public override bool HasStarCostX => true;
    public void TestStarUpgrade(int amount) => UpgradeStarCostBy(amount);
}
public sealed class StarCostFixtureRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public decimal Result { get; set; }
    public int Calls { get; private set; }
    public override bool TryModifyStarCost(CardModel card, decimal originalCost, out decimal modifiedCost)
    { Calls++; modifiedCost = Result; return false; }
}
internal sealed class ThrowingCardEnumerable : IEnumerable<CardModel>
{
    public IEnumerator<CardModel> GetEnumerator() => throw new Exception("Ending command enumerated cards");
    System.Collections.IEnumerator System.Collections.IEnumerable.GetEnumerator() => GetEnumerator();
}
