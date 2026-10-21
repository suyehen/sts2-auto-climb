using System.Reflection;
using System.Runtime.Loader;
using System.Text;
using System.Text.Json.Nodes;
using AutoSlay.RuleSourceSync;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Models.Relics;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;

internal static class CardParameterTests
{
    private const string Source = """
        using MegaCrit.Sts2.Core.Entities.Cards;
        using MegaCrit.Sts2.Core.Localization.DynamicVars;
        namespace MegaCrit.Sts2.Core.Models.Cards;
        public sealed class ParameterizedCostCard : CardModel {
            public int ConstructorCost { get; }
            public int ConstructorStarCost { get; }
            public decimal ConstructorVariable { get; }
            public int __ruleParameters { get; } = 17;
            public override int CanonicalStarCost => 5;
            protected override System.Collections.Generic.IEnumerable<DynamicVar> CanonicalVars => new DynamicVar[] { new StarsVar(7) };
            public ParameterizedCostCard()
                : base(canonicalEnergyCost: 3, type: CardType.Skill, rarity: CardRarity.Common, targetType: TargetType.Self) {
                ConstructorCost = EnergyCost.Canonical;
                ConstructorStarCost = BaseStarCost;
                ConstructorVariable = DynamicVars.Stars.BaseValue;
            }
            protected override void OnUpgrade() {
                if (EnergyCost.GetWithModifiers(CostModifiers.None) > 0) EnergyCost.UpgradeBy(-1);
                UpgradeStarCostBy(-1);
            }
        }
        """;
    private static void Assert(bool value) { if (!value) throw new Exception("Card parameter assertion failed"); }
    private static (string Root, string Source, string Output) Stage(string source)
    {
        var root = Path.Combine(Environment.CurrentDirectory, ".runtime", "card-parameter-" + Guid.NewGuid().ToString("N"));
        var input = Path.Combine(root, "source"); var output = Path.Combine(root, "output"); Directory.CreateDirectory(input);
        File.WriteAllText(Path.Combine(input, "Card.cs"), source); SourceStage.Write(input, output, true, ["Card.cs"]);
        return (root, input, output);
    }
    private static Assembly Compile(string directory)
    {
        var refs = ((string)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES")!).Split(Path.PathSeparator)
            .Append(typeof(CardModel).Assembly.Location).Distinct().Select(p => MetadataReference.CreateFromFile(p));
        var source = File.ReadAllText(Path.Combine(directory, "Code", "Card.cs"));
        var compilation = CSharpCompilation.Create("CardParameters_" + Guid.NewGuid().ToString("N"),
            [CSharpSyntaxTree.ParseText(source)], refs, new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary));
        using var binary = new MemoryStream(); var result = compilation.Emit(binary);
        if (!result.Success) throw new Exception(string.Join('\n', result.Diagnostics));
        binary.Position = 0; return AssemblyLoadContext.Default.LoadFromStream(binary);
    }
    private static RuleParameters Package(JsonNode node) => RuleParameters.Load(Encoding.UTF8.GetBytes(node.ToJsonString()),
        node["behavior_sha256"]!.GetValue<string>(), node["contract_sha256"]!.GetValue<string>());
    private static CardModel Model(SimulationWorld world, Type type) => world.Models.GetById<CardModel>(ModelDb.GetId(type));
    public static void Run(Action<string, Action> check)
    {
        check("source card constructor reads energy star cost and variables from the same numeric package before initialization", () => {
            var stage = Stage(Source); var path = Path.Combine(stage.Output, "rule-parameters.json");
            var original = JsonNode.Parse(File.ReadAllBytes(path))!; var generated = File.ReadAllText(Path.Combine(stage.Output, "Code", "Card.cs"));
            var oldPath = Path.Combine(stage.Root, "original.json"); File.WriteAllText(oldPath, original.ToJsonString());
            Assert(original["entries"]!.AsArray().Count == 5 && generated.Contains("__ruleParameters1.Int32"));
            Assert(generated.Contains("ruleParameters:__ruleParameters1"));
            Assert(generated.Contains("ConstructorCost = EnergyCost.Canonical;") && generated.Contains("CostModifiers.None) > 0"));
            Assert(!generated.Contains("canonicalEnergyCost: 3") && !generated.Contains("UpgradeBy(-1)"));
            var assembly = Compile(stage.Output); var type = assembly.GetType("AutoSlay.Rules.Native.Models.Cards.ParameterizedCostCard")!;
            var registry = new ModelRegistry(typeof(Mango).Assembly, assembly);
            var world = new SimulationWorld(Package(original), registry); var card = Model(world, type);
            Assert(card.EnergyCost.Canonical == 3 && (int)type.GetProperty("ConstructorCost")!.GetValue(card)! == 3);
            Assert((int)type.GetProperty("ConstructorStarCost")!.GetValue(card)! == 5 && (decimal)type.GetProperty("ConstructorVariable")!.GetValue(card)! == 7m);
            var mutable = card.ToMutable(); mutable.UpgradeInternal();
            Assert(mutable.EnergyCost.GetWithModifiers(CostModifiers.None) == 2 && mutable.EnergyCost.WasJustUpgraded);
            Assert(mutable.BaseStarCost == 4 && mutable.WasStarCostJustUpgraded);
            var updatedSource = Source.Replace("canonicalEnergyCost: 3", "canonicalEnergyCost: 6").Replace("UpgradeBy(-1)", "UpgradeBy(-2)").Replace("UpgradeStarCostBy(-1)", "UpgradeStarCostBy(-3)")
                .Replace("CanonicalStarCost => 5", "CanonicalStarCost => 8").Replace("StarsVar(7)", "StarsVar(11)");
            File.WriteAllText(Path.Combine(stage.Source, "Card.cs"), updatedSource); SourceStage.Write(stage.Source, stage.Output, true, ["Card.cs"]);
            var updated = JsonNode.Parse(File.ReadAllBytes(path))!;
            Assert(generated == File.ReadAllText(Path.Combine(stage.Output, "Code", "Card.cs")));
            var difference = ParameterDifference.Compare(oldPath, path);
            Assert(difference.Classification == "numeric_data_only_within_extracted_scope" && difference.ChangedValues.Length == 5);
            Assert(original["source_fingerprint"]!.GetValue<string>() != updated["source_fingerprint"]!.GetValue<string>());
            var changedWorld = new SimulationWorld(Package(updated), registry); var changed = Model(changedWorld, type);
            Assert(changed.EnergyCost.Canonical == 6 && (int)type.GetProperty("ConstructorCost")!.GetValue(changed)! == 6);
            Assert((int)type.GetProperty("ConstructorStarCost")!.GetValue(changed)! == 8 && (decimal)type.GetProperty("ConstructorVariable")!.GetValue(changed)! == 11m);
            var changedMutable = changed.ToMutable(); changedMutable.UpgradeInternal();
            Assert(changedMutable.EnergyCost.GetWithModifiers(CostModifiers.None) == 4 && card.EnergyCost.Canonical == 3);
            Assert(changedMutable.BaseStarCost == 5 && card.BaseStarCost == 5 && card.DynamicVars.Stars.BaseValue == 7m);
            File.WriteAllText(Path.Combine(stage.Source, "Card.cs"), updatedSource.Replace("CardType.Skill", "CardType.Attack"));
            SourceStage.Write(stage.Source, stage.Output, true, ["Card.cs"]);
            Assert(ParameterDifference.Compare(oldPath, path).Classification == "behavior_changed_semantic_migration_required");
        });
        check("parameterized model factories keep world data isolated before constructors and clones do not rerun creation", () => {
            var stage = Stage(Source); var assembly = Compile(stage.Output); var type = assembly.GetType("AutoSlay.Rules.Native.Models.Cards.ParameterizedCostCard")!;
            var registry = new ModelRegistry(typeof(Mango).Assembly, assembly);
            var data = JsonNode.Parse(File.ReadAllBytes(Path.Combine(stage.Output, "rule-parameters.json")))!;
            var first = Package(data);
            data["entries"]!.AsArray().Single(e => e!["key"]!.GetValue<string>().Contains(".ctor."))!["value"] = 9;
            data["entries"]!.AsArray().Single(e => e!["key"]!.GetValue<string>().Contains(".CanonicalStarCost::"))!["value"] = 13;
            data["entries"]!.AsArray().Single(e => e!["key"]!.GetValue<string>().Contains(".CanonicalVars::"))!["value"] = 19m;
            var second = Package(data); var a = new SimulationWorld(first, registry); var b = new SimulationWorld(second, registry);
            void AssertConstructor(SimulationWorld world, int energy, int stars, decimal variable)
            {
                var card = Model(world, type);
                Assert(card.EnergyCost.Canonical == energy && card.BaseStarCost == stars && card.DynamicVars.Stars.BaseValue == variable);
                Assert((int)type.GetProperty("ConstructorCost")!.GetValue(card)! == energy);
                Assert((int)type.GetProperty("ConstructorStarCost")!.GetValue(card)! == stars);
                Assert((decimal)type.GetProperty("ConstructorVariable")!.GetValue(card)! == variable);
            }
            Parallel.Invoke(() => AssertConstructor(a, 3, 5, 7m), () => AssertConstructor(b, 9, 13, 19m));
            Assert(ReferenceEquals(Model(a, type), Model(a, type)) && !ReferenceEquals(Model(a, type), Model(b, type)));
            var fork = b.Clone(); var forked = Model(fork, type);
            Assert(forked.EnergyCost.Canonical == 9 && (int)type.GetProperty("ConstructorCost")!.GetValue(forked)! == 9);
            AssertConstructor(fork, 9, 13, 19m);
            Assert(!ReferenceEquals(forked, Model(b, type)) && ReferenceEquals(fork.Parameters, second));
            var missing = new SimulationWorld(registry: registry);
            try { Model(missing, type); throw new Exception("Missing constructor parameters accepted"); }
            catch (RuleBoundaryException e) { Assert(e.Code == "parameter_missing"); }
            var missingKey = new SimulationWorld(CompiledRules.LoadDefaultParameters(), registry);
            try { Model(missingKey, type); throw new Exception("Missing constructor binding accepted"); }
            catch (RuleBoundaryException e) { Assert(e.Code == "parameter_missing"); }
            Assert(type.GetConstructor(Type.EmptyTypes) == null && type.GetConstructor([typeof(RuleParameters)]) != null);
        });
        check("unsupported card constructor graphs and indirect costs remain source code with explicit extraction gaps", () => {
            const string source = """
                using MegaCrit.Sts2.Core.Entities.Cards;
                namespace MegaCrit.Sts2.Core.Models.Cards;
                public class Indirect : CardModel {
                    const int Cost = 3;
                    public Indirect() : base(Cost, CardType.Skill, CardRarity.Common, TargetType.Self) { }
                }
                public class Chained : CardModel {
                    public Chained() : this(2) { }
                    public Chained(int n) : base(n, CardType.Skill, CardRarity.Common, TargetType.Self) { }
                }
                public partial class Partial : CardModel {
                    public Partial() : base(1, CardType.Skill, CardRarity.Common, TargetType.Self) { }
                }
                public class MinCost : CardModel {
                    public MinCost() : base(-2147483648, CardType.Status, CardRarity.Status, TargetType.Self) { }
                }
                """;
            var stage = Stage(source); var scope = JsonNode.Parse(File.ReadAllBytes(Path.Combine(stage.Output, "parameter-scope.json")))!;
            var sites = scope["constructor_cost_sites"]!.AsArray();
            Assert(sites.Count == 4 && sites.Count(s => s!["status"]!.GetValue<string>() == "bound") == 1);
            var reasons = sites.Select(s => s!["reason"]!.GetValue<string>()).ToArray();
            Assert(reasons.Contains("cost_not_direct_int32_literal") && reasons.Contains("requires_single_instance_constructor") && reasons.Contains("partial_type_requires_constructor_graph"));
            var package = JsonNode.Parse(File.ReadAllBytes(Path.Combine(stage.Output, "rule-parameters.json")))!;
            Assert(package["entries"]!.AsArray().Single()!["value"]!.GetValue<int>() == int.MinValue);
            var generated = File.ReadAllText(Path.Combine(stage.Output, "Code", "Card.cs"));
            Assert(generated.Contains("base(Cost,") && generated.Contains("public Chained() : this(2)") && generated.Contains("public Partial() : base(1,"));
            var assembly = Compile(stage.Output); var world = new SimulationWorld(Package(package), new ModelRegistry(assembly));
            var minCost = Model(world, assembly.GetType("AutoSlay.Rules.Native.Models.Cards.MinCost")!);
            Assert(minCost.EnergyCost.Canonical == int.MinValue);
            var wrongType = Stage(Source.Replace("canonicalEnergyCost: 3", "canonicalEnergyCost: 3u"));
            var wrongScope = JsonNode.Parse(File.ReadAllBytes(Path.Combine(wrongType.Output, "parameter-scope.json")))!;
            Assert(wrongScope["constructor_cost_sites"]!.AsArray().Single()!["status"]!.GetValue<string>() == "unbound");
            Assert(File.ReadAllText(Path.Combine(wrongType.Output, "Code", "Card.cs")).Contains("canonicalEnergyCost: 3u"));
        });
        check("energy cost binding preserves arithmetic and unrelated UpgradeBy calls as compiled behavior", () => {
            const string source = """
                namespace MegaCrit.Sts2.Core.Models.Cards;
                public class Sample {
                    public void OnUpgrade() {
                        EnergyCost.UpgradeBy(-2);
                        EnergyCost.UpgradeBy(-2147483648);
                        OtherCost.UpgradeBy(-7);
                        EnergyCost.UpgradeBy(-3 * scale);
                    }
                }
                """;
            var stage = Stage(source); var package = JsonNode.Parse(File.ReadAllBytes(Path.Combine(stage.Output, "rule-parameters.json")))!;
            Assert(package["entries"]!.AsArray().Count == 2 && package["entries"]!.AsArray().Any(e => e!["value"]!.GetValue<decimal>() == int.MinValue));
            var generated = File.ReadAllText(Path.Combine(stage.Output, "Code", "Card.cs"));
            Assert(generated.Contains("OtherCost.UpgradeBy(-7)") && generated.Contains("EnergyCost.UpgradeBy(-3 * scale)"));
            var scope = JsonNode.Parse(File.ReadAllBytes(Path.Combine(stage.Output, "parameter-scope.json")))!;
            Assert(!scope["full_parameter_coverage"]!.GetValue<bool>() && scope["unbound_numeric_literals"]!.AsArray().Count == 2);
        });
        check("canonical star getter binding preserves supported syntax and explicitly reports indirect or missing constructor context", () => {
            foreach (var getter in new[] { "=> -2;", "{ get => -2; }", "{ get { return -2; } }" })
            {
                var stage = Stage(Source.Replace("=> 5;", getter));
                var scope = JsonNode.Parse(File.ReadAllBytes(Path.Combine(stage.Output, "parameter-scope.json")))!;
                Assert(scope["canonical_star_cost_sites"]!.AsArray().Single()!["status"]!.GetValue<string>() == "bound");
                var package = JsonNode.Parse(File.ReadAllBytes(Path.Combine(stage.Output, "rule-parameters.json")))!;
                var assembly = Compile(stage.Output); var type = assembly.GetType("AutoSlay.Rules.Native.Models.Cards.ParameterizedCostCard")!;
                var world = new SimulationWorld(Package(package), new ModelRegistry(assembly));
                Assert((int)type.GetProperty("ConstructorStarCost")!.GetValue(Model(world, type))! == -2);
            }
            foreach (var (source, reason) in new[] {
                (Source.Replace("=> 5;", "=> 2 + 3;"), "star_cost_not_direct_int32_getter_literal"),
                (Source.Replace("canonicalEnergyCost: 3", "canonicalEnergyCost: 1 + 2"), "requires_bound_direct_card_constructor_context")
            })
            {
                var stage = Stage(source); var scope = JsonNode.Parse(File.ReadAllBytes(Path.Combine(stage.Output, "parameter-scope.json")))!;
                var gap = scope["canonical_star_cost_sites"]!.AsArray().Single()!;
                Assert(gap["status"]!.GetValue<string>() == "unbound" && gap["reason"]!.GetValue<string>() == reason);
                Assert(File.ReadAllText(Path.Combine(stage.Output, "Code", "Card.cs")).Contains(source.Contains("=> 2 + 3;") ? "=> 2 + 3;" : "=> 5;"));
            }
        });
    }
}
