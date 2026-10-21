using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Combat.History.Entries;
using AutoSlay.Rules.Native.Commands;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Models.Relics;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;

internal static class ResourcePaymentTests
{
    private static void Assert(bool condition) { if (!condition) throw new Exception("Resource payment assertion failed"); }
    private static void Execute(SimulationWorld world, Func<Task> action) => world.ExecuteAsync(action).GetAwaiter().GetResult();
    private static void Error(string code, Action action)
    {
        try { action(); throw new Exception("Expected boundary: " + code); }
        catch (RuleBoundaryException error) { Assert(error.Code == code); }
    }
    private static SimulationWorld World(int? energy = 3, int? stars = 9, bool history = true, bool inventory = true,
        CombatPhase phase = CombatPhase.InProgress)
    {
        var world = new SimulationWorld(CompiledRules.LoadDefaultParameters(), new ModelRegistry(typeof(Mango).Assembly, typeof(PaymentCard).Assembly));
        world.Add(new("p", 30, 80)); var player = world.RestorePlayer(7, "p", true);
        world.RestoreCombat("payment", phase, "p"); world.RestorePlayerCombatPresence(player, ObservationStatus.ObservedPresent);
        if (energy.HasValue) world.RestorePlayerEnergy(player, energy.Value);
        if (stars.HasValue) world.RestorePlayerStars(player, stars.Value);
        if (inventory) Observe(world);
        world.RestoreCombatTimeline(1, CombatSide.Player, new Dictionary<ulong, int> { [7] = 1 });
        if (history) world.RestoreEmptyCombatResourceHistory();
        return world;
    }
    private static void Observe(SimulationWorld world)
    {
        var player = world.RunState.Players[0];
        world.RestoreCombatHookInventory(new(["p"], [], [new(7, [], player.Relics.Select(r => r.InstanceId!).ToArray(), [],
            ObservationStatus.ObservedPresent, [], player.PlayerCombatState!.AllPiles.Select(p => p.Cards.Select(c => c.InstanceId!).ToArray()).ToArray())],
            [], [], [], [], ObservationStatus.ObservedAbsent, new() { ["p"] = [] }));
    }
    private static T Card<T>(SimulationWorld world, int energy, int stars) where T : CardModel
    {
        var card = (T)world.RestoreCardCostScope(world.RunState.Players[0], "c", ModelDb.GetId(typeof(T)), PileType.Hand,
            new(energy, 0, false, []), ObservationStatus.ObservedAbsent, ObservationStatus.ObservedAbsent);
        world.RestoreCardStarCosts(card, new(stars, true, false, 0, [])); Observe(world); return card;
    }
    private static PaymentRelic Relic(SimulationWorld world, string id = "r")
    {
        var relic = (PaymentRelic)world.RestoreRelic(world.RunState.Players[0],
            new(id, ModelDb.GetId(typeof(PaymentRelic)), false, false, 0, 1, RelicStatus.Normal)); Observe(world); return relic;
    }
    public static void Run(Action<string, Action> check)
    {
        check("star mutations record actual truncated deltas before events but gain hooks receive requested integer amounts including zero", () => {
            var world = World(stars: 2); var player = world.RunState.Players[0]; var state = player.PlayerCombatState!;
            var relic = Relic(world); var order = new List<string>(); var history = world.CombatState!.History;
            history.Changed += () => order.Add("history:" + state.Stars);
            state.StarsChanged += (old, next) => { Assert(history.ResourceEntries.Count > 0); order.Add($"stars:{old}:{next}"); };
            Execute(world, async () => {
                await PlayerCmd.GainStars(1.9m, player); await PlayerCmd.GainStars(.2m, player); await PlayerCmd.GainStars(0, player);
                await PlayerCmd.LoseStars(.1m, player); await PlayerCmd.SetStars(5.4m, player); await PlayerCmd.SetStars(-1, player);
            });
            Assert(state.Stars == 0 && relic.Gained.SequenceEqual([1, 0, 0, 3]) && relic.Spent.Count == 0);
            Assert(history.ResourceEntries.Cast<StarsModifiedEntry>().Select(e => e.Amount).SequenceEqual([1, -1, 3, -5]));
            Assert(order.SequenceEqual(["history:3", "stars:2:3", "history:2", "stars:3:2", "history:5", "stars:2:5", "history:0", "stars:5:0"]));
            Assert(relic.GainSnapshots.SequenceEqual([3, 3, 3, 5]));
        });
        check("star gain permission short-circuits before unknown resources and negative amounts retain native validation", () => {
            var blocked = World(stars: null, history: false); var first = Relic(blocked); var second = Relic(blocked, "second"); first.BlockGain = true;
            Execute(blocked, () => PlayerCmd.GainStars(-1, blocked.RunState.Players[0]));
            Assert(first.GainChecks.SequenceEqual([-1m]) && second.GainChecks.Count == 0 && first.Gained.Count == 0);
            Error("state_incomplete", () => { _ = blocked.RunState.Players[0].PlayerCombatState!.Stars; });
            var allowed = World(); var relic = Relic(allowed);
            try { Execute(allowed, () => PlayerCmd.GainStars(-1, allowed.RunState.Players[0])); throw new Exception("Negative gain accepted"); }
            catch (ArgumentException) { Assert(allowed.IsFaulted && relic.Gained.Count == 0); }
            Assert(allowed.RunState.Players[0].PlayerCombatState!.Stars == 9 && allowed.CombatState!.History.ResourceEntries.Count == 0);
            var negativeLoss = World();
            try { Execute(negativeLoss, () => PlayerCmd.LoseStars(-1, negativeLoss.RunState.Players[0])); throw new Exception("Negative loss accepted"); }
            catch (ArgumentException) { Assert(negativeLoss.IsFaulted); }
        });
        check("only Ending suppresses star commands and star arithmetic has no invented energy cap", () => {
            var ending = World(energy: null, stars: null, inventory: false, history: false, phase: CombatPhase.Ending);
            Execute(ending, async () => {
                var p = ending.RunState.Players[0]; await PlayerCmd.GainStars(-1, p); await PlayerCmd.LoseStars(-1, p); await PlayerCmd.SetStars(5, p);
            });
            foreach (var phase in new[] { CombatPhase.NotStarted, CombatPhase.Over, CombatPhase.Starting, CombatPhase.InProgress })
            {
                var world = World(stars: 999999999, phase: phase); var player = world.RunState.Players[0];
                Execute(world, () => PlayerCmd.GainStars(2.9m, player)); Assert(player.PlayerCombatState!.Stars == 1000000001);
            }
        });
        check("resource affordability combines reason flags and converts only excess energy at two stars per energy", () => {
            var world = World(energy: 2, stars: 2); var card = Card<PaymentCard>(world, 5, 3); var relic = Relic(world);
            Execute(world, () => {
                Assert(!card.Owner.PlayerCombatState!.HasEnoughResourcesFor(card, out var reason));
                Assert(reason == (UnplayableReason.EnergyCostTooHigh | UnplayableReason.StarCostTooHigh));
                relic.ConvertEnergy = true;
                Assert(!card.Owner.PlayerCombatState.HasEnoughResourcesFor(card, out reason) && reason == UnplayableReason.StarCostTooHigh);
                return Task.CompletedTask;
            });
            world.RestorePlayerStars(card.Owner, 9);
            Execute(world, () => { Assert(card.Owner.PlayerCombatState!.HasEnoughResourcesFor(card, out var reason) && reason == UnplayableReason.None); return Task.CompletedTask; });
            Assert(card.Owner.PlayerCombatState!.Energy == 2 && card.Owner.PlayerCombatState.Stars == 9 && world.CombatState!.History.ResourceEntries.Count == 0);
        });
        check("payment records energy before deduction then star delta before event and notifies hooks in native order", () => {
            var world = World(); var card = Card<PaymentCard>(world, 5, 1); var relic = Relic(world); relic.ConvertEnergy = true;
            var state = card.Owner.PlayerCombatState!; var order = new List<string>(); var history = world.CombatState!.History;
            history.Changed += () => order.Add($"history:{state.Energy}:{state.Stars}");
            state.EnergyChanged += (old, next) => order.Add($"energy:{old}:{next}");
            state.StarsChanged += (old, next) => order.Add($"stars:{old}:{next}");
            Execute(world, async () => { Assert(await card.SpendResources() == (3, 5)); });
            Assert(order.SequenceEqual(["history:3:9", "energy:3:0", "history:0:4", "stars:9:4"]));
            Assert(history.ResourceEntries[0] is EnergySpentEntry { Amount: 3 } && history.ResourceEntries[1] is StarsModifiedEntry { Amount: -5 });
            Assert(relic.PaymentSnapshots.SequenceEqual(["energy:3:0:9:0", "stars:5:0:4:5"]));
            Assert(card.LastStarsSpent == 5 && history.ResourceEntries.All(e => e.HappenedThisTurn(world.CombatState)));
        });
        check("payment captures X before callbacks and zero payment still notifies energy hooks without inventing star history", () => {
            var xWorld = World(energy: 4, stars: 6); var x = Card<PaymentXCard>(xWorld, -1, 0); var relic = Relic(xWorld);
            Execute(xWorld, async () => { Assert(await x.SpendResources() == (4, 6)); });
            Assert(x.EnergyCost.CapturedXValue == 4 && x.LastStarsSpent == 6 && x.Owner.PlayerCombatState!.Stars == 0);
            Assert(relic.CapturedEnergy.SequenceEqual([4]));
            var zero = World(stars: null, history: false); var card = Card<PaymentCard>(zero, 0, -1); var zeroRelic = Relic(zero);
            zeroRelic.SkipStarRead = true;
            Execute(zero, async () => { Assert(await card.SpendResources() == (0, 0)); });
            Assert(zeroRelic.EnergySpent.SequenceEqual([0]) && zeroRelic.Spent.Count == 0 && card.LastStarsSpent == 0);
            Error("state_incomplete", () => { _ = zero.CombatState!.History.ResourceEntries; });
        });
        check("energy-spent callbacks can change stars before the already computed star payment", () => {
            var world = World(); var card = Card<PaymentCard>(world, 5, 1); var relic = Relic(world);
            relic.ConvertEnergy = true; relic.GainAfterEnergy = 2;
            Execute(world, async () => { Assert(await card.SpendResources() == (3, 5)); });
            Assert(card.Owner.PlayerCombatState!.Stars == 6 && card.LastStarsSpent == 5);
            Assert(world.CombatState!.History.ResourceEntries.Select(e => e switch {
                EnergySpentEntry energy => "energy:" + energy.Amount, StarsModifiedEntry stars => "stars:" + stars.Amount, _ => "?"
            }).SequenceEqual(["energy:3", "stars:2", "stars:-5"]));
        });
        check("payment keeps native requested amounts separate from clamped actual deductions and has no implicit legality gate", () => {
            var world = World(energy: 2, stars: 1); var card = Card<PaymentCard>(world, 5, 3); var relic = Relic(world);
            Execute(world, async () => { Assert(await card.SpendResources() == (5, 3)); });
            Assert(card.Owner.PlayerCombatState!.Energy == 0 && card.Owner.PlayerCombatState.Stars == 0 && card.LastStarsSpent == 3);
            Assert(world.CombatState!.History.ResourceEntries[0] is EnergySpentEntry { Amount: 5 }
                && world.CombatState.History.ResourceEntries[1] is StarsModifiedEntry { Amount: -1 });
            Assert(relic.EnergySpent.SequenceEqual([5]) && relic.Spent.SequenceEqual([3]));
        });
        check("payment in Ending still deducts directly while phase-gated conversion and notifications are skipped", () => {
            var world = World(phase: CombatPhase.Ending); var card = Card<PaymentCard>(world, 5, 1); var relic = Relic(world);
            relic.ConvertEnergy = true;
            Execute(world, async () => { Assert(await card.SpendResources() == (5, 1)); });
            Assert(card.Owner.PlayerCombatState!.Energy == 0 && card.Owner.PlayerCombatState.Stars == 8 && card.LastStarsSpent == 1);
            Assert(relic.EnergySpent.Count == 0 && relic.Spent.Count == 0);
            Assert(world.CombatState!.History.ResourceEntries[0] is EnergySpentEntry { Amount: 5 }
                && world.CombatState.History.ResourceEntries[1] is StarsModifiedEntry { Amount: -1 });
        });
        check("failed star-gain notification keeps the mutation and history prefix isolated from source and sibling worlds", () => {
            var source = World(); var relic = Relic(source); relic.ThrowAfterGain = true;
            var failed = source.Clone(); var sibling = source.Clone();
            try { Execute(failed, () => PlayerCmd.GainStars(2, failed.RunState.Players[0])); throw new Exception("Failed gain hook accepted"); }
            catch (InvalidOperationException error) { Assert(error.Message == "star gain fixture failure"); }
            Assert(failed.IsFaulted && failed.RunState.Players[0].PlayerCombatState!.Stars == 11
                && failed.CombatState!.History.ResourceEntries.Single() is StarsModifiedEntry { Amount: 2 });
            Assert(source.RunState.Players[0].PlayerCombatState!.Stars == 9 && sibling.RunState.Players[0].PlayerCombatState!.Stars == 9);
            Assert(source.CombatState!.History.ResourceEntries.Count == 0 && relic.Gained.Count == 0 && !sibling.IsFaulted);
        });
        check("resource history and star observations remain explicit and missing history stops payment before mutation", () => {
            var world = World(history: false); var card = Card<PaymentCard>(world, 1, 2); var relic = Relic(world);
            world.RestoreEmptyCombatPowerHistory();
            Error("state_incomplete", () => Execute(world, async () => { await card.SpendResources(); }));
            Assert(card.Owner.PlayerCombatState!.Energy == 3 && card.Owner.PlayerCombatState.Stars == 9 && relic.EnergySpent.Count == 0);
            var missingStars = World(stars: null); var other = Card<PaymentCard>(missingStars, 1, 2);
            Error("state_incomplete", () => Execute(missingStars, async () => { await other.SpendResources(); }));
            Assert(other.Owner.PlayerCombatState!.Energy == 3 && missingStars.CombatState!.History.ResourceEntries.Count == 0);
            var noHistory = World(history: false); var player = noHistory.RunState.Players[0];
            Error("state_incomplete", () => Execute(noHistory, () => PlayerCmd.GainStars(1, player)));
            Assert(player.PlayerCombatState!.Stars == 9);
            var duplicate = World(); Error("invalid_state", duplicate.RestoreEmptyCombatResourceHistory);
            Error("mechanism_not_implemented", () => { WorldSnapshotCodec.Export(duplicate); });
        });
        check("failed payment leaves its native energy prefix only in that branch and forked resource history retains owned identity", () => {
            var source = World(); var card = Card<PaymentCard>(source, 2, 3); var relic = Relic(source); relic.ThrowAfterEnergy = true;
            var failed = source.Clone(); var sibling = source.Clone(); var failedCard = (PaymentCard)failed.RequireModel("c");
            try { Execute(failed, async () => { await failedCard.SpendResources(); }); throw new Exception("Failed hook accepted"); }
            catch (InvalidOperationException error) { Assert(error.Message == "payment fixture failure"); }
            Assert(failed.IsFaulted && failedCard.Owner.PlayerCombatState!.Energy == 1 && failedCard.Owner.PlayerCombatState.Stars == 9 && failedCard.LastStarsSpent == 0);
            Assert(source.CombatState!.History.ResourceEntries.Count == 0 && source.RunState.Players[0].PlayerCombatState!.Energy == 3);
            ((PaymentRelic)sibling.RequireModel("r")).ThrowAfterEnergy = false;
            Execute(sibling, async () => { await ((PaymentCard)sibling.RequireModel("c")).SpendResources(); });
            var fork = sibling.Clone(); var entries = fork.CombatState!.History.ResourceEntries;
            Assert(entries.Count == 2 && ReferenceEquals(entries[0].Actor, fork.Get("p")) && ReferenceEquals(entries[0].History, fork.CombatState.History));
            Execute(fork, () => PlayerCmd.GainStars(2, fork.RunState.Players[0]));
            Assert(entries.Count == 3 && sibling.CombatState!.History.ResourceEntries.Count == 2 && relic.EnergySpent.Count == 0);
        });
    }
}

