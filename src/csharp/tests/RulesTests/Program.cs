using System.Text.Json;
using AutoSlay.Rules.Native.Commands;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Entities.Powers;
using AutoSlay.Rules.Native.Models.Powers;
using AutoSlay.Rules.Native.GameActions.Multiplayer;
using AutoSlay.Rules.Native.Rooms;
using AutoSlay.Rules.Native.Models.Singleton;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Models.Relics;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;
using AutoSlay.RulesHost;
using AutoSlay.Rules.Native.Localization.DynamicVars;
using AutoSlay.Rules.Native.ValueProps;
using AutoSlay.Rules.Parameters;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using System.Text;
using System.Text.Json.Nodes;

var failures = new List<string>(); var executed = 0;
void Check(string name, Action test)
{
    executed++;
    try { test(); Console.WriteLine("PASS " + name); }
    catch (Exception error) { failures.Add(name); Console.WriteLine("FAIL " + name + ": " + error); }
}
void Assert(bool value) { if (!value) throw new Exception("Assertion failed"); }
void Run(SimulationWorld world, Func<Task> action, AbstractModel? model = null) => world.ExecuteAsync(action, model).GetAwaiter().GetResult();
void CheckAsync(string name, Func<Task> test) => Check(name, () => test().GetAwaiter().GetResult());
SimulationWorld PlayerWorld(int hp = 30, int maxHp = 80, bool? historyExists = true, RuleParameters? parameters = null, ModelRegistry? registry = null)
{
    var world = new SimulationWorld(parameters ?? CompiledRules.LoadDefaultParameters(), registry);
    world.Add(new("p", hp, maxHp)); world.RestorePlayer(7, "p", true); world.RestoreRoom("event-room", RoomType.Event, 4);
    if (historyExists.HasValue) world.RestoreHistory(historyExists.Value);
    return world;
}
T Restore<T>(SimulationWorld world, string instance) where T : RelicModel => (T)world.RestoreRelic(world.RunState.Players[0],
    new(instance, ModelDb.GetId(typeof(T)), false, false, 4, 1, RelicStatus.Normal));
CombatHookObservation HookObservation(SimulationWorld world) => new(
    world.CombatState!.Creatures.Select(c => c.InstanceId).ToArray(), [],
    world.RunState.Players.Select(p => new PlayerHookDomains(p.NetId, [], p.Relics.Select(r => r.InstanceId!).ToArray(),
        [null, null], ObservationStatus.ObservedPresent, [], [[], [], [], [], []])).ToArray(),
    [], [], [], [], world.CombatState.MultiplayerScalingModel == null ? ObservationStatus.ObservedAbsent : ObservationStatus.ObservedPresent,
    world.CombatState.Creatures.ToDictionary(c => c.InstanceId, c => (string[]?)c.Powers.Select(p => p.InstanceId!).ToArray()));
SimulationWorld PowerWorld(CombatPhase phase = CombatPhase.InProgress, bool timeline = true, bool history = true)
{
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(PowerOrderRelic).Assembly));
    world.RestoreCombat("native-combat", phase, "p");
    world.RestoreCombatHookInventory(HookObservation(world));
    if (timeline) world.RestoreCombatTimeline(1, CombatSide.Player, new Dictionary<ulong, int> { [7] = 1 });
    if (history) world.RestoreEmptyCombatPowerHistory();
    return world;
}

