using System.Reflection;
using System.Collections.Frozen;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Localization.DynamicVars;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Rooms;

namespace AutoSlay.Rules.State;

/// <summary>Own-state checkpoint codec for the current resource/relic graph. Not a live-game snapshot adapter.</summary>
public static class WorldSnapshotCodec
{
    public const string Schema = "autoslay-independent-resource-snapshot/1";
    private static readonly string RulesSha256 = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(typeof(SimulationWorld).Assembly.Location))).ToLowerInvariant();
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        RespectRequiredConstructorParameters = true, RespectNullableAnnotations = true, MaxDepth = 64
    };
    private static readonly FrozenSet<Type> NumericVariables = new Type[] {
        typeof(DynamicVar), typeof(BlockVar), typeof(DamageVar), typeof(MaxHpVar), typeof(HealVar), typeof(CardsVar),
        typeof(EnergyVar), typeof(GoldVar), typeof(RepeatVar), typeof(HpLossVar), typeof(ForgeVar), typeof(StarsVar),
        typeof(CalculationBaseVar), typeof(CalculationExtraVar), typeof(IntVar)
    }.ToFrozenSet();
    public static byte[] Export(SimulationWorld world) => world.Inspect(() => JsonSerializer.SerializeToUtf8Bytes(Capture(world), Json));
    private static Snapshot Capture(SimulationWorld world)
    {
        if (world.Models.CanonicalInstances.Any(m => m is CardModel))
            Fail("mechanism_not_implemented", "Resource checkpoint cannot discard canonical card state");
        if (world.Models.CanonicalInstances.OfType<RelicModel>().Any(m => m.DynamicVars.Values.Any(v => v.HasCanonicalValueWrites)))
            Fail("mechanism_not_implemented", "Resource checkpoint cannot discard modified canonical variables");
        if (world.RunState.CurrentMapPointHistoryEntry?.PlayerStats.Any(e => e.CardUpgradeHistoryObserved) == true)
            Fail("mechanism_not_implemented", "Resource checkpoint cannot discard card upgrade history observations");
        if (world.RunState.Players.Any(p => p.CombatStateStatus != ObservationStatus.Unobserved || p.ObservedDeckCards != null || p.Deck.Cards.Count != 0))
            Fail("mechanism_not_implemented", "Resource checkpoint cannot discard player combat/pile/energy observations");
        if (world.LocalContext.IsObserved)
            Fail("mechanism_not_implemented", "Resource checkpoint cannot discard local player identity");
        if (world.CombatState?.HookInventory != null)
            Fail("mechanism_not_implemented", "Resource checkpoint cannot discard native hook-domain evidence");
        if (world.RunState.MultiplayerScalingModel != null || world.RunState.ObservedActIndex != null
            || world.CombatState?.EncounterStatus is ObservationStatus.ObservedAbsent or ObservationStatus.ObservedPresent)
            Fail("mechanism_not_implemented", "Resource checkpoint cannot discard act/encounter/singleton state");
        if (world.CombatState?.Timeline != null || world.CombatState?.History.IsObserved == true
            || world.Creatures.Any(c => !c.IsPlayer && c.ObservedSide != null || c.IsPlayer && c.ObservedSide != CombatSide.Player))
            Fail("mechanism_not_implemented", "Resource checkpoint cannot discard combat timeline/history/side state");
        if (world.Creatures.Any(c => c.Powers.Count != 0))
            Fail("mechanism_not_implemented", "Resource/relic checkpoint cannot discard power state; full graph checkpoint is not migrated");
        if (world.Parameters != null) CompiledRules.ValidateIdentity(world.Parameters);
        var run = world.RunState;
        var supported = run.Players.SelectMany(p => p.Relics).ToHashSet<AbstractModel>(ReferenceEqualityComparer.Instance);
        if (world.ModelInstances.Any(m => !supported.Contains(m)))
            Fail("mechanism_not_implemented", "Resource checkpoint cannot discard detached or non-relic model instances");
        var players = run.Players.Select(player => new PlayerData(player.NetId, player.Creature.InstanceId, player.IsActiveForHooks,
            player.Relics.Select(relic => {
                RequireScalarModel(relic);
                return new RelicData(relic.InstanceId!, relic.Id.ToString(), relic.IsWax, relic.IsMelted, relic.FloorAddedToDeck,
                    relic.StackCount, relic.Status, relic.DynamicVars.Values.Select(v => {
                        RequireScalarVar(v);
                        return new VariableData(v.Name, v.GetType().FullName!, v.BaseValue, v.EnchantedValue, v.PreviewValue, v.WasJustUpgraded,
                            v is BlockVar block ? (int)block.Props : v is DamageVar damage ? (int)damage.Props : null);
                    }).ToArray());
            }).ToArray())).ToArray();
        return new(Schema, RulesSha256, world.Parameters?.Sha256,
            run.TotalFloor, run.CurrentRoom == null ? null : new(run.CurrentRoom.InstanceId, run.CurrentRoom.Type),
            run.HistoryStatus, run.CurrentMapPointHistoryEntry?.PlayerStats.Select(h => new HistoryData(h.PlayerId, h.HpHealed, h.MaxHpGained, h.MaxHpLost)).ToArray(),
            world.Creatures.Select(c => new CreatureData(c.InstanceId, c.CurrentHp, c.MaxHp, c.Block)).ToArray(), players,
            world.CombatState == null ? null : new(world.CombatState.InstanceId, world.CombatState.Phase,
                world.CombatState.Creatures.Select(c => c.InstanceId).ToArray(), world.CombatState.ObservedHookOrder?.ToArray()),
            run.ObservedHookOrder?.ToArray(), world.Trace.ToArray());
    }
    public static SimulationWorld Import(ReadOnlySpan<byte> utf8, RuleParameters? parameters, ModelRegistry? registry = null)
    {
        try
        {
            if (utf8.Length > 4_000_000) Fail("invalid_snapshot", "Snapshot exceeds byte limit");
            using var document = JsonDocument.Parse(utf8.ToArray(), new JsonDocumentOptions { MaxDepth = 64 });
            RejectDuplicates(document.RootElement);
            var state = document.RootElement.Deserialize<Snapshot>(Json) ?? throw new JsonException("Object snapshot required");
            if (state.SchemaVersion != Schema || state.RulesSha256 != RulesSha256) Fail("version_mismatch", "Snapshot compiled rule identity differs");
            if (state.ParametersSha256 != parameters?.Sha256) Fail("parameter_mismatch", "Snapshot parameter package differs");
            if (parameters != null) CompiledRules.ValidateIdentity(parameters);
            if (state.Creatures == null || state.Players == null || state.Trace == null || !Enum.IsDefined(state.HistoryStatus) || state.TotalFloor < 0)
                Fail("invalid_snapshot", "Incomplete state arrays, history status or floor");
            var world = new SimulationWorld(parameters, registry);
            foreach (var creature in state.Creatures!) world.Add(new(creature.InstanceId, creature.Hp, creature.MaxHp, creature.Block));
            foreach (var player in state.Players!) world.RestorePlayer(player.NetId, player.CreatureId, player.IsActiveForHooks);
            if (state.Room != null) world.RestoreRoom(state.Room.InstanceId, state.Room.Type, state.TotalFloor);
            else world.RunState.TotalFloor = state.TotalFloor;
            if (state.HistoryStatus == ObservationStatus.ObservedPresent)
            {
                if (state.History == null || state.History.Select(h => h.PlayerId).Distinct().Count() != state.Players!.Length
                    || !state.History.Select(h => h.PlayerId).Order().SequenceEqual(state.Players.Select(p => p.NetId).Order()))
                    Fail("invalid_snapshot", "History does not cover exactly the observed players");
                world.RestoreHistory(true);
                foreach (var entry in state.History!)
                {
                    var restored = world.RunState.CurrentMapPointHistoryEntry!.GetEntry(entry.PlayerId);
                    restored.HpHealed = entry.HpHealed; restored.MaxHpGained = entry.MaxHpGained; restored.MaxHpLost = entry.MaxHpLost;
                }
            }
            else
            {
                if (state.History != null) Fail("invalid_snapshot", "Unexpected history for absent/unobserved entry");
                if (state.HistoryStatus == ObservationStatus.ObservedAbsent) world.RestoreHistory(false);
            }
            foreach (var player in state.Players!)
            {
                if (player.Relics == null) Fail("invalid_snapshot", "Relic inventory cannot be null");
                var owner = world.RunState.Players.Single(p => p.NetId == player.NetId);
                foreach (var observed in player.Relics!)
                {
                    var relic = world.RestoreRelic(owner, new(observed.InstanceId, ModelId.Deserialize(observed.ModelId), observed.IsWax,
                        observed.IsMelted, observed.FloorAddedToDeck, observed.StackCount, observed.Status));
                    RequireScalarModel(relic);
                    if (observed.Variables == null || observed.Variables.Length != relic.DynamicVars.Count
                        || observed.Variables.Select(v => v.Name).Distinct().Count() != observed.Variables.Length)
                        Fail("invalid_snapshot", "Variable shape differs from compiled model");
                    foreach (var variable in observed.Variables!) RestoreVariable(relic, variable);
                }
            }
            if (state.Combat != null)
            {
                if (state.Combat.CreatureIds == null) Fail("invalid_snapshot", "Combat membership cannot be null");
                world.RestoreCombat(state.Combat.InstanceId, state.Combat.Phase, state.Combat.CreatureIds!);
            }
            // A null order stays unknown. Never infer a complete order from the limited inventory projection.
            if (state.RunHookOrder != null)
                world.RestoreHookProjection(state.RunHookOrder, state.Combat?.HookOrder);
            else if (state.Combat?.HookOrder != null) Fail("invalid_snapshot", "Combat projection without a run projection");
            world.RestoreTrace(state.Trace!); return world;
        }
        catch (Exception error) when (error is JsonException or ArgumentException or NullReferenceException or InvalidOperationException or OverflowException)
        { throw new RuleBoundaryException("invalid_snapshot", error.Message); }
    }
    private static void RestoreVariable(RelicModel relic, VariableData observed)
    {
        if (!relic.DynamicVars.TryGetValue(observed.Name, out var variable) || observed.Type != variable.GetType().FullName)
            throw new RuleBoundaryException("invalid_snapshot", "Unknown or differently typed variable: " + observed.Name);
        RequireScalarVar(variable);
        if (observed.BaseValue > 999999999m || observed.EnchantedValue > 999999999m || observed.PreviewValue > 999999999m)
            Fail("invalid_snapshot", "Variable exceeds native value cap");
        var expectedProps = variable is BlockVar block ? (int?)block.Props : variable is DamageVar damage ? (int?)damage.Props : null;
        if (observed.Props != expectedProps) Fail("mechanism_not_implemented", "Restoring altered variable Props is not migrated");
        variable.RestoreScalarState(observed.BaseValue, observed.EnchantedValue, observed.PreviewValue, observed.WasJustUpgraded);
    }
    private static void RequireScalarModel(RelicModel model)
    {
        if (model.HasBeenRemovedFromState)
            Fail("mechanism_not_implemented", "Resource checkpoint does not include removed relic lifecycle state");
        if (model.GetType().Assembly != typeof(AbstractModel).Assembly)
            Fail("mechanism_not_implemented", "Checkpoint identity for additional compiled model assemblies is not migrated");
        for (var type = model.GetType(); type != typeof(RelicModel); type = type.BaseType!)
            if (type.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly).Length != 0)
                Fail("mechanism_not_implemented", "Private model state checkpoint contract not migrated: " + type.FullName);
    }
    private static void RequireScalarVar(DynamicVar variable)
    {
        if (!NumericVariables.Contains(variable.GetType()) || variable is EnergyVar { ColorPrefix.Length: > 0 })
            Fail("mechanism_not_implemented", "Non-numeric or custom variable checkpoint contract not migrated");
    }
    private static void RejectDuplicates(JsonElement value)
    {
        if (value.ValueKind == JsonValueKind.Object)
        {
            var names = new HashSet<string>(StringComparer.Ordinal);
            foreach (var field in value.EnumerateObject())
            { if (!names.Add(field.Name)) Fail("invalid_snapshot", "Duplicate field: " + field.Name); RejectDuplicates(field.Value); }
        }
        else if (value.ValueKind == JsonValueKind.Array) foreach (var item in value.EnumerateArray()) RejectDuplicates(item);
    }
    [System.Diagnostics.CodeAnalysis.DoesNotReturn]
    private static void Fail(string code, string message) => throw new RuleBoundaryException(code, message);
    private sealed record Snapshot(string SchemaVersion, string RulesSha256, string? ParametersSha256, int TotalFloor, RoomData? Room,
        ObservationStatus HistoryStatus, HistoryData[]? History, CreatureData[] Creatures, PlayerData[] Players, CombatData? Combat,
        string[]? RunHookOrder, CommandTrace[] Trace);
    private sealed record RoomData(string InstanceId, RoomType Type);
    private sealed record CreatureData(string InstanceId, int Hp, int MaxHp, int Block);
    private sealed record PlayerData(ulong NetId, string CreatureId, bool IsActiveForHooks, RelicData[] Relics);
    private sealed record RelicData(string InstanceId, string ModelId, bool IsWax, bool IsMelted, int FloorAddedToDeck, int StackCount, RelicStatus Status, VariableData[] Variables);
    private sealed record VariableData(string Name, string Type, decimal BaseValue, decimal EnchantedValue, decimal PreviewValue, bool WasJustUpgraded, int? Props);
    private sealed record HistoryData(ulong PlayerId, int HpHealed, int MaxHpGained, int MaxHpLost);
    private sealed record CombatData(string InstanceId, CombatPhase Phase, string[] CreatureIds, string[]? HookOrder);
}
