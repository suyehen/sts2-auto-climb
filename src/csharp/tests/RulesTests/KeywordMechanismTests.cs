using System.Text;
using System.Text.Json.Nodes;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Models.Cards;
using AutoSlay.Rules.Native.Models.Relics;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;

internal static class KeywordMechanismTests
{
    private static void Assert(bool condition) { if (!condition) throw new Exception("Keyword mechanism assertion failed"); }
    private static void Error(string code, Action action)
    {
        try { action(); throw new Exception("Expected boundary " + code); }
        catch (RuleBoundaryException e) { Assert(e.Code == code); }
    }
    private static SimulationWorld World(CombatPhase phase = CombatPhase.InProgress)
    {
        var world = new SimulationWorld(CompiledRules.LoadDefaultParameters(), new ModelRegistry(typeof(Mango).Assembly, typeof(KeywordFixtureRelic).Assembly));
        world.Add(new("p", 30, 80)); world.RestorePlayer(7, "p", true); world.RestoreCombat("keywords", phase, "p");
        world.RestorePlayerCombatPresence(world.RunState.Players[0], ObservationStatus.ObservedPresent);
        world.RestoreCombatHookInventory(Observation(world)); return world;
    }
    private static CombatHookObservation Observation(SimulationWorld world) => new(["p"], [],
        [new(7, world.RunState.Players[0].Deck.Cards.Select(c => c.InstanceId!).ToArray(),
            world.RunState.Players[0].Relics.Select(r => r.InstanceId!).ToArray(), [], ObservationStatus.ObservedPresent, [],
            world.RunState.Players[0].PlayerCombatState?.AllPiles.Select(p => p.Cards.Select(c => c.InstanceId!).ToArray()).ToArray() ?? [[], [], [], [], []])],
        [], [], [], [], ObservationStatus.ObservedAbsent, new() { ["p"] = [] });
    private static CardModel Card<T>(SimulationWorld world, string id, int cost = -1, bool observeUpgrade = true) where T : CardModel
    {
        var card = world.RestoreCardCostScope(world.RunState.Players[0], id, ModelDb.GetId(typeof(T)), PileType.Hand,
            new(cost, 0, false, []), ObservationStatus.ObservedAbsent, ObservationStatus.ObservedAbsent);
        if (observeUpgrade) world.RestoreCardUpgradeLevel(card, 0);
        world.RestoreCardDynamicVars(card, []); world.RestoreCardStarCosts(card, new(-1, false, false, 0, []));
        return card;
    }
    private static KeywordFixtureRelic Relic(SimulationWorld world, string id) => (KeywordFixtureRelic)world.RestoreRelic(world.RunState.Players[0],
        new(id, ModelDb.GetId(typeof(KeywordFixtureRelic)), false, false, 0, 1, RelicStatus.Normal));
    private static void Execute(SimulationWorld world, Action action) => world.ExecuteAsync(() => { action(); return Task.CompletedTask; }).GetAwaiter().GetResult();
    public static void Run(Action<string, Action> check)
    {
        check("real Injury and Dazed source classes register through the parameterized factory with native local keywords", () => {
            var parameters = CompiledRules.LoadDefaultParameters(); var world = new SimulationWorld(parameters);
            var injury = world.Models.Card<Injury>(); var dazed = world.Models.Card<Dazed>();
            Assert(injury.EnergyCost.Canonical == -1 && injury.Keywords.SetEquals([CardKeyword.Unplayable]));
            Assert(dazed.EnergyCost.Canonical == -1 && dazed.Keywords.SetEquals([CardKeyword.Ethereal, CardKeyword.Unplayable]));
            Assert(injury.MaxUpgradeLevel == 0 && !injury.IsUpgradable && !dazed.IsUpgraded);
            Error("mechanism_not_implemented", () => WorldSnapshotCodec.Export(world));
            var data = JsonNode.Parse(File.ReadAllBytes(Path.Combine(Environment.CurrentDirectory, "src/csharp/rules/Generated/rule-parameters.json")))!;
            var entry = data["entries"]!.AsArray().Single(e => e!["key"]!.GetValue<string>().Contains("Injury.ctor.CanonicalEnergyCost"));
            entry!["value"] = 2;
            var changed = CompiledRules.LoadParameters(Encoding.UTF8.GetBytes(data.ToJsonString()));
            var updatedWorld = new SimulationWorld(changed); var updated = updatedWorld.Models.Card<Injury>();
            Assert(updated.EnergyCost.Canonical == 2 && updated.Keywords.Contains(CardKeyword.Unplayable));
            Assert(updatedWorld.Models.Card<Dazed>().EnergyCost.Canonical == -1 && injury.EnergyCost.Canonical == -1);
            Assert(parameters.BehaviorSha256 == changed.BehaviorSha256 && parameters.ContractSha256 == changed.ContractSha256 && parameters.Sha256 != changed.Sha256);
            Assert(typeof(Injury).GetConstructor(Type.EmptyTypes) == null && typeof(Injury).GetConstructor([typeof(RuleParameters)]) != null);
        });
        check("restored keyword state remains unknown until observed and known absence is not canonical fallback", () => {
            var world = World(); var card = Card<Dazed>(world, "dazed"); var relic = Relic(world, "r"); relic.Add = CardKeyword.Retain;
            world.RestoreCombatHookInventory(Observation(world));
            Error("state_incomplete", () => card.GetKeywordsWithSources(KeywordSources.Local));
            Error("state_incomplete", () => Execute(world, () => _ = card.Keywords));
            Assert(relic.Calls == 0);
            var fresh = World(); var knownEmpty = Card<Dazed>(fresh, "empty", observeUpgrade: false);
            var none = knownEmpty.GetKeywordsWithSources(KeywordSources.None); Assert(none.Count == 0);
            fresh.RestoreCardKeywords(knownEmpty, []);
            Error("state_incomplete", () => _ = knownEmpty.IsUpgradable);
            Error("invalid_state", () => fresh.RestoreCardUpgradeLevel(knownEmpty, 1));
            fresh.RestoreCardUpgradeLevel(knownEmpty, 0); Assert(!knownEmpty.IsUpgradable);
            Assert(knownEmpty.GetKeywordsWithSources(KeywordSources.Local).Count == 0 && knownEmpty.CanonicalKeywords.Count() == 2);
            var observed = new[] { CardKeyword.Unplayable }; fresh.RestoreCardKeywords(knownEmpty, observed); observed[0] = CardKeyword.Eternal;
            Assert(knownEmpty.GetKeywordsWithSources(KeywordSources.Local).SetEquals([CardKeyword.Unplayable]));
            Error("invalid_state", () => fresh.RestoreCardKeywords(knownEmpty, [CardKeyword.Retain, CardKeyword.Retain]));
            Error("invalid_state", () => fresh.RestoreCardKeywords(knownEmpty, [(CardKeyword)999]));
            Assert(knownEmpty.GetKeywordsWithSources(KeywordSources.Local).SetEquals([CardKeyword.Unplayable]));
        });
        check("local keyword edits notify on native no-op changes while global contributions are recomputed without persistence", () => {
            var world = World(); var card = Card<Injury>(world, "injury"); world.RestoreCardKeywords(card, [CardKeyword.Unplayable]);
            var relic = Relic(world, "r"); relic.Add = CardKeyword.Ethereal; world.RestoreCombatHookInventory(Observation(world));
            var changes = 0; var finished = 0; card.KeywordsChanged += () => changes++; relic.ExecutionFinished += _ => finished++;
            card.AddKeyword(CardKeyword.Retain); card.AddKeyword(CardKeyword.Retain); card.RemoveKeyword(CardKeyword.Innate);
            Assert(changes == 3 && card.GetKeywordsWithSources(KeywordSources.Local).SetEquals([CardKeyword.Unplayable, CardKeyword.Retain]));
            Execute(world, () => {
                Assert(card.Keywords.SetEquals([CardKeyword.Unplayable, CardKeyword.Retain, CardKeyword.Ethereal]));
                Assert(card.GetKeywordsWithSources(KeywordSources.Global).SetEquals([CardKeyword.Ethereal]));
            });
            Assert(relic.Calls == 2 && changes == 3 && finished == 0);
            var copyForCreation = (CardModel)card.MutableClone();
            Assert(copyForCreation.GetKeywordsWithSources(KeywordSources.Local).SetEquals([CardKeyword.Unplayable, CardKeyword.Retain]));
            copyForCreation.AddKeyword(CardKeyword.Exhaust); Assert(changes == 3 && !card.GetKeywordsWithSources(KeywordSources.Local).Contains(CardKeyword.Exhaust));
            relic.Owner.DeactivateHooks(); Execute(world, () => Assert(!card.Keywords.Contains(CardKeyword.Ethereal)));
            Assert(card.GetKeywordsWithSources(KeywordSources.Local).Contains(CardKeyword.Retain));
        });
        check("keyword hooks retain raw native ending-phase iteration and ordered removals", () => {
            var world = World(CombatPhase.Ending); var card = Card<Dazed>(world, "dazed", cost: 2);
            world.RestoreCardKeywords(card, [CardKeyword.Unplayable]);
            var first = Relic(world, "first"); var second = Relic(world, "second"); first.Add = CardKeyword.Ethereal; second.Remove = CardKeyword.Ethereal;
            world.RestoreCombatHookInventory(Observation(world));
            Execute(world, () => {
                Assert(card.EnergyCost.GetWithModifiers(CostModifiers.All) == 2);
                Assert(card.Keywords.SetEquals([CardKeyword.Unplayable]));
            });
            Assert(first.Calls == 1 && second.Calls == 1);
            second.Owner.DeactivateHooks(); Execute(world, () => Assert(card.Keywords.SetEquals([CardKeyword.Unplayable])));
        });
        check("generic forks isolate observed/unknown keyword sets and local events without rerunning constructors", () => {
            var world = World(); var card = Card<Injury>(world, "known"); var unknown = Card<Dazed>(world, "unknown");
            world.RestoreCardKeywords(card, [CardKeyword.Unplayable, CardKeyword.Retain]); var relic = Relic(world, "r"); relic.Add = CardKeyword.Ethereal;
            world.RestoreCombatHookInventory(Observation(world)); var branch = world.Clone(); var sibling = world.Clone();
            var copied = (CardModel)branch.RequireModel("known"); copied.RemoveKeyword(CardKeyword.Retain);
            Assert(card.GetKeywordsWithSources(KeywordSources.Local).Contains(CardKeyword.Retain));
            Assert(((CardModel)sibling.RequireModel("known")).GetKeywordsWithSources(KeywordSources.Local).Contains(CardKeyword.Retain));
            Error("state_incomplete", () => ((CardModel)branch.RequireModel("unknown")).GetKeywordsWithSources(KeywordSources.Local));
            branch.RestoreCardKeywords((CardModel)branch.RequireModel("unknown"), []);
            Error("state_incomplete", () => unknown.GetKeywordsWithSources(KeywordSources.Local));
            Execute(branch, () => Assert(copied.Keywords.SetEquals([CardKeyword.Unplayable, CardKeyword.Ethereal])));
            Assert(relic.Calls == 0 && ((KeywordFixtureRelic)branch.RequireModel("r")).Calls == 1);
            Assert(branch.RunState.Players[0].PlayerCombatState!.Hand.UpgradableCardCount == 0);
        });
    }
}

public sealed class KeywordFixtureRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public CardKeyword? Add { get; set; }
    public CardKeyword? Remove { get; set; }
    public int Calls { get; private set; }
    public override bool TryModifyKeywordsInCombat(CardModel card, ISet<CardKeyword> keywords)
    {
        Calls++;
        if (card.Owner != Owner) return false;
        if (Add is { } added) keywords.Add(added);
        if (Remove is { } removed) keywords.Remove(removed);
        return false; // Native dispatch consumes the set mutation even when this return value is false.
    }
}