public class PaymentCard : CardModel
{
    public PaymentCard() : base(5, CardType.Skill, CardRarity.Common, TargetType.Self) { }
    public override int CanonicalStarCost => 1;
}
public sealed class PaymentXCard : CardModel
{
    public PaymentXCard() : base(-1, CardType.Skill, CardRarity.Common, TargetType.Self) { }
    protected override bool HasEnergyCostX => true;
    public override bool HasStarCostX => true;
    public override int CanonicalStarCost => -1;
}
public sealed class PaymentRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public bool BlockGain { get; set; }
    public bool ConvertEnergy { get; set; }
    public bool ThrowAfterEnergy { get; set; }
    public bool ThrowAfterGain { get; set; }
    public bool SkipStarRead { get; set; }
    public decimal GainAfterEnergy { get; set; }
    public List<decimal> GainChecks { get; private set; } = [];
    public List<int> Gained { get; private set; } = [];
    public List<int> GainSnapshots { get; private set; } = [];
    public List<int> Spent { get; private set; } = [];
    public List<int> EnergySpent { get; private set; } = [];
    public List<int> CapturedEnergy { get; private set; } = [];
    public List<string> PaymentSnapshots { get; private set; } = [];
    protected override void DeepCloneFields()
    {
        base.DeepCloneFields(); GainChecks = new(GainChecks); Gained = new(Gained); GainSnapshots = new(GainSnapshots);
        Spent = new(Spent); EnergySpent = new(EnergySpent); CapturedEnergy = new(CapturedEnergy); PaymentSnapshots = new(PaymentSnapshots);
    }
    public override bool ShouldGainStars(decimal amount, Player player) { GainChecks.Add(amount); return !BlockGain; }
    public override bool ShouldPayExcessEnergyCostWithStars(Player player) => ConvertEnergy;
    public override Task AfterStarsGained(int amount, Player gainer)
    {
        Gained.Add(amount); GainSnapshots.Add(gainer.PlayerCombatState!.Stars);
        if (ThrowAfterGain) throw new InvalidOperationException("star gain fixture failure");
        return Task.CompletedTask;
    }
    public override Task AfterStarsSpent(int amount, Player spender)
    {
        Spent.Add(amount); var card = spender.PlayerCombatState!.Hand.Cards.Single();
        PaymentSnapshots.Add($"stars:{amount}:{spender.PlayerCombatState.Energy}:{spender.PlayerCombatState.Stars}:{card.LastStarsSpent}"); return Task.CompletedTask;
    }
    public override async Task AfterEnergySpent(CardModel card, int amount)
    {
        EnergySpent.Add(amount);
        if (card.EnergyCost.CostsX) CapturedEnergy.Add(card.EnergyCost.CapturedXValue);
        if (!SkipStarRead) PaymentSnapshots.Add($"energy:{amount}:{card.Owner.PlayerCombatState!.Energy}:{card.Owner.PlayerCombatState.Stars}:{card.LastStarsSpent}");
        if (ThrowAfterEnergy) throw new InvalidOperationException("payment fixture failure");
        if (GainAfterEnergy != 0) await PlayerCmd.GainStars(GainAfterEnergy, card.Owner);
    }
}
