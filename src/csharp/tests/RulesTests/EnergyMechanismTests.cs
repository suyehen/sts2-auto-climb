using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Commands;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.GameActions.Multiplayer;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Models.Powers;
using AutoSlay.Rules.Native.Models.Relics;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;

internal static class EnergyMechanismTests
{
    private static void Assert(bool condition) { if (!condition) throw new Exception("Energy command assertion failed"); }
    private static void Execute(SimulationWorld world, Func<Task> action) => world.ExecuteAsync(action).GetAwaiter().GetResult();
    private static SimulationWorld World(int? energy = 3, CombatPhase phase = CombatPhase.InProgress, bool inventory = true, int players = 1)
    {
        var world = new SimulationWorld(CompiledRules.LoadDefaultParameters(), new ModelRegistry(typeof(Mango).Assembly, typeof(EnergyGainFixture).Assembly));
        for (var i = 0; i < players; i++) { world.Add(new("p" + i, 30, 80)); world.RestorePlayer((ulong)(7 + i), "p" + i, true); }
        world.RestoreCombat("energy", phase, world.Creatures.Select(c => c.InstanceId).ToArray());
        if (inventory) world.RestoreCombatHookInventory(Observation(world));
        if (energy != null)
            foreach (var player in world.RunState.Players)
            { world.RestorePlayerCombatPresence(player, ObservationStatus.ObservedPresent); world.RestorePlayerEnergy(player, energy.Value); }
        return world;
    }
    private static CombatHookObservation Observation(SimulationWorld world) => new(world.Creatures.Select(c => c.InstanceId).ToArray(), [],
        world.RunState.Players.Select(p => new PlayerHookDomains(p.NetId, [], p.Relics.Select(r => r.InstanceId!).ToArray(), [],
            ObservationStatus.ObservedPresent, [], [[], [], [], [], []])).ToArray(), [], [], [], [], ObservationStatus.ObservedAbsent,
        world.Creatures.ToDictionary(c => c.InstanceId, c => (string[]?)c.Powers.Select(p => p.InstanceId!).ToArray()));
    private static EnergyGainFixture Relic(SimulationWorld world, string id) => (EnergyGainFixture)world.RestoreRelic(world.RunState.Players[0],
        new(id, ModelDb.GetId(typeof(EnergyGainFixture)), false, false, 0, 1, RelicStatus.Normal));
    private static NoEnergyGainPower Block(SimulationWorld world) => (NoEnergyGainPower)world.RestorePower(world.Get("p0"),
        new("block", ModelDb.GetId(typeof(NoEnergyGainPower)), 1, 1, false, null, null));
    public static void Run(Action<string, Action> check)
    {
        check("native energy commands notify integer-changing modifiers before gain and SetEnergy uses that gain pipeline", () => {
            var world = World(); var player = world.RunState.Players[0]; var first = Relic(world, "a"); var second = Relic(world, "b");
            first.Delta = .4m; second.Delta = 1.7m; world.RestoreCombatHookInventory(Observation(world));
            var finishedA = 0; var finishedB = 0; first.ExecutionFinished += _ => finishedA++; second.ExecutionFinished += _ => finishedB++;
            Execute(world, () => PlayerCmd.GainEnergy(1.2m, player));
            Assert(player.PlayerCombatState!.Energy == 6 && first.AfterCount == 0 && second.AfterCount == 1);
            Assert(second.AfterEnergy.SequenceEqual([3]) && finishedA == 0 && finishedB == 1);
            Execute(world, () => PlayerCmd.SetEnergy(7, player)); Assert(player.PlayerCombatState.Energy == 9);
            Execute(world, () => PlayerCmd.LoseEnergy(.9m, player)); Assert(player.PlayerCombatState.Energy == 8);
            Execute(world, () => PlayerCmd.SetEnergy(-2, player)); Assert(player.PlayerCombatState.Energy == 0);
        });
        check("native energy early exits do not require unused state and only Ending suppresses positive commands", () => {
            var missing = World(energy: null, inventory: false); var player = missing.RunState.Players[0];
            Execute(missing, async () => { await PlayerCmd.GainEnergy(0, player); await PlayerCmd.GainEnergy(-1, player); await PlayerCmd.LoseEnergy(-1, player); });
            var ending = World(energy: null, inventory: false, phase: CombatPhase.Ending); var endingPlayer = ending.RunState.Players[0];
            Execute(ending, async () => { await PlayerCmd.GainEnergy(5, endingPlayer); await PlayerCmd.LoseEnergy(5, endingPlayer); await PlayerCmd.SetEnergy(8, endingPlayer); });
            foreach (var phase in new[] { CombatPhase.NotStarted, CombatPhase.Over, CombatPhase.Starting })
            {
                var world = World(phase: phase); var p = world.RunState.Players[0];
                Execute(world, () => PlayerCmd.GainEnergy(2, p)); Assert(p.PlayerCombatState!.Energy == 5);
            }
        });
        check("source-synchronized NoEnergyGainPower blocks only its owner and removes through the shared command", () => {
            var world = World(players: 2); var blocked = Block(world); var first = world.RunState.Players[0]; var second = world.RunState.Players[1];
            world.RestoreCombatHookInventory(Observation(world));
            Execute(world, async () => { await PlayerCmd.GainEnergy(2, first); await PlayerCmd.GainEnergy(2, second); });
            Assert(first.PlayerCombatState!.Energy == 3 && second.PlayerCombatState!.Energy == 5);
            Execute(world, () => blocked.AfterSideTurnEnd(new ThrowingPlayerChoiceContext(), CombatSide.Player, [second.Creature]));
            Assert(first.Creature.HasPower<NoEnergyGainPower>());
            Execute(world, () => blocked.AfterSideTurnEnd(new ThrowingPlayerChoiceContext(), CombatSide.Enemy, [first.Creature]));
            Assert(!first.Creature.HasPower<NoEnergyGainPower>());
            Execute(world, () => PlayerCmd.GainEnergy(2, first)); Assert(first.PlayerCombatState.Energy == 5);
        });
        check("blocked energy gains do not invent or demand unused energy while missing domains stop affected callbacks", () => {
            var blocked = World(energy: null); Block(blocked); blocked.RestoreCombatHookInventory(Observation(blocked));
            Execute(blocked, () => PlayerCmd.GainEnergy(2, blocked.RunState.Players[0]));
            try { _ = blocked.RunState.Players[0].PlayerCombatState!.Energy; throw new Exception("Missing energy invented"); }
            catch (RuleBoundaryException e) { Assert(e.Code == "state_incomplete"); }
            var missing = World(inventory: false); var relic = Relic(missing, "r");
            try { Execute(missing, () => PlayerCmd.GainEnergy(2, missing.RunState.Players[0])); throw new Exception("Missing hook inventory accepted"); }
            catch (RuleBoundaryException e) { Assert(e.Code == "state_incomplete"); }
            Assert(relic.ModifiedInputs.Count == 0 && missing.RunState.Players[0].PlayerCombatState!.Energy == 3);
        });
        check("energy callback failures fault only that branch before addition and preserve source/sibling state", () => {
            var source = World(); var relic = Relic(source, "r"); relic.Delta = 1; relic.ThrowAfter = true;
            source.RestoreCombatHookInventory(Observation(source)); var failed = source.Clone(); var sibling = source.Clone();
            try { Execute(failed, () => PlayerCmd.GainEnergy(2, failed.RunState.Players[0])); throw new Exception("Callback failure ignored"); }
            catch (InvalidOperationException e) { Assert(e.Message == "energy fixture failure"); }
            Assert(failed.IsFaulted && failed.RunState.Players[0].PlayerCombatState!.Energy == 3);
            Assert(((EnergyGainFixture)failed.RequireModel("r")).AfterCount == 1 && relic.AfterCount == 0);
            Assert(!source.IsFaulted && !sibling.IsFaulted && sibling.RunState.Players[0].PlayerCombatState!.Energy == 3);
            ((EnergyGainFixture)sibling.RequireModel("r")).ThrowAfter = false;
            Execute(sibling, () => PlayerCmd.GainEnergy(2, sibling.RunState.Players[0]));
            Assert(sibling.RunState.Players[0].PlayerCombatState!.Energy == 6 && source.RunState.Players[0].PlayerCombatState!.Energy == 3);
        });
    }
}

public sealed class EnergyGainFixture : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public decimal Delta { get; set; }
    public bool ThrowAfter { get; set; }
    public int AfterCount { get; private set; }
    public List<int> AfterEnergy { get; private set; } = [];
    public List<decimal> ModifiedInputs { get; private set; } = [];
    protected override void DeepCloneFields() { base.DeepCloneFields(); AfterEnergy = new(AfterEnergy); ModifiedInputs = new(ModifiedInputs); }
    public override decimal ModifyEnergyGain(Player player, decimal amount) { ModifiedInputs.Add(amount); return amount + Delta; }
    public override Task AfterModifyingEnergyGain()
    {
        AfterCount++; AfterEnergy.Add(Owner.PlayerCombatState!.Energy);
        if (ThrowAfter) throw new InvalidOperationException("energy fixture failure");
        return Task.CompletedTask;
    }
}