Check("native resource casts truncate the sum; capped heal can revive an intermediate dead creature", () => {
    var creature = new Creature("c", 0, 80); creature.HealInternal(5.9m);
    Assert(creature.CurrentHp == 5 && !creature.IsDead);
    creature.HealInternal(100); Assert(creature.CurrentHp == 80);
    creature.GainBlockInternal(2.9m); creature.LoseBlockInternal(.9m); Assert(creature.Block == 1);
});
Check("gain max HP records SetMaxHp then heals only the actual increase", () => {
    var world = new SimulationWorld(); world.Add(new("p", 78, 80));
    Run(world, () => CreatureCmd.GainMaxHp(world.Get("p"), 5.9m));
    Assert(world.Get("p").MaxHp == 85 && world.Get("p").CurrentHp == 83);
    Assert(world.Trace.Select(t => t.Operation).SequenceEqual(["CreatureCmd.SetMaxHp", "CreatureCmd.Heal"]));
});
Check("candidate worlds own targets and traces and do not mutate the source", () => {
    var world = new SimulationWorld(); world.Add(new("p", 30, 80));
    var a = world.Clone(); var b = world.Clone();
    Run(a, () => CreatureCmd.Heal(a.Get("p"), 9));
    Assert(world.Get("p").CurrentHp == 30 && b.Get("p").CurrentHp == 30 && a.Get("p").CurrentHp == 39);
    Assert(world.Trace.Count == 0 && b.Trace.Count == 0 && a.Trace.Count == 1);
    try { Run(a, () => CreatureCmd.Heal(world.Get("p"), 9)); throw new Exception("Foreign target accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "foreign_state"); }
});
Check("missing listeners cannot become an exact resource transition", () => {
    var world = new SimulationWorld(); world.Add(new("p", 30, 80));
    world.RestoreCombat("battle", CombatPhase.InProgress, "p");
    try { Run(world, () => CreatureCmd.Heal(world.Get("p"), 9)); throw new Exception("Incomplete state accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "state_incomplete"); }
    Assert(world.Get("p").CurrentHp == 30 && world.Trace.Count == 0);
});
Check("runtime assemblies have no native game, engine or bridge dependencies", () => {
    foreach (var assembly in new[] { typeof(Creature).Assembly, typeof(RuleServer).Assembly })
        Assert(!assembly.GetReferencedAssemblies().Any(a => new[] { "sts2", "GodotSharp", "0Harmony", "AutoSlayBridge" }.Contains(a.Name)));
});
Check("handshake cannot advertise model execution or silently accept an unknown operation", () => {
    var server = new RuleServer();
    using var hello = JsonDocument.Parse("{\"protocol_version\":\"autoslay-rules-host/1\",\"request_id\":\"a\",\"operation\":\"hello\"}");
    var result = JsonSerializer.SerializeToElement(server.Process(hello.RootElement));
    Assert(result.GetProperty("ok").GetBoolean() && !result.GetProperty("result").GetProperty("full_support").GetBoolean());
    using var missing = JsonDocument.Parse("{\"protocol_version\":\"autoslay-rules-host/1\",\"request_id\":\"b\",\"operation\":\"simulate\"}");
    result = JsonSerializer.SerializeToElement(server.Process(missing.RootElement));
    Assert(!result.GetProperty("ok").GetBoolean() && result.GetProperty("error").GetProperty("code").GetString() == "operation_not_implemented");
});
Check("AST source staging preserves rules and refuses to overwrite edited generated files", () => {
    var root = Path.Combine(Environment.CurrentDirectory, ".runtime", "source-stage-test-" + Guid.NewGuid().ToString("N"));
    var source = Path.Combine(root, "source"); var output = Path.Combine(root, "output"); Directory.CreateDirectory(source);
    var text = "namespace MegaCrit.Sts2.Core.Models; public class Sample { public int Run(int n) { if(n<0) throw new System.Exception(); while(n>2) n--; return n; } global::MegaCrit.Sts2.Core.Models.Sample? other; }";
    File.WriteAllText(Path.Combine(source, "Sample.cs"), text);
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output);
    var path = Path.Combine(output, "Sample.cs"); var first = File.ReadAllText(path);
    Assert(first.Contains("namespace AutoSlay.Rules.Native.Models;") && first.Contains("global::AutoSlay.Rules.Native.Models.Sample"));
    Assert(first.Contains("if(n<0) throw new System.Exception(); while(n>2) n--; return n;"));
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output); Assert(first == File.ReadAllText(path));
    File.AppendAllText(path, "\n// local fix");
    try { AutoSlay.RuleSourceSync.SourceStage.Write(source, output); throw new Exception("Local fix overwritten"); }
    catch (InvalidOperationException error) { Assert(error.Message.Contains("local edits")); }
    Assert(File.ReadAllText(path).EndsWith("// local fix"));
    File.Delete(Path.Combine(source, "Sample.cs"));
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output);
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output);
    using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(output, "source-stage-manifest.json")));
    Assert(manifest.RootElement.GetProperty("obsolete_files").EnumerateArray().Single().GetString() == "Sample.cs");
    Assert(File.ReadAllText(path).EndsWith("// local fix"));
});
Check("unparseable source fails staging before any output is created", () => {
    var root = Path.Combine(Environment.CurrentDirectory, ".runtime", "source-invalid-test-" + Guid.NewGuid().ToString("N"));
    var source = Path.Combine(root, "source"); var output = Path.Combine(root, "output"); Directory.CreateDirectory(source);
    File.WriteAllText(Path.Combine(source, "Bad.cs"), "class { broken");
    try { AutoSlay.RuleSourceSync.SourceStage.Write(source, output); throw new Exception("Invalid source staged"); }
    catch (InvalidOperationException error) { Assert(error.Message.Contains("Unparseable")); }
    Assert(!Directory.Exists(output));
});
Check("native variable arithmetic uses base values; clone resets previews and retains props and upgrade flag", () => {
    var variable = new BlockVar(8.9m, ValueProp.Move) { PreviewValue = 99, EnchantedValue = 17 };
    Assert(variable.IntValue == 8 && variable.BaseValue == 8.9m);
    variable.UpgradeValueBy(2.2m); Assert(variable.BaseValue == 11.1m && variable.WasJustUpgraded && variable.PreviewValue == 11.1m);
    variable.PreviewValue = 400; var clone = (BlockVar)variable.Clone();
    Assert(clone.BaseValue == 11.1m && clone.PreviewValue == 11.1m && clone.WasJustUpgraded && clone.Props == ValueProp.Move);
    clone.FinalizeUpgrade(); clone.BaseValue = 2_000_000_000m;
    Assert(clone.BaseValue == 999_999_999 && variable.BaseValue == 11.1m && variable.WasJustUpgraded);
    variable.BaseValue = -2.9m; Assert(variable.IntValue == -2);
    Assert(Convert.ToInt32(variable) == -3 && Convert.ToDecimal(variable) == -2.9m);
    try { _ = Convert.ToBoolean(variable); throw new Exception("Unsupported native conversion accepted"); }
    catch (InvalidCastException) { }
    var set = new DynamicVarSet([new MaxHpVar(14), new CardsVar(2)]); var other = set.CloneScalars();
    other.MaxHp.UpgradeValueBy(3); Assert(set.MaxHp.BaseValue == 14 && other.MaxHp.BaseValue == 17);
    try { _ = new DynamicVarSet([new MaxHpVar(1), new MaxHpVar(2)]); throw new Exception("Duplicate variable accepted"); }
    catch (ArgumentException) { }
});
Check("compiled model expressions read updated parameter data without rewriting behavior", () => {
    var root = Path.Combine(Environment.CurrentDirectory, ".runtime", "parameter-stage-test-" + Guid.NewGuid().ToString("N"));
    var source = Path.Combine(root, "source"); var output = Path.Combine(root, "output"); Directory.CreateDirectory(source);
    var text = """
        using AutoSlay.Rules.Parameters;
        using AutoSlay.Rules.Native.Localization.DynamicVars;
        using AutoSlay.Rules.Native.ValueProps;
        namespace MegaCrit.Sts2.Core.Models.Cards;
        public class Sample {
            public RuleParameters Parameters {get;}
            public Sample(RuleParameters parameters) { Parameters = parameters; }
            public DynamicVar[] CanonicalVars => new DynamicVar[] { new BlockVar(8m, ValueProp.Move), new CardsVar(2) };
            public void OnUpgrade(DynamicVar value) { if(value.BaseValue > 0) value.UpgradeValueBy(2m); }
        }
        """;
    File.WriteAllText(Path.Combine(source, "Sample.cs"), text);
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output, true);
    var generated = File.ReadAllText(Path.Combine(output, "Sample.cs"));
    Assert(generated.Contains("if(value.BaseValue > 0)") && generated.Contains("ValueProp.Move") && !generated.Contains("new BlockVar(8m"));
    var dataPath = Path.Combine(output, "rule-parameters.json"); var bytes = File.ReadAllBytes(dataPath);
    var data = JsonNode.Parse(bytes)!; var behavior = data["behavior_sha256"]!.GetValue<string>(); var contract = data["contract_sha256"]!.GetValue<string>();
    var original = RuleParameters.Load(bytes, behavior, contract); Assert(original.Count == 3);
    var originalPath = Path.Combine(root, "original-package.json"); File.WriteAllBytes(originalPath, bytes);
    var refs = ((string)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")!).Split(Path.PathSeparator)
        .Append(typeof(RuleParameters).Assembly.Location).Distinct().Select(p => MetadataReference.CreateFromFile(p));
    var compilation = CSharpCompilation.Create("ParameterFixture_" + Guid.NewGuid().ToString("N"),
        [CSharpSyntaxTree.ParseText(generated)], refs, new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary));
    using var stream = new MemoryStream(); var result = compilation.Emit(stream);
    Assert(result.Success); stream.Position = 0;
    var assembly = System.Runtime.Loader.AssemblyLoadContext.Default.LoadFromStream(stream);
    var type = assembly.GetType("AutoSlay.Rules.Native.Models.Cards.Sample")!;
    DynamicVar[] Vars(RuleParameters pack) => (DynamicVar[])type.GetProperty("CanonicalVars")!.GetValue(Activator.CreateInstance(type, pack))!;
    var baseline = Vars(original); Assert(baseline[0].BaseValue == 8 && ((BlockVar)baseline[0]).Props == ValueProp.Move);
    data["entries"]![0]!["value"] = 13.5m;
    var updated = RuleParameters.Load(Encoding.UTF8.GetBytes(data.ToJsonString()), behavior, contract);
    var updatedInstance = Activator.CreateInstance(type, updated)!;
    var changed = (DynamicVar[])type.GetProperty("CanonicalVars")!.GetValue(updatedInstance)!;
    type.GetMethod("OnUpgrade")!.Invoke(updatedInstance, [changed[0]]);
    Assert(changed[0].BaseValue == 15.5m && changed[0].WasJustUpgraded && baseline[0].BaseValue == 8);
    Assert(original.Sha256 != updated.Sha256 && original.BehaviorSha256 == updated.BehaviorSha256);
    // A source-only numeric update yields identical behavior and binding fingerprints.
    File.WriteAllText(Path.Combine(source, "Sample.cs"), text.Replace("new BlockVar(8m", "new BlockVar(10m"));
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output, true);
    var newer = JsonNode.Parse(File.ReadAllBytes(dataPath))!;
    Assert(newer["behavior_sha256"]!.GetValue<string>() == behavior && newer["contract_sha256"]!.GetValue<string>() == contract);
    Assert(File.ReadAllText(Path.Combine(output, "Sample.cs")) == generated);
    var numericDifference = AutoSlay.RuleSourceSync.ParameterDifference.Compare(originalPath, dataPath);
    Assert(numericDifference.Classification == "numeric_data_only_within_extracted_scope" && numericDifference.ChangedValues.Single().NewValue == 10);
    // A changed condition is behavior, so cannot use the old compiled identity.
    File.WriteAllText(Path.Combine(source, "Sample.cs"), text.Replace("BaseValue > 0", "BaseValue > 1"));
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output, true);
    Assert(AutoSlay.RuleSourceSync.ParameterDifference.Compare(originalPath, dataPath).Classification == "behavior_changed_semantic_migration_required");
    try { RuleParameters.Load(File.ReadAllBytes(dataPath), behavior, contract); throw new Exception("Changed behavior accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "parameter_mismatch"); }
    try { original.Decimal("missing"); throw new Exception("Missing parameter defaulted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "parameter_missing"); }
    var intKey = data["entries"]![1]!["key"]!.GetValue<string>();
    try { original.Decimal(intKey); throw new Exception("Wrong numeric type accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "parameter_type_mismatch"); }
    data["entries"]![1]!["value"] = 1.5m;
    try { RuleParameters.Load(Encoding.UTF8.GetBytes(data.ToJsonString()), behavior, contract); throw new Exception("Fractional count accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "invalid_parameters"); }
});
Check("semantic inventory distinguishes source/framework edges from unresolved calls and retains properties/events", () => {
    var root = Path.Combine(Environment.CurrentDirectory, ".runtime", "dependency-test-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(root);
    File.WriteAllText(Path.Combine(root, "Models.cs"), """
        namespace MegaCrit.Sts2.Core.Models.Cards;
        public class Other { public static int Read() => 3; }
        public delegate int Decision(Other value);
        public class Sample {
            public event System.Action? Changed;
            public int Value { get { return Other.Read(); } }
            public int Run() { Changed?.Invoke(); MissingDependency.Call(); return Value + Other.Read() + System.Math.Abs(-1); }
        }
        """);
    var inventory = AutoSlay.RuleSourceSync.SourceInventory.Build(root);
    var sample = inventory.Types.Single(t => t.FullName.EndsWith(".Sample"));
    Assert(inventory.Types.Single(t => t.FullName.EndsWith(".Decision")).Methods.Single().Parameters == "(Other value)");
    Assert(sample.Properties.Single().SyntacticCalls.Contains("Other.Read") && sample.Events.Single().Name == "Changed");
    var graph = AutoSlay.RuleSourceSync.DependencyInventory.Build(root);
    var type = graph.Types.Single(t => t.FullName.EndsWith(".Sample"));
    Assert(graph.Types.Single(t => t.FullName.EndsWith(".Decision")).Dependencies.Contains("MegaCrit.Sts2.Core.Models.Cards.Other"));
    Assert(type.Dependencies.Contains("MegaCrit.Sts2.Core.Models.Cards.Other"));
    Assert(type.Calls.Any(c => c.Target == "MegaCrit.Sts2.Core.Models.Cards.Other.Read()" && c.Scope == "source"));
    Assert(type.Calls.Any(c => c.Scope == "framework" && c.Target!.Contains("System.Math.Abs")));
    Assert(type.Calls.Any(c => c.Expression == "MissingDependency.Call()" && c.Scope == "unresolved"));
    Assert(graph.CompilationErrors > 0 && graph.UnresolvedCalls > 0 && graph.Diagnostics.Any(d => d.Contains("MissingDependency")));
});
Check("Host rejects duplicate fields, empty operation and unexpected hello payload with the original request identity", () => {
    var server = new RuleServer();
    foreach (var body in new[] {
        "\"operation\":\"hello\",\"operation\":\"hello\"",
        "\"operation\":\" \"",
        "\"operation\":\"hello\",\"payload\":{\"effects\":[]}"
    }) {
        using var document = JsonDocument.Parse("{\"protocol_version\":\"autoslay-rules-host/1\",\"request_id\":\"original\"," + body + "}");
        var value = JsonSerializer.SerializeToElement(server.Process(document.RootElement));
        Assert(!value.GetProperty("ok").GetBoolean() && value.GetProperty("request_id").GetString() == "original"
            && value.GetProperty("error").GetProperty("code").GetString() == "invalid_request");
    }
});
Check("unmigrated kill/revival boundaries fail before resource or trace mutation", () => {
    foreach (var index in Enumerable.Range(0, 3)) {
        var world = new SimulationWorld(); world.Add(new("alive", 5, 10)); world.Add(new("dead", 0, 10));
        Func<Task> action = index switch { 0 => () => CreatureCmd.SetMaxHp(world.Get("alive"), .9m),
            1 => () => CreatureCmd.Heal(world.Get("dead"), 2), _ => () => CreatureCmd.GainMaxHp(world.Get("dead"), 2) };
        try { Run(world, action); throw new Exception("Missing lifecycle silently accepted"); }
        catch (RuleBoundaryException error) { Assert(error.Code == "mechanism_not_implemented"); }
        Assert(world.Get("alive").MaxHp == 10 && world.Get("dead").MaxHp == 10 && world.Get("dead").CurrentHp == 0 && world.Trace.Count == 0);
    }
});
Check("three original model callbacks execute compiled native commands and record actual gain/healing", () => {
    foreach (var item in new[] { (typeof(Mango), 14), (typeof(Pear), 10), (typeof(Strawberry), 7) })
    {
        var world = PlayerWorld(); var id = ModelDb.GetId(item.Item1);
        Assert(world.Registry.Ids.Contains(id));
        var relic = world.RestoreRelic(world.RunState.Players[0], new("relic", id, false, false, 4, 1, RelicStatus.Normal));
        Run(world, relic.AfterObtained, relic);
        var history = world.RunState.CurrentMapPointHistoryEntry!.GetEntry(7);
        Assert(world.Get("p").MaxHp == 80 + item.Item2 && world.Get("p").CurrentHp == 30 + item.Item2);
        Assert(history.MaxHpGained == item.Item2 && history.HpHealed == item.Item2);
        Assert(world.Trace.Select(t => t.Operation).SequenceEqual(["CreatureCmd.SetMaxHp", "CreatureCmd.Heal"]));
        Assert(world.Trace.All(t => t.Source == "relic"));
    }
    var capped = PlayerWorld(999999998, 999999999); var mango = Restore<Mango>(capped, "mango");
    Run(capped, mango.AfterObtained, mango);
    Assert(capped.Get("p").CurrentHp == 999999998 && capped.Get("p").MaxHp == 999999999);
    Assert(capped.Trace[1].Requested == 0 && capped.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).MaxHpGained == 0);
});
Check("actual compiled entity reads changed numeric package while compiled behavior identity stays fixed", () => {
    using var stream = typeof(CompiledRules).Assembly.GetManifestResourceStream("AutoSlay.Rules.Parameters.Default")!;
    var pack = JsonNode.Parse(stream)!;
    var entry = pack["entries"]!.AsArray().Single(e => e!["key"]!.GetValue<string>().Contains("/Mango.cs::"))!;
    entry["value"] = 17.9m;
    var updated = CompiledRules.LoadParameters(Encoding.UTF8.GetBytes(pack.ToJsonString()));
    var world = PlayerWorld(parameters: updated); var mango = Restore<Mango>(world, "mango");
    Assert(mango.DynamicVars.MaxHp.BaseValue == 17.9m);
    Run(world, mango.AfterObtained, mango);
    Assert(world.Get("p").MaxHp == 97 && world.Get("p").CurrentHp == 47);
    Assert(world.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).MaxHpGained == 17);
    Assert(CompiledRules.LoadDefaultParameters().Sha256 != updated.Sha256);
});
Check("world graph clones preserve cyclic identity, canonical isolation, variables, history and cleared subscribers", () => {
    var source = PlayerWorld(); var original = Restore<Mango>(source, "mango");
    source.RestoreCombat("combat", CombatPhase.InProgress, "p"); source.RestoreHookProjection([], ["mango"]);
    var callbacks = 0; original.ExecutionFinished += _ => callbacks++;
    var a = source.Clone(); var b = source.Clone(); var relic = (Mango)a.RequireModel("mango");
    Assert(ReferenceEquals(a.Parameters, source.Parameters));
    Assert(ReferenceEquals(relic.Owner.Creature, a.Get("p")) && ReferenceEquals(a.Get("p").Player, relic.Owner));
    Assert(ReferenceEquals(a.Get("p").CombatState, a.CombatState) && ReferenceEquals(a.CombatState!.RunState, a.RunState));
    Assert(!ReferenceEquals(relic.CanonicalInstance, original.CanonicalInstance));
    Assert(!ReferenceEquals(a.RunState.CurrentRoom, source.RunState.CurrentRoom));
    relic.DynamicVars.MaxHp.UpgradeValueBy(3); Run(a, relic.AfterObtained, relic);
    Assert(a.Get("p").MaxHp == 97 && b.Get("p").MaxHp == 80 && source.Get("p").MaxHp == 80);
    Assert(a.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).HpHealed == 17 && source.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).HpHealed == 0);
    Assert(original.DynamicVars.MaxHp.BaseValue == 14 && ((Mango)b.RequireModel("mango")).DynamicVars.MaxHp.BaseValue == 14 && callbacks == 0);
    original.InvokeExecutionFinished(); Assert(callbacks == 1);
    a.Models.Relic<Mango>().DynamicVars.MaxHp.UpgradeValueBy(1);
    Assert(a.Models.Relic<Mango>().DynamicVars.MaxHp.BaseValue == 15 && b.Models.Relic<Mango>().DynamicVars.MaxHp.BaseValue == 14);
});
Check("unobserved history and unhydrated model inventory cannot silently become empty state", () => {
    var unknown = PlayerWorld(historyExists: null); var mango = Restore<Mango>(unknown, "mango");
    try { Run(unknown, mango.AfterObtained, mango); throw new Exception("Unknown history accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "state_incomplete"); }
    Assert(unknown.Get("p").MaxHp == 80 && unknown.Trace.Count == 0);
    var absent = PlayerWorld(historyExists: false); var available = Restore<Mango>(absent, "mango");
    Run(absent, available.AfterObtained, available); Assert(absent.Get("p").MaxHp == 94 && absent.RunState.CurrentMapPointHistoryEntry == null);
    try { absent.RestoreRelic(absent.RunState.Players[0], new("unknown", new("RELIC", "NOT_MIGRATED"), false, false, 4, 1, RelicStatus.Normal)); throw new Exception("Missing model accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "model_not_migrated"); }
    Assert(absent.RunState.Players[0].Relics.Count == 1);
});
Check("native HP hooks use requested delta even at full HP and clone custom references through the identity map", () => {
    var registry = new ModelRegistry(typeof(Mango).Assembly, typeof(HpLedgerRelic).Assembly);
    var world = PlayerWorld(80, 80, registry: registry); var first = Restore<HpLedgerRelic>(world, "first"); var second = Restore<HpLedgerRelic>(world, "second");
    first.Peer = second; second.Peer = first;
    world.RestoreCombat("combat", CombatPhase.InProgress, "p"); world.RestoreHookProjection([], ["second", "first"]);
    var order = new List<string>(); first.ExecutionFinished += _ => order.Add("first"); second.ExecutionFinished += _ => order.Add("second");
    Run(world, () => CreatureCmd.Heal(world.Get("p"), 5.9m));
    Assert(world.Get("p").CurrentHp == 80 && first.Deltas.SequenceEqual([5.9m]) && order.SequenceEqual(["second", "first"]));
    Assert(world.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).HpHealed == 0);
    Run(world, () => CreatureCmd.SetCurrentHp(world.Get("p"), 200));
    Assert(world.Get("p").CurrentHp == 80 && first.Deltas.SequenceEqual([5.9m, 120m]));
    var copy = world.Clone(); var copiedFirst = (HpLedgerRelic)copy.RequireModel("first"); var copiedSecond = (HpLedgerRelic)copy.RequireModel("second");
    Assert(ReferenceEquals(copiedFirst.Peer, copiedSecond) && ReferenceEquals(copiedSecond.Peer, copiedFirst));
    Assert(ReferenceEquals(copiedFirst.LastTarget, copy.Get("p")));
    Run(copy, () => CreatureCmd.Heal(copy.Get("p"), 3));
    Assert(first.Deltas.Count == 2 && copiedFirst.Deltas.SequenceEqual([5.9m, 120m, 3m]) && order.Count == 4);
});
Check("world fork copies private fields without per-model adapters or entity creation lifecycle", () => {
    var registry = new ModelRegistry(typeof(Mango).Assembly, typeof(UnsafeRelic).Assembly);
    var source = PlayerWorld(registry: registry); var unsafeModel = Restore<UnsafeRelic>(source, "unsafe");
    var peer = Restore<UnsafeRelic>(source, "peer"); unsafeModel.ShareStateWith(peer); unsafeModel.Attach(source.Get("p"));
    var observerCalls = 0; source.Get("p").CurrentHpChanged += (_, _) => observerCalls++;
    source.Get("p").HealInternal(2); Assert(unsafeModel.EventCount == 1 && peer.EventCount == 1 && observerCalls == 1);
    unsafeModel.DynamicVars.Values.ToArray(); // Cache a dictionary view before the fork.
    var copy = source.Clone(); var sibling = source.Clone(); var copied = (UnsafeRelic)copy.RequireModel("unsafe");
    Assert(copied.CreationResets == 1 && copied.EventCount == 1);
    copy.Get("p").HealInternal(3); copied.PrivateData.Add(2);
    Assert(copied.EventCount == 2 && ((UnsafeRelic)copy.RequireModel("peer")).EventCount == 2);
    Assert(unsafeModel.EventCount == 1 && ((UnsafeRelic)sibling.RequireModel("unsafe")).EventCount == 1 && observerCalls == 1);
    Assert(copied.PrivateData.SequenceEqual([1, 2]) && unsafeModel.PrivateData.SequenceEqual([1]));
    Assert(!source.IsFaulted && source.Get("p").CurrentHp == 32 && copy.Get("p").CurrentHp == 35);
});
CheckAsync("failed/cancelled branches retain only their own prefix and cannot be reused as exact candidates", async () => {
    var source = PlayerWorld(); var failed = source.Clone(); var cancelled = source.Clone(); var sibling = source.Clone();
    try { await failed.ExecuteAsync(async () => { await CreatureCmd.Heal(failed.Get("p"), 7); throw new InvalidOperationException("fixture failure"); }); }
    catch (InvalidOperationException) { }
    using var cancellation = new CancellationTokenSource();
    try { await cancelled.ExecuteAsync(async () => { await CreatureCmd.Heal(cancelled.Get("p"), 2); cancellation.Cancel(); await Task.Yield(); }, cancellationToken: cancellation.Token); }
    catch (OperationCanceledException) { }
    Assert(failed.IsFaulted && cancelled.IsFaulted && failed.Get("p").CurrentHp == 37 && cancelled.Get("p").CurrentHp == 32);
    Assert(source.Get("p").CurrentHp == 30 && source.Trace.Count == 0 && sibling.Get("p").CurrentHp == 30);
    try { failed.Clone(); throw new Exception("Failed branch reused"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "world_faulted"); }
    await sibling.ExecuteAsync(() => CreatureCmd.Heal(sibling.Get("p"), 9)); Assert(sibling.Get("p").CurrentHp == 39);
});
CheckAsync("world contexts reject concurrent reuse and expired child tasks while independent worlds run concurrently", async () => {
    var world = PlayerWorld(); var sibling = world.Clone();
    var started = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var release = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
    var running = world.ExecuteAsync(async () => { started.SetResult(); await release.Task; await CreatureCmd.Heal(world.Get("p"), 1); });
    await started.Task;
    try { world.Clone(); throw new Exception("Active clone accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "world_busy"); }
    try { await world.ExecuteAsync(() => Task.CompletedTask); throw new Exception("Concurrent reuse accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "world_busy"); }
    var independent = sibling.ExecuteAsync(() => CreatureCmd.Heal(sibling.Get("p"), 5)); release.SetResult(); await Task.WhenAll(running, independent);
    Assert(!world.IsFaulted && world.Get("p").CurrentHp == 31 && sibling.Get("p").CurrentHp == 35);
    var later = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously); Task? escaped = null;
    await world.ExecuteAsync(() => { escaped = Task.Run(async () => { await later.Task; await CreatureCmd.Heal(world.Get("p"), 50); }); return Task.CompletedTask; });
    later.SetResult();
    try { await escaped!; throw new Exception("Expired execution context accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "foreign_state"); }
    Assert(world.Get("p").CurrentHp == 31);
});
Check("new native source model compiles and registers without an entity mapping; unsupported calls remain build errors", () => {
    var root = Path.Combine(Environment.CurrentDirectory, ".runtime", "compiled-unit-test-" + Guid.NewGuid().ToString("N"));
    var source = Path.Combine(root, "source"); var output = Path.Combine(root, "output"); Directory.CreateDirectory(source);
    var native = """
        using System.Collections.Generic;
        using System.Threading.Tasks;
        using MegaCrit.Sts2.Core.Commands;
        using MegaCrit.Sts2.Core.Entities.Relics;
        using MegaCrit.Sts2.Core.Localization.DynamicVars;
        namespace MegaCrit.Sts2.Core.Models.Relics;
        public sealed class NewGrowthSeed : RelicModel {
            public override RelicRarity Rarity => RelicRarity.Rare;
            protected override IEnumerable<DynamicVar> CanonicalVars => new global::_003C_003Ez__ReadOnlySingleElementList<DynamicVar>(new MaxHpVar(4m));
            public override async Task AfterObtained() { await CreatureCmd.GainMaxHp(Owner.Creature, DynamicVars.MaxHp.BaseValue); }
        }
        """;
    File.WriteAllText(Path.Combine(source, "New.cs"), native);
    File.WriteAllText(Path.Combine(source, "Retired.cs"), "namespace MegaCrit.Sts2.Core.Models.Relics; public class Retired { }");
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output, true, ["New.cs", "Retired.cs"]);
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output, true, ["New.cs"]);
    Assert(File.Exists(Path.Combine(output, "Code", "Retired.cs")));
    var props = System.Xml.Linq.XDocument.Load(Path.Combine(output, "RuleUnits.props"));
    Assert(!props.Descendants("Compile").Any(n => n.Attribute("Include")!.Value.Contains("Retired.cs")));
    string[] ActiveSources()
    {
        using var manifest = JsonDocument.Parse(File.ReadAllText(Path.Combine(output, "source-stage-manifest.json")));
        return manifest.RootElement.GetProperty("files").EnumerateArray().Select(f => f.GetProperty("path").GetString()!)
            .Where(p => p.EndsWith(".cs", StringComparison.Ordinal)).Select(p => Path.Combine(output, p)).ToArray();
    }
    var refs = ((string)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")!).Split(Path.PathSeparator)
        .Append(typeof(AbstractModel).Assembly.Location).Distinct().Select(p => MetadataReference.CreateFromFile(p));
    CSharpCompilation Compilation() => CSharpCompilation.Create("MigratedEntity_" + Guid.NewGuid().ToString("N"),
        ActiveSources().Select(p => CSharpSyntaxTree.ParseText(File.ReadAllText(p))), refs, new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary));
    using var binary = new MemoryStream(); var result = Compilation().Emit(binary);
    if (!result.Success) throw new Exception(string.Join('\n', result.Diagnostics));
    binary.Position = 0; var assembly = System.Runtime.Loader.AssemblyLoadContext.Default.LoadFromStream(binary);
    var identity = assembly.GetType("AutoSlay.Rules.Generated.CompiledUnitIdentity")!;
    string Identity(string key) => (string)identity.GetField(key, System.Reflection.BindingFlags.Static | System.Reflection.BindingFlags.NonPublic)!.GetRawConstantValue()!;
    var parameters = RuleParameters.Load(File.ReadAllBytes(Path.Combine(output, "rule-parameters.json")), Identity("BehaviorSha256"), Identity("ContractSha256"));
    var registry = new ModelRegistry(typeof(Mango).Assembly, assembly);
    var world = PlayerWorld(parameters: parameters, registry: registry);
    var model = world.RestoreRelic(world.RunState.Players[0], new("new", new("RELIC", "NEW_GROWTH_SEED"), false, false, 4, 1, RelicStatus.Normal));
    Run(world, model.AfterObtained, model);
    Assert(world.Get("p").MaxHp == 84 && world.Get("p").CurrentHp == 34);
    Assert(registry.Ids.Count == new ModelRegistry(typeof(Mango).Assembly).Ids.Count + 1 && assembly.GetType("AutoSlay.Rules.Native.Models.Relics.Retired") == null);
    File.WriteAllText(Path.Combine(source, "New.cs"), native.Replace("await CreatureCmd.GainMaxHp(Owner.Creature, DynamicVars.MaxHp.BaseValue);", "await MissingMechanism.Apply();"));
    AutoSlay.RuleSourceSync.SourceStage.Write(source, output, true, ["New.cs"]);
    using var invalidBinary = new MemoryStream(); var invalid = Compilation().Emit(invalidBinary);
    Assert(!invalid.Success && invalid.Diagnostics.Any(d => d.Severity == DiagnosticSeverity.Error && d.ToString().Contains("MissingMechanism")));
});
Check("native low-level damage metadata, decimal block loss and revival event ordering are preserved", () => {
    var creature = new Creature("resource", 5, 5, 3);
    Assert(creature.DamageBlockInternal(.9m, ValueProp.Move) == .9m && creature.Block == 3);
    Assert(creature.DamageBlockInternal(10, ValueProp.Unblockable) == 0 && creature.Block == 3);
    var damage = creature.LoseHpInternal(8.8m, ValueProp.Unpowered);
    Assert(ReferenceEquals(damage.Receiver, creature) && damage.Props == ValueProp.Unpowered && damage.UnblockedDamage == 5);
    Assert(damage.WasTargetKilled && damage.OverkillDamage == 3 && creature.CurrentHp == 0);
    var revivals = 0; creature.Revived += c => { Assert(c.CurrentHp > 0); revivals++; };
    creature.HealInternal(4.8m); creature.HealInternal(1);
    Assert(creature.CurrentHp == 5 && revivals == 1);
    try { creature.SetCurrentHpInternal(-1.9m); throw new Exception("Native HP property validation bypassed"); }
    catch (ArgumentException) { Assert(creature.CurrentHp == 5); }
    try { creature.SetMaxHpInternal((decimal)int.MaxValue + 1); throw new Exception("Native overflow was silently clamped"); }
    catch (OverflowException) { Assert(creature.MaxHp == 5); }
    var world = new SimulationWorld(); world.Add(new("dead", 0, 0));
    try { Run(world, () => CreatureCmd.GainMaxHp(world.Get("dead"), 5)); throw new Exception("Unmigrated revival after max HP mutation accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "mechanism_not_implemented"); }
    Assert(world.Get("dead").MaxHp == 0 && world.Trace.Count == 0);
});
Check("another synced native entity reuses existing commands and preserves max-HP then full-heal order", () => {
    var world = PlayerWorld(); var waffle = Restore<LeesWaffle>(world, "waffle");
    Run(world, waffle.AfterObtained, waffle);
    Assert(world.Get("p").MaxHp == 87 && world.Get("p").CurrentHp == 87);
    Assert(world.Trace.Select(t => t.Operation).SequenceEqual(["CreatureCmd.SetMaxHp", "CreatureCmd.Heal", "CreatureCmd.Heal"]));
    Assert(world.Trace[1].Requested == 7 && world.Trace[2].Requested == 50);
    var history = world.RunState.CurrentMapPointHistoryEntry!.GetEntry(7);
    Assert(history.MaxHpGained == 7 && history.HpHealed == 57);
});
Check("own-state checkpoint restores graph, upgrades, history and trace without replaying acquisition callbacks", () => {
    var source = PlayerWorld(); var waffle = Restore<LeesWaffle>(source, "waffle");
    Run(source, waffle.AfterObtained, waffle);
    waffle.DynamicVars.MaxHp.UpgradeValueBy(2); waffle.DynamicVars.MaxHp.PreviewValue = 99;
    source.RestoreCombat("combat", CombatPhase.InProgress, "p"); source.RestoreHookProjection([], ["waffle"]);
    var bytes = WorldSnapshotCodec.Export(source);
    var restored = WorldSnapshotCodec.Import(bytes, source.Parameters); var relic = (LeesWaffle)restored.RequireModel("waffle");
    Assert(restored.Get("p").MaxHp == 87 && restored.Get("p").CurrentHp == 87 && restored.Trace.SequenceEqual(source.Trace));
    Assert(restored.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).HpHealed == 57 && restored.RunState.TotalFloor == 4);
    Assert(ReferenceEquals(relic.Owner.Creature, restored.Get("p")) && ReferenceEquals(restored.Get("p").CombatState, restored.CombatState));
    Assert(relic.DynamicVars.MaxHp.BaseValue == 9 && relic.DynamicVars.MaxHp.WasJustUpgraded && relic.DynamicVars.MaxHp.PreviewValue == 99);
    Assert(relic.CanonicalInstance.DynamicVars.MaxHp.BaseValue == 7);
    relic.DynamicVars.MaxHp.UpgradeValueBy(1); Assert(waffle.DynamicVars.MaxHp.BaseValue == 9);
    Run(restored, () => CreatureCmd.Heal(restored.Get("p"), 1)); Assert(restored.Trace.Count == 4 && source.Trace.Count == 3);
    var log = Path.Combine(Environment.CurrentDirectory, ".runtime", "analysis", "independent-world-checkpoint.json");
    Directory.CreateDirectory(Path.GetDirectoryName(log)!); File.WriteAllBytes(log, bytes);
});
Check("checkpoint identity/shape validation rejects stale data, missing fields and duplicate JSON", () => {
    var world = PlayerWorld(); _ = Restore<Mango>(world, "mango");
    var raw = WorldSnapshotCodec.Export(world); var data = JsonNode.Parse(raw)!;
    void Invalid(JsonNode altered, string code = "invalid_snapshot")
    {
        try { WorldSnapshotCodec.Import(Encoding.UTF8.GetBytes(altered.ToJsonString()), world.Parameters); throw new Exception("Bad checkpoint accepted"); }
        catch (RuleBoundaryException error) { Assert(error.Code == code); }
    }
    var stale = data.DeepClone(); stale["rules_sha256"] = new string('0', 64); Invalid(stale, "version_mismatch");
    stale = data.DeepClone(); stale["parameters_sha256"] = new string('0', 64); Invalid(stale, "parameter_mismatch");
    stale = data.DeepClone(); stale.AsObject().Remove("players"); Invalid(stale);
    stale = data.DeepClone(); stale["players"]![0]!["creature_id"] = null; Invalid(stale);
    stale = data.DeepClone(); stale["effects"] = new JsonArray(); Invalid(stale);
    stale = data.DeepClone(); stale["players"]![0]!["relics"]![0]!["variables"]![0]!["name"] = "unknown"; Invalid(stale);
    var duplicate = Encoding.UTF8.GetString(raw).Replace("\"schema_version\":", "\"schema_version\":\"duplicate\",\"schema_version\":");
    try { WorldSnapshotCodec.Import(Encoding.UTF8.GetBytes(duplicate), world.Parameters); throw new Exception("Duplicate checkpoint field accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "invalid_snapshot"); }
    Assert(world.Get("p").MaxHp == 80 && world.Trace.Count == 0);
});
Check("checkpoint preserves unknown observation and refuses unported private model state", () => {
    var source = PlayerWorld(historyExists: null); _ = Restore<Mango>(source, "mango");
    var restored = WorldSnapshotCodec.Import(WorldSnapshotCodec.Export(source), source.Parameters);
    Assert(restored.RunState.HistoryStatus == ObservationStatus.Unobserved);
    var model = (Mango)restored.RequireModel("mango");
    try { Run(restored, model.AfterObtained, model); throw new Exception("Unknown history became absent during restore"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "state_incomplete"); }
    var fixture = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(HpLedgerRelic).Assembly)); _ = Restore<HpLedgerRelic>(fixture, "ledger");
    try { WorldSnapshotCodec.Export(fixture); throw new Exception("Private state was silently omitted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "mechanism_not_implemented"); }
});
Check("native heal ending guard skips non-players but keeps player healing with explicit hook state", () => {
    var monster = new SimulationWorld(); monster.Add(new("monster", 10, 20)); monster.RestoreCombat("combat", CombatPhase.Ending, "monster");
    Run(monster, () => CreatureCmd.Heal(monster.Get("monster"), 5));
    Assert(monster.Get("monster").CurrentHp == 10 && monster.Trace.Count == 0 && !monster.IsFaulted);
    var player = PlayerWorld(); player.RestoreCombat("combat", CombatPhase.Ending, "p"); player.RestoreHookProjection([], []);
    Run(player, () => CreatureCmd.Heal(player.Get("p"), 5));
    Assert(player.Get("p").CurrentHp == 35 && player.RunState.CurrentMapPointHistoryEntry!.GetEntry(7).HpHealed == 5);
});
Check("clone graph inspection does not execute unknown collection enumerators", () => {
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(OpaqueRelic).Assembly));
    var opaque = Restore<OpaqueRelic>(world, "opaque");
    try { world.Clone(); throw new Exception("Unmigrated collection state was accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "clone_contract_missing"); }
    Assert(opaque.PrivateData.Enumerations == 0 && world.Get("p").CurrentHp == 30 && !world.IsFaulted);
});
Check("native resource property notifications occur on actual changes and max HP notifies before HP clamping", () => {
    var creature = new Creature("resource", 10, 10, 2); var events = new List<string>();
    creature.MaxHpChanged += (before, after) => events.Add($"max:{before}>{after}:hp={creature.CurrentHp}");
    creature.CurrentHpChanged += (before, after) => events.Add($"hp:{before}>{after}");
    creature.BlockChanged += (before, after) => events.Add($"block:{before}>{after}");
    creature.HealInternal(5); Assert(events.Count == 0);
    creature.SetMaxHpInternal(8); Assert(events.SequenceEqual(["max:10>8:hp=10", "hp:10>8"]));
    creature.GainBlockInternal(.9m); Assert(events.Count == 2);
    creature.LoseBlockInternal(.1m); Assert(events[^1] == "block:2>1");
    creature.SetCurrentHpInternal(-.9m); Assert(creature.CurrentHp == 0 && events[^1] == "hp:8>0");
    var source = PlayerWorld(); var observed = 0; source.Get("p").CurrentHpChanged += (_, _) => observed++;
    var branch = source.Clone(); Run(branch, () => CreatureCmd.Heal(branch.Get("p"), 1));
    Assert(observed == 0 && branch.Get("p").CurrentHp == 31 && source.Get("p").CurrentHp == 30);
});
Check("PE inventory retains raw fields and discovers inheritance without executing constructors or getters", () => {
    var root = Path.Combine(Environment.CurrentDirectory, ".runtime", "pe-inventory-test-" + Guid.NewGuid().ToString("N")); Directory.CreateDirectory(root);
    var marker = Path.Combine(root, "must-not-exist");
    var literal = Microsoft.CodeAnalysis.CSharp.SymbolDisplay.FormatLiteral(marker, true);
    var source = """
        namespace MegaCrit.Sts2.Core.Models;
        public abstract class AbstractModel { public virtual bool IsMock => false; }
        public abstract class RelicModel : AbstractModel { }
        public abstract class GenericBase<T> : RelicModel { }
        public sealed class Seed : GenericBase<int> {
            static Seed() { System.IO.File.WriteAllText(MARKER, "executed"); }
            public Seed() { throw new System.InvalidOperationException("constructor must not run"); }
            public int Counter { get; set; }
        }
        public sealed class MockSeed : RelicModel { public override bool IsMock => true; }
        public sealed class OpenSeed<T> : RelicModel { }
        [System.Runtime.CompilerServices.CompilerGenerated] internal class CompilerHelper { private int _item = 42; }
        """.Replace("MARKER", literal);
    var refs = ((string)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")!).Split(Path.PathSeparator).Distinct().Select(p => MetadataReference.CreateFromFile(p));
    var compilation = CSharpCompilation.Create("MetadataFixture_" + Guid.NewGuid().ToString("N"), [CSharpSyntaxTree.ParseText(source)], refs,
        new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, optimizationLevel: OptimizationLevel.Release));
    var path = Path.Combine(root, "fixture.dll"); using (var file = File.Create(path)) { var emitted = compilation.Emit(file); Assert(emitted.Success); }
    var report = AutoSlay.RuleSourceSync.PeInventory.Build(path);
    Assert(!File.Exists(marker) && !report.GameRulesExecuted && !report.RuntimeModelRosterVerified);
    Assert(report.ModelCandidates.Count == 2 && report.ModelCandidates.Single(c => c.ModelId == "RELIC.SEED").IsMockConstant == false);
    Assert(report.ModelCandidates.Single(c => c.ModelId == "RELIC.MOCK_SEED").IsMockConstant == true);
    Assert(report.Types.Single(t => t.FullName.EndsWith(".Seed")).Fields.Any(f => f.Name == "<Counter>k__BackingField"));
    Assert(report.Types.Single(t => t.FullName.EndsWith(".CompilerHelper")).CompilerGenerated);
    Assert(report.Types.Single(t => t.FullName.EndsWith(".CompilerHelper")).Fields.Any(f => f.Name == "_item"));
    var table = """
        using System;
        using MegaCrit.Sts2.Core.Models;
        namespace MegaCrit.Sts2.Core.Models;
        public static class AbstractModelSubtypes {
            private static readonly Type _t0 = typeof(Seed);
            private static readonly Type _t1 = typeof(MockSeed);
            private static readonly Type[] _subtypes = new Type[2] { _t0, _t1 };
            public static int Count => 2;
            public static object All => _subtypes;
        }
        """;
    var tablePath = Path.Combine(root, "table.cs"); File.WriteAllText(tablePath, table);
    var roster = AutoSlay.RuleSourceSync.RosterInventory.Build(path, tablePath);
    Assert(roster.StaticSetVerified && !roster.RuntimeModelRosterVerified && !roster.SemanticSupportCertified && !File.Exists(marker));
    File.WriteAllText(tablePath, table.Replace("{ _t0, _t1 }", "{ _t0, _t0 }"));
    roster = AutoSlay.RuleSourceSync.RosterInventory.Build(path, tablePath);
    Assert(!roster.StaticSetVerified && roster.DuplicateSourceTypes.Length == 1 && roster.PeOnlyTypes.Length == 1);
    File.WriteAllText(tablePath, table.Replace("Count => 2", "Count => 3"));
    Assert(!AutoSlay.RuleSourceSync.RosterInventory.Build(path, tablePath).StaticSetVerified);
    File.WriteAllText(tablePath, table.Replace("typeof(Seed)", "typeof(MissingSeed)"));
    try { AutoSlay.RuleSourceSync.RosterInventory.Build(path, tablePath); throw new Exception("Missing generated type accepted"); }
    catch (ArgumentException error) { Assert(error.Message.Contains("Missing or ambiguous")); }
    Assert(!File.Exists(marker));
});
Check("power state restoration has no application effects and world fork preserves private data and references", () => {
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(SignedCounterPower).Assembly));
    var creature = world.Get("p"); world.RestoreCombat("combat", CombatPhase.InProgress, "p");
    var applied = 0; var changed = 0; creature.PowerApplied += _ => applied++; creature.PowerIncreased += (_, _, _) => changed++;
    var power = (SignedCounterPower)world.RestorePower(creature, new("counter", ModelDb.GetId(typeof(SignedCounterPower)), -4, 7, true, "p", "p"));
    Assert(applied == 0 && changed == 0 && power.Amount == -4 && power.AmountOnTurnStart == 7 && power.SkipNextDurationTick);
    Assert(power.TypeForCurrentAmount == PowerType.Debuff && !power.ShouldRemoveDueToAmount());
    world.RestoreHookProjection([], ["counter"]); Run(world, () => CreatureCmd.Heal(creature, 2));
    power.DynamicVars.MaxHp.UpgradeValueBy(4); power.DynamicVars.MaxHp.PreviewValue = 99;
    var copy = world.Clone(); var sibling = world.Clone(); var forked = (SignedCounterPower)copy.RequireModel("counter");
    Assert(forked.Initializations == 1 && forked.Deltas.SequenceEqual([2m]));
    Assert(ReferenceEquals(forked.Owner, copy.Get("p")) && ReferenceEquals(forked.Applier, forked.Owner) && ReferenceEquals(forked.Target, forked.Owner));
    Assert(ReferenceEquals(forked.LastTarget, forked.Owner) && ReferenceEquals(copy.Get("p").GetPower<SignedCounterPower>(), forked));
    Assert(forked.DynamicVars.MaxHp.WasJustUpgraded && forked.DynamicVars.MaxHp.PreviewValue == 99 && forked.DynamicVars.MaxHp.BaseValue == 7);
    Run(copy, () => CreatureCmd.Heal(copy.Get("p"), 3)); forked.SetAmount(-9); forked.DynamicVars.MaxHp.UpgradeValueBy(1);
    Assert(power.Amount == -4 && power.Deltas.SequenceEqual([2m]) && forked.Deltas.SequenceEqual([2m, 3m]));
    Assert(((SignedCounterPower)sibling.RequireModel("counter")).Deltas.SequenceEqual([2m]) && changed == 0);
    var entityClone = (SignedCounterPower)forked.MutableClone();
    Assert(entityClone.Initializations == 2 && entityClone.Deltas.Count == 0 && entityClone.Owner == null);
    Assert(entityClone.DynamicVars.MaxHp.PreviewValue == 8 && forked.DynamicVars.MaxHp.WasJustUpgraded);
    try { WorldSnapshotCodec.Export(copy); throw new Exception("Power state silently omitted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "mechanism_not_implemented"); }
});
Check("native power amount events, cap, negative counters and removal ordering retain low-level semantics", () => {
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(SignedCounterPower).Assembly)); var creature = world.Get("p");
    var power = world.Models.Power<SignedCounterPower>().ToMutable(); var order = new List<string>();
    power.DisplayAmountChanged += () => order.Add("display");
    creature.PowerIncreased += (p, delta, silent) => order.Add($"increase:{delta}:{silent}");
    creature.PowerDecreased += (_, silent) => order.Add($"decrease:{silent}");
    creature.PowerApplied += _ => order.Add("applied");
    power.ApplyInternal(creature, 3.9m, true);
    Assert(power.Amount == 3 && order.SequenceEqual(["display", "increase:3:True", "applied"]));
    order.Clear(); power.SetAmount(3); Assert(order.Count == 0);
    power.SetAmount(-2, true); Assert(order.SequenceEqual(["display", "increase:-5:True"]));
    power.SetAmount(int.MinValue); Assert(power.Amount == -999999999 && !power.ShouldRemoveDueToAmount());
    power.SetAmount(int.MaxValue); Assert(power.Amount == 999999999);
    creature.BeforeTurnStart(CombatSide.Enemy); Assert(power.AmountOnTurnStart == 999999999);
    power.SetAmount(0); Assert(power.ShouldRemoveDueToAmount() && creature.HasPower<SignedCounterPower>()); // Remove is a separate command.
    order.Clear(); power.Removed += () => { Assert(creature.HasPower<SignedCounterPower>()); order.Add("removed"); };
    creature.PowerRemoved += _ => { Assert(!creature.HasPower<SignedCounterPower>()); order.Add("owner-removed"); };
    power.RemoveInternal(); Assert(order.SequenceEqual(["removed", "owner-removed"]));
    var debuff = world.Models.Power<DebuffCounterPower>().ToMutable(); debuff.ApplyInternal(creature, .9m);
    Assert(debuff.Amount == 0 && creature.HasPower<DebuffCounterPower>() && debuff.ShouldRemoveDueToAmount());
    Assert(debuff.GetTypeForAmount(-.1m) == PowerType.Buff && debuff.GetTypeForAmount(1) == PowerType.Debuff);
});
Check("power instance lookup stacks by type or applier identity without entity IDs", () => {
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(SignedCounterPower).Assembly));
    world.Add(new("other", 10, 10)); var owner = world.Get("p"); var other = world.Get("other");
    var first = world.RestorePower(owner, new("first", ModelDb.GetId(typeof(PerApplierPower)), 2, 1, false, "p", null));
    var second = world.RestorePower(owner, new("second", first.Id, 4, 2, false, "other", null));
    Assert(ReferenceEquals(PowerCmd.FindExistingInstanceForStacking(world.Models.Power<PerApplierPower>(), owner, owner), first));
    Assert(ReferenceEquals(PowerCmd.FindExistingInstanceForStacking(first, owner, other), second));
    Assert(PowerCmd.FindExistingInstanceForStacking(first, owner, null) == null);
    var instanced = world.RestorePower(owner, new("instance", ModelDb.GetId(typeof(AlwaysInstancedPower)), 5, 1, false, null, null));
    Assert(PowerCmd.FindExistingInstanceForStacking(instanced, owner, null) == null);
    var single = world.RestorePower(owner, new("single", ModelDb.GetId(typeof(DebuffCounterPower)), 1, 1, false, null, null));
    Assert(ReferenceEquals(PowerCmd.FindExistingInstanceForStacking(single, owner, other), single));
    try { world.RestorePower(owner, new("duplicate", single.Id, 1, 1, false, null, null)); throw new Exception("Duplicate non-instanced power accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "invalid_state" && owner.Powers.Count == 4); }
    Assert(owner.RemoveAllPowersInternalExcept([single]).Count() == 3 && owner.Powers.SequenceEqual([single]));
});
Check("restoring power inventory invalidates hook evidence and omitted powers cannot become exact transitions", () => {
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(SignedCounterPower).Assembly));
    world.RestoreCombat("combat", CombatPhase.InProgress, "p"); world.RestoreHookProjection([], []);
    _ = world.RestorePower(world.Get("p"), new("counter", ModelDb.GetId(typeof(SignedCounterPower)), 1, 1, false, null, null));
    try { world.RestoreHookProjection([], []); throw new Exception("Incomplete power inventory accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "state_incomplete"); }
    try { Run(world, () => CreatureCmd.Heal(world.Get("p"), 4)); throw new Exception("Unknown hooks accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "state_incomplete"); }
    Assert(world.Get("p").CurrentHp == 30 && world.Trace.Count == 0);
});
Check("fork rehashes reference-key collections while preserving free slots, aliases, lookup and future insertion order", () => {
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(IdentityLedgerRelic).Assembly));
    foreach (var id in new[] { "a", "b", "c", "d" }) world.Add(new(id, 10, 10));
    var ledger = Restore<IdentityLedgerRelic>(world, "ledger");
    foreach (var id in new[] { "p", "a", "b", "c" }) { ledger.Ledger.Add(world.Get(id), id.Length); ledger.Seen.Add(world.Get(id)); }
    ledger.Ledger.Remove(world.Get("a")); ledger.Seen.Remove(world.Get("a"));
    ledger.Ledger.Remove(world.Get("c")); ledger.Seen.Remove(world.Get("c"));
    ledger.ReferenceLedger.Add(world.Get("p"), "player");
    ledger.ReferenceLedger.Add(ledger, "self");
    var keys = ledger.Ledger.Keys; var values = ledger.Ledger.Values; // cached views must also point at the copied dictionary.
    var copy = world.Clone(); var fork = (IdentityLedgerRelic)copy.RequireModel("ledger");
    Assert(fork.Ledger.ContainsKey(copy.Get("p")) && !fork.Ledger.ContainsKey(world.Get("p")));
    Assert(fork.Seen.Contains(copy.Get("b")) && !fork.Seen.Contains(world.Get("b")));
    Assert(fork.ReferenceLedger[fork] == "self" && fork.ReferenceLedger[copy.Get("p")] == "player");
    ledger.Ledger.Add(world.Get("d"), 9); fork.Ledger.Add(copy.Get("d"), 9);
    ledger.Seen.Add(world.Get("d")); fork.Seen.Add(copy.Get("d"));
    Assert(ledger.Ledger.Keys.Select(c => c.InstanceId).SequenceEqual(fork.Ledger.Keys.Select(c => c.InstanceId)));
    Assert(ledger.Seen.Select(c => c.InstanceId).SequenceEqual(fork.Seen.Select(c => c.InstanceId)));
    fork.Ledger[copy.Get("p")] = 7; Assert(ledger.Ledger[world.Get("p")] == 1);
    var again = copy.Clone(); Assert(((IdentityLedgerRelic)again.RequireModel("ledger")).Ledger[again.Get("p")] == 7);
    ledger.CustomLedger.Add(new(), 1); var calls = IdentityLedgerRelic.CustomKey.Invocations;
    try { world.Clone(); throw new Exception("Custom key equality accepted without migration"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "clone_contract_missing"); }
    Assert(IdentityLedgerRelic.CustomKey.Invocations == calls);
});
Check("fork retains delegate identity across private captured locals and multiple fields without sharing closure state", () => {
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(DelegateGraphRelic).Assembly));
    var relic = Restore<DelegateGraphRelic>(world, "callback"); relic.Initialize(); Assert(relic.Callback!() == 1);
    var copy = world.Clone(); var fork = (DelegateGraphRelic)copy.RequireModel("callback");
    Assert(ReferenceEquals(fork.Callback, fork.Alias) && !ReferenceEquals(fork.Callback, relic.Callback));
    Assert(fork.Callback!() == 2 && fork.Alias!() == 3 && relic.Callback!() == 2);
    Assert(fork.Multicast!.GetInvocationList().All(d => ReferenceEquals(d, fork.Subscriber)));
    var next = (DelegateGraphRelic)copy.Clone().RequireModel("callback"); Assert(next.Callback!() == 4 && fork.Callback!() == 4);
    relic.InitializeArrayTarget(); Assert(relic.Callback!() == 1);
    fork = (DelegateGraphRelic)world.Clone().RequireModel("callback");
    Assert(ReferenceEquals(fork.Callback, fork.Alias) && fork.Callback!() == 2 && fork.Alias!() == 3 && relic.Callback!() == 2);
});
Check("native Artifact blocks negative Strength changes, consumes itself through nested commands and records zero received offsets", () => {
    var world = PowerWorld(); var owner = world.Get("p"); var choice = new ThrowingPlayerChoiceContext();
    Run(world, async () => {
        await PowerCmd.Apply<StrengthPower>(choice, owner, 3, null, null);
        await PowerCmd.Apply<ArtifactPower>(choice, owner, 1, null, null);
        var returned = await PowerCmd.Apply<StrengthPower>(choice, owner, -2, null, null);
        Assert(ReferenceEquals(returned, owner.GetPower<StrengthPower>()));
    });
    Assert(owner.GetPowerAmount<StrengthPower>() == 3 && !owner.HasPower<ArtifactPower>());
    Assert(world.CombatState!.History.PowerReceipts.Select(e => e.Amount).SequenceEqual([3m, 1m, 0m, -1m]));
    var strength = owner.GetPower<StrengthPower>()!;
    Assert(strength.ModifyDamageAdditive(owner, 8, ValueProp.Move, owner, null, null) == 3);
    Assert(strength.ModifyDamageAdditive(owner, 8, ValueProp.Unpowered | ValueProp.Move, owner, null, null) == 0);
    Assert(world.CombatState.History.PowerReceipts.All(e => e.HappenedThisTurn(world.CombatState)));
    Run(world, async () => Assert(await PowerCmd.Apply<StrengthPower>(choice, owner, -3, null, null) == null));
    Assert(!owner.HasPower<StrengthPower>() && world.CombatState.History.PowerReceipts.Last().Amount == -3);
});
Check("blocked new powers preserve native return/lifecycle behavior, invisible debuffs bypass Artifact and duration skips once", () => {
    var world = PowerWorld(); var owner = world.Get("p"); var choice = new ThrowingPlayerChoiceContext();
    Run(world, async () => {
        await PowerCmd.Apply<ArtifactPower>(choice, owner, 1, null, null);
        var blocked = await PowerCmd.Apply<DebuffCounterPower>(choice, owner, 2.9m, null, null);
        Assert(blocked != null && blocked.Owner == null && blocked.Amount == 0 && blocked.SkipNextDurationTick);
        Assert(!owner.HasPower<DebuffCounterPower>() && !owner.HasPower<ArtifactPower>());
        await PowerCmd.Apply<ArtifactPower>(choice, owner, 2, null, null);
        var hidden = await PowerCmd.Apply<InvisibleDebuffFixture>(choice, owner, 2.9m, null, null);
        Assert(hidden!.Amount == 2 && owner.GetPowerAmount<ArtifactPower>() == 2);
        Assert(hidden.SkipNextDurationTick);
        await PowerCmd.TickDownDuration(hidden); Assert(hidden.Amount == 2 && !hidden.SkipNextDurationTick);
        // Negative amount on a nonnegative debuff is a buff: Artifact must not block its duration tick.
        await PowerCmd.TickDownDuration(hidden); Assert(hidden.Amount == 1 && owner.GetPowerAmount<ArtifactPower>() == 2);
        await PowerCmd.TickDownDuration(hidden); Assert(!owner.HasPower<InvisibleDebuffFixture>());
    });
    Assert(world.CombatState!.History.PowerReceipts.Count == 6);
});
Check("temporary Strength removes its own model before expiry changes and reuses the original Artifact path", () => {
    var world = PowerWorld(); var owner = world.Get("p"); var choice = new ThrowingPlayerChoiceContext();
    Run(world, async () => {
        var temporary = await PowerCmd.Apply<TemporaryGainFixture>(choice, owner, 2, owner, null);
        Assert(temporary!.Amount == 2 && owner.GetPowerAmount<StrengthPower>() == 2);
        await PowerCmd.Apply<TemporaryGainFixture>(choice, owner, 1, owner, null);
        Assert(temporary.Amount == 3 && owner.GetPowerAmount<StrengthPower>() == 3);
        await PowerCmd.Apply<ArtifactPower>(choice, owner, 1, null, null);
        var order = new List<string>();
        owner.PowerRemoved += p => order.Add(p.GetType().Name);
        await temporary.AfterSideTurnEnd(choice, CombatSide.Player, [owner]);
        Assert(order.SequenceEqual([nameof(TemporaryGainFixture), nameof(ArtifactPower)]));
        Assert(!owner.HasPower<TemporaryGainFixture>() && owner.GetPowerAmount<StrengthPower>() == 3);
    });
    var negative = PowerWorld(); var target = negative.Get("p");
    Run(negative, async () => {
        var temporary = await PowerCmd.Apply<TemporaryLossFixture>(choice, target, 2, null, null);
        Assert(target.GetPowerAmount<StrengthPower>() == -2);
        await temporary!.AfterSideTurnEnd(choice, CombatSide.Player, [target]);
        Assert(!target.HasPower<TemporaryLossFixture>() && !target.HasPower<StrengthPower>());
    });
});
Check("power modifiers preserve additive/multiplicative phases and invoke each changed model once after modification", () => {
    var world = PowerWorld(); var first = Restore<PowerOrderRelic>(world, "first"); var second = Restore<PowerOrderRelic>(world, "second");
    first.Additive = 1; first.Multiplier = 2; second.Additive = 2; second.Multiplier = 3;
    world.RestoreCombatHookInventory(HookObservation(world));
    Run(world, async () => {
        var power = await PowerCmd.Apply<SignedCounterPower>(new ThrowingPlayerChoiceContext(), world.Get("p"), 2, world.Get("p"), null);
        Assert(power!.Amount == 30);
    });
    Assert(first.Calls.SequenceEqual(["before:2", "add:2", "multiply:5", "modified:30", "after:30"]));
    Assert(second.Calls.SequenceEqual(["before:2", "add:3", "multiply:10", "modified:30", "after:30"]));
});
Check("native ModifyAmount truncates offsets, keeps its unclamped return and logs history before amount conversion", () => {
    var world = PowerWorld(); var choice = new ThrowingPlayerChoiceContext();
    Run(world, async () => {
        var power = await PowerCmd.Apply<StrengthPower>(choice, world.Get("p"), 1, null, null);
        var value = await PowerCmd.ModifyAmount(choice, power!, -.9m, null, null);
        Assert(value == 1 && power!.Amount == 1);
        power!.SetAmount(999999999);
        value = await PowerCmd.ModifyAmount(choice, power, 2.9m, null, null);
        Assert(value == 1000000001 && power.Amount == 999999999);
    });
    Assert(world.CombatState!.History.PowerReceipts.Select(e => e.Amount).SequenceEqual([1m, -.9m, 2.9m]));
    var failed = world.Clone(); var sibling = world.Clone(); var count = world.CombatState.History.PowerReceipts.Count;
    try { Run(failed, () => PowerCmd.ModifyAmount(choice, failed.Get("p").GetPower<StrengthPower>()!, (decimal)int.MaxValue + 1, null, null)); throw new Exception("Overflow offset accepted"); }
    catch (OverflowException) { }
    Assert(failed.IsFaulted && failed.CombatState!.History.PowerReceipts.Count == count + 1);
    Assert(world.CombatState.History.PowerReceipts.Count == count && sibling.CombatState!.History.PowerReceipts.Count == count);
    Assert(!ReferenceEquals(sibling.CombatState!.History.PowerReceipts[0].Power, world.CombatState.History.PowerReceipts[0].Power));
    Assert(ReferenceEquals(sibling.CombatState!.History.PowerReceipts[0].Actor, sibling.Get("p")));
});
Check("missing hook domains/timeline/history stop Power commands before callbacks and fixture projection cannot fill gaps", () => {
    foreach (var variant in new[] { "deck", "powers", "timeline", "history", "nonempty" })
    {
        var world = PowerWorld(timeline: variant != "timeline", history: variant != "history");
        var ledger = Restore<PowerOrderRelic>(world, "ledger"); var observed = HookObservation(world);
        if (variant == "deck") observed = observed with { Players = [observed.Players![0] with { DeckCards = null }] };
        if (variant == "powers") observed.CreaturePowers!["p"] = null;
        if (variant == "nonempty") observed = observed with { Players = [observed.Players![0] with { DeckCards = ["unported-card"] }] };
        world.RestoreCombatHookInventory(observed); world.RestoreHookProjection([], ["ledger"]);
        try { Run(world, () => PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), world.Get("p"), 2, null, null)); throw new Exception("Unknown domains accepted"); }
        catch (RuleBoundaryException error) { Assert(error.Code == (variant == "nonempty" ? "invalid_state" : "state_incomplete")); }
        Assert(ledger.Calls.Count == 0 && world.Get("p").Powers.Count == 0 && world.Get("p").CurrentHp == 30);
    }
});
Check("combat hook dispatch gates once at start, preserves a started sequence and supports the setup phase", () => {
    var world = PowerWorld(); var first = Restore<PowerOrderRelic>(world, "first"); var second = Restore<PowerOrderRelic>(world, "second");
    first.EndOnBefore = true; world.RestoreCombatHookInventory(HookObservation(world));
    Run(world, () => PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), world.Get("p"), 2, null, null));
    Assert(first.Calls.SequenceEqual(["before:2"]) && second.Calls.SequenceEqual(["before:2"]));
    Assert(world.CombatState!.Phase == CombatPhase.Ending && world.CombatState.IsInProgress && world.Get("p").GetPowerAmount<StrengthPower>() == 2);
    Run(world, async () => Assert(await PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), world.Get("p"), 3, null, null) == null));
    var setup = PowerWorld(CombatPhase.Starting); var ledger = Restore<PowerOrderRelic>(setup, "ledger"); setup.RestoreCombatHookInventory(HookObservation(setup));
    Assert(setup.CombatState!.IsStarting && setup.CombatState.IsOverOrEnding && !setup.CombatState.IsInProgress);
    Run(setup, () => PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), setup.Get("p"), 1, null, null));
    Assert(ledger.Calls.SequenceEqual(["before:1", "after:1"]));
});
Check("power targeting guards, instance stacking, silent forwarding and no-choice context follow native distinctions", () => {
    var world = PowerWorld(); var owner = world.Get("p"); var choice = new ThrowingPlayerChoiceContext();
    var silentValues = new List<bool>(); owner.PowerIncreased += (_, _, silent) => silentValues.Add(silent);
    Run(world, async () => {
        var first = await PowerCmd.Apply<PerApplierPower>(choice, owner, 1, owner, null, silent: true);
        var same = await PowerCmd.Apply<PerApplierPower>(choice, owner, 1, owner, null, silent: true);
        Assert(ReferenceEquals(first, same) && same!.Amount == 2);
        var fromNull = await PowerCmd.Apply<PerApplierPower>(choice, owner, 2, null, null);
        Assert(!ReferenceEquals(fromNull, first));
        await PowerCmd.Apply(choice, world.Models.Power<PerApplierPower>().ToMutable(), owner, 1, owner, null, silent: true);
        Assert(silentValues.SequenceEqual([true, true, false, false]));
        var a = await PowerCmd.Apply<AlwaysInstancedPower>(choice, owner, 1, null, null);
        var b = await PowerCmd.Apply<AlwaysInstancedPower>(choice, owner, 1, null, null); Assert(!ReferenceEquals(a, b));
        choice.PushModel(first!); choice.PushModel(fromNull!); choice.PopModel(first!);
        Assert(ReferenceEquals(choice.LastInvolvedModel, fromNull)); choice.PopModel(fromNull!); Assert(ReferenceEquals(choice.LastInvolvedModel, first));
        try { await choice.SignalPlayerChoiceBegun(owner.Player!, 0); throw new Exception("Unexpected choice swallowed"); }
        catch (NotImplementedException) { }
    });
    var guarded = PowerWorld(); var guard = Restore<PowerOrderRelic>(guarded, "guard"); guard.PreventHitting = true;
    guarded.RestoreCombatHookInventory(HookObservation(guarded));
    Run(guarded, async () => Assert(await PowerCmd.Apply<StrengthPower>(choice, guarded.Get("p"), 2, null, null) == null));
    Assert(guarded.Get("p").Powers.Count == 0 && guarded.CombatState!.History.PowerReceipts.Count == 0 && guard.Calls.Count == 0);
});
Check("native listener snapshots recheck active ownership between callbacks and failed callbacks remain branch-local", () => {
    var world = PowerWorld(); var first = Restore<PowerOrderRelic>(world, "first"); var second = Restore<PowerOrderRelic>(world, "second");
    first.DeactivateOnBefore = true; world.RestoreCombatHookInventory(HookObservation(world));
    Run(world, () => PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), world.Get("p"), 2, null, null));
    Assert(first.Calls.SequenceEqual(["before:2"]) && second.Calls.Count == 0 && !world.RunState.Players[0].IsActiveForHooks);
    var source = PowerWorld(); var observer = Restore<PowerOrderRelic>(source, "observer"); observer.ThrowOnBefore = true;
    source.RestoreCombatHookInventory(HookObservation(source)); var failed = source.Clone(); var sibling = source.Clone();
    try { Run(failed, () => PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), failed.Get("p"), 2, null, null)); throw new Exception("Observer failure swallowed"); }
    catch (InvalidOperationException error) { Assert(error.Message == "fixture before failure"); }
    Assert(failed.IsFaulted && ((PowerOrderRelic)failed.RequireModel("observer")).Calls.SequenceEqual(["before:2"]));
    Assert(failed.Get("p").Powers.Count == 0 && failed.CombatState!.History.PowerReceipts.Count == 0);
    Assert(observer.Calls.Count == 0 && ((PowerOrderRelic)sibling.RequireModel("observer")).Calls.Count == 0 && !source.IsFaulted && !sibling.IsFaulted);
});
Check("native shared scaling model joins listener order and forks with its run/combat references intact", () => {
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(PowerOrderRelic).Assembly));
    world.Add(new("p2", 20, 50)); world.RestorePlayer(8, "p2", true); world.RestoreCombat("two-players", CombatPhase.InProgress, "p", "p2");
    var relic = Restore<PowerOrderRelic>(world, "observer");
    var scaling = world.RestoreMultiplayerScaling("scaling", attachedToCombat: true);
    world.RestoreActIndex(2); world.RestoreAbsentCombatEncounter();
    world.RestoreCombatHookInventory(HookObservation(world));
    world.RestoreCombatTimeline(1, CombatSide.Player, new Dictionary<ulong, int> { [7] = 1, [8] = 1 }); world.RestoreEmptyCombatPowerHistory();
    var order = new List<string>(); relic.ExecutionFinished += _ => order.Add("relic"); scaling.ExecutionFinished += _ => order.Add("scaling");
    Run(world, () => PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), world.Get("p"), 1, null, null));
    Assert(order.SequenceEqual(["relic", "scaling", "relic", "scaling"]));
    Assert(world.Models.Power<SignedCounterPower>().GetScaledAmountForMultiplayer(world.CombatState!, null, 5, world.Get("p"), null) == 12);
    Assert(world.Models.Power<ArtifactPower>().GetScaledAmountForMultiplayer(world.CombatState!, null, 5, world.Get("p"), null) == 6);
    Assert(scaling.ModifyBlockMultiplicative(world.Get("p"), 10, ValueProp.Move, null, null) == 1);
    var boss = world.Models.GetById<BossEncounterFixture>(ModelDb.GetId(typeof(BossEncounterFixture)));
    Assert(MultiplayerScalingModel.GetMultiplayerScaling(null, 0) == 1.1m && MultiplayerScalingModel.GetMultiplayerScaling(null, 1) == 1.2m);
    Assert(MultiplayerScalingModel.GetMultiplayerScaling(null, 2) == 1.2m && MultiplayerScalingModel.GetMultiplayerScaling(boss, 2) == 1.3m);
    var copy = world.Clone(); Assert(!ReferenceEquals(copy.RunState.MultiplayerScalingModel, scaling));
    Assert(ReferenceEquals(copy.RunState.MultiplayerScalingModel, copy.CombatState!.MultiplayerScalingModel));
    Assert(copy.RunState.CurrentActIndex == 2 && copy.CombatState.Encounter == null);
    Run(copy, () => PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), copy.Get("p"), 1, null, null));
    Assert(copy.Get("p").GetPowerAmount<StrengthPower>() == 2 && world.Get("p").GetPowerAmount<StrengthPower>() == 1 && order.Count == 4);
    Assert(copy.CombatState.History.PowerReceipts.All(e => e.HappenedThisTurn(copy.CombatState)));
    Assert(ReferenceEquals(copy.CombatState.History.PowerReceipts[0].History, copy.CombatState.History));
    copy.RestoreCombatTimeline(2, CombatSide.Enemy, new Dictionary<ulong, int> { [7] = 2, [8] = 2 });
    Assert(copy.CombatState.History.PowerReceipts.All(e => !e.HappenedThisTurn(copy.CombatState)));
    Assert(copy.CombatState.History.PowerReceipts.All(e => e.HappenedLastPlayerTurn(copy.RunState.Players[0])));
    Assert(world.CombatState!.History.PowerReceipts[0].HappenedThisTurn(world.CombatState));
    try { WorldSnapshotCodec.Export(copy); throw new Exception("New state was discarded by old checkpoint"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "mechanism_not_implemented"); }
});
Check("hook observations are defensively owned and Minion flags preserve secondary-enemy and fatal semantics", () => {
    var world = PowerWorld(); var observed = HookObservation(world); world.RestoreCombatHookInventory(observed);
    observed.Allies![0] = "external-change"; observed.CreaturePowers!["p"] = null;
    observed.Players![0] = observed.Players[0] with { DeckCards = null };
    Run(world, async () => {
        var power = await PowerCmd.Apply<MinionPower>(new ThrowingPlayerChoiceContext(), world.Get("p"), 1, null, null);
        Assert(power!.OwnerIsSecondaryEnemy && !power.ShouldPlayVfx && !power.ShouldOwnerDeathTriggerFatal() && !power.ShouldPowerBeRemovedAfterOwnerDeath());
        Assert(!world.Get("p").IsSecondaryEnemy); // A player-side creature does not become an enemy from this flag.
        await PowerCmd.Remove<MinionPower>(world.Get("p")); Assert(world.Get("p").Powers.Count == 0);
    });
    var bad = PowerWorld(); var input = HookObservation(bad);
    bad.RestoreCombatHookInventory(input with { Players = [input.Players![0] with { CombatPiles = [[]] }] });
    try { Run(bad, () => PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), bad.Get("p"), 1, null, null)); throw new Exception("Incomplete native pile layout accepted"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "invalid_state"); }
});
Check("native early exits do not demand unused card/history state while actual card-source execution is rejected", () => {
    var world = PowerWorld(timeline: false, history: false); var guard = Restore<PowerOrderRelic>(world, "guard");
    guard.PreventHitting = true; world.RestoreCombatHookInventory(HookObservation(world));
    var card = world.Models.GetById<UnportedCardFixture>(ModelDb.GetId(typeof(UnportedCardFixture)));
    var choice = new ThrowingPlayerChoiceContext();
    Run(world, async () => Assert(await PowerCmd.Apply<StrengthPower>(choice, world.Get("p"), 2, null, card) == null));
    Assert(guard.Calls.Count == 0 && world.Get("p").Powers.Count == 0);
    guard.PreventHitting = false;
    Run(world, async () => {
        var empty = await PowerCmd.Apply<StrengthPower>(choice, world.Get("p"), 0, null, card);
        Assert(empty != null && empty.Owner == null && empty.Amount == 0 && world.Get("p").Powers.Count == 0);
    });
    var outside = PlayerWorld(); Run(outside, async () => Assert(await PowerCmd.Apply<StrengthPower>(choice, outside.Get("p"), 1, null, card) == null));
    var actual = PowerWorld(); var observer = Restore<PowerOrderRelic>(actual, "observer"); actual.RestoreCombatHookInventory(HookObservation(actual));
    var source = actual.Models.GetById<UnportedCardFixture>(ModelDb.GetId(typeof(UnportedCardFixture)));
    try { Run(actual, () => PowerCmd.Apply<StrengthPower>(choice, actual.Get("p"), 1, null, source)); throw new Exception("Card source executed through nominal type"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "mechanism_not_implemented"); }
    Assert(observer.Calls.Count == 0 && actual.Get("p").Powers.Count == 0 && actual.CombatState!.History.PowerReceipts.Count == 0);
    var detached = PlayerWorld(); var retained = detached.RestorePower(detached.Get("p"), new("removed", ModelDb.GetId(typeof(StrengthPower)), 1, 1, false, null, null));
    retained.RemoveInternal(); Assert(detached.Get("p").Powers.Count == 0);
    try { WorldSnapshotCodec.Export(detached); throw new Exception("Detached model state was discarded"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "mechanism_not_implemented"); }
});
Check("native targeted-power visibility uses world-local identity, including known absence, without sharing observer context", () => {
    var world = PlayerWorld(registry: new ModelRegistry(typeof(Mango).Assembly, typeof(PowerOrderRelic).Assembly));
    world.Add(new("p2", 20, 50)); world.RestorePlayer(8, "p2", true); world.RestoreCombat("visibility", CombatPhase.InProgress, "p", "p2");
    world.RestoreCombatHookInventory(HookObservation(world)); world.RestoreCombatTimeline(1, CombatSide.Player, new Dictionary<ulong, int> { [7] = 1, [8] = 1 });
    world.RestoreEmptyCombatPowerHistory(); var choice = new ThrowingPlayerChoiceContext();
    var targeted = world.Models.Power<DebuffCounterPower>().ToMutable(); targeted.Target = world.Get("p2");
    try { _ = targeted.IsVisible; throw new Exception("Unknown local player became a guessed identity"); }
    catch (RuleBoundaryException error) { Assert(error.Code == "state_incomplete"); }
    world.RestoreLocalPlayer(7); Assert(!targeted.IsVisible);
    Run(world, async () => {
        await PowerCmd.Apply<ArtifactPower>(choice, world.Get("p"), 1, null, null);
        await PowerCmd.Apply(choice, targeted, world.Get("p"), 2, null, null);
        Assert(world.Get("p").GetPowerAmount<ArtifactPower>() == 1 && targeted.Amount == 2);
    });
    var copy = world.Clone(); copy.RestoreLocalPlayer(8);
    var forked = (DebuffCounterPower)copy.RequireModel(targeted.InstanceId!);
    Assert(forked.IsVisible && !targeted.IsVisible && world.LocalContext.NetId == 7 && copy.LocalContext.NetId == 8);
    Run(copy, () => PowerCmd.ModifyAmount(choice, forked, 1, null, null));
    Assert(forked.Amount == 2 && !copy.Get("p").HasPower<ArtifactPower>() && world.Get("p").GetPowerAmount<ArtifactPower>() == 1);
    copy.RestoreLocalPlayer(null); Assert(!forked.IsVisible && copy.LocalContext.NetId == null);
    Assert(world.Get("p").HasPower<ArtifactPower>());
});
CardMechanismTests.Run(Check);
EnergyMechanismTests.Run(Check);
CardParameterTests.Run(Check);
KeywordMechanismTests.Run(Check);
UpgradeMechanismTests.Run(Check);
ResourcePaymentTests.Run(Check);

if (failures.Count == 0)
{
    var compiled = new ModelRegistry(typeof(Mango).Assembly).Ids.Order().Select(id => id.ToString()).ToArray();
    var report = Path.Combine(Environment.CurrentDirectory, ".runtime", "analysis", "independent-compiled-roster.json");
    Directory.CreateDirectory(Path.GetDirectoryName(report)!);
    File.WriteAllText(report, JsonSerializer.Serialize(new { scope = "compiled_definitions_only", models = compiled, full_model_support_certified = 0 }, new JsonSerializerOptions { WriteIndented = true }) + "\n");
}
Console.WriteLine($"{executed - failures.Count} passed, {failures.Count} failed");
return failures.Count == 0 ? 0 : 1;
