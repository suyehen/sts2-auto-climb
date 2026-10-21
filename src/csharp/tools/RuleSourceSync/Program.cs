using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace AutoSlay.RuleSourceSync;

public static class Program
{
    public static int Main(string[] args)
    {
        try
        {
            if (args.Length == 4 && args[0] == "roster")
            {
                var roster = RosterInventory.Build(args[1], args[2]); var rosterOutput = Path.GetFullPath(args[3]);
                Directory.CreateDirectory(Path.GetDirectoryName(rosterOutput)!); File.WriteAllText(rosterOutput, JsonSerializer.Serialize(roster, SourceInventory.Json) + "\n");
                Console.WriteLine(JsonSerializer.Serialize(new { roster.DeclaredCount, roster.PeInheritanceCandidates, roster.StaticSetVerified,
                    roster.RuntimeModelRosterVerified, roster.SemanticSupportCertified, roster.GameRulesExecuted }, SourceInventory.Json));
                return roster.StaticSetVerified ? 0 : 2;
            }
            if (args.Length == 4 && args[0] == "units")
            {
                var files = JsonSerializer.Deserialize<string[]>(File.ReadAllText(args[2])) ?? throw new ArgumentException("Source file list required");
                Console.WriteLine(JsonSerializer.Serialize(SourceStage.Write(args[1], args[3], true, files), SourceInventory.Json)); return 0;
            }
            if (args.Length != 3 || args[0] is not ("metadata" or "inventory" or "dependencies" or "stage" or "parameters" or "compare-parameters"))
                throw new ArgumentException("Usage: RuleSourceSync metadata ASSEMBLY OUTPUT.json | roster ASSEMBLY SOURCE_TABLE OUTPUT.json | inventory|dependencies SOURCE_ROOT OUTPUT.json | stage|parameters SOURCE_ROOT OUTPUT_DIRECTORY | compare-parameters OLD_PACKAGE NEW_PACKAGE | units SOURCE_ROOT SOURCE_FILES.json OUTPUT_DIRECTORY");
            if (args[0] == "metadata")
            {
                var metadataReport = PeInventory.Build(args[1]); var metadataOutput = Path.GetFullPath(args[2]);
                Directory.CreateDirectory(Path.GetDirectoryName(metadataOutput)!); File.WriteAllText(metadataOutput, JsonSerializer.Serialize(metadataReport, SourceInventory.Json) + "\n");
                Console.WriteLine(JsonSerializer.Serialize(new { metadataReport.SchemaVersion, metadataReport.InputAssemblySha256, types = metadataReport.Types.Count,
                    model_candidates = metadataReport.ModelCandidates.Count, metadataReport.Categories, metadataReport.RuntimeModelRosterVerified, metadataReport.GameRulesExecuted }, SourceInventory.Json)); return 0;
            }
            if (args[0] == "compare-parameters") { Console.WriteLine(JsonSerializer.Serialize(ParameterDifference.Compare(args[1], args[2]), SourceInventory.Json)); return 0; }
            if (args[0] is "stage" or "parameters") { Console.WriteLine(JsonSerializer.Serialize(SourceStage.Write(args[1], args[2], args[0] == "parameters"), SourceInventory.Json)); return 0; }
            if (args[0] == "dependencies")
            {
                var graph = DependencyInventory.Build(args[1]);
                var destination = Path.GetFullPath(args[2]); Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
                File.WriteAllText(destination, JsonSerializer.Serialize(graph, SourceInventory.Json) + "\n");
                Console.WriteLine(JsonSerializer.Serialize(new { graph.SchemaVersion, graph.SourceFingerprint,
                    types = graph.Types.Count, graph.UnresolvedCalls, graph.CompilationErrors, dependency_closure_complete = graph.CompilationErrors == 0 && graph.UnresolvedCalls == 0 }, SourceInventory.Json));
                return 0; // A report of unresolved native dependencies is valid inventory, not migration success.
            }
            var report = SourceInventory.Build(args[1]);
            var output = Path.GetFullPath(args[2]);
            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            File.WriteAllText(output, JsonSerializer.Serialize(report, SourceInventory.Json) + "\n");
            Console.WriteLine(JsonSerializer.Serialize(new { report.SchemaVersion, report.Files, types = report.Types.Count,
                report.Fingerprint, report.ParseErrors, semantic_completeness_certified = false }, SourceInventory.Json));
            return report.ParseErrors == 0 ? 0 : 2;
        }
        catch (Exception error) { Console.Error.WriteLine(error.Message); return 1; }
    }
}

public static class SourceInventory
{
    public static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower, WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    };
    public static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();

    public static Inventory Build(string directory)
    {
        var root = Path.GetFullPath(directory);
        if (!Directory.Exists(root)) throw new DirectoryNotFoundException(root);
        var types = new List<TypeEvidence>(); var files = new List<FileEvidence>();
        foreach (var path in Directory.EnumerateFiles(root, "*.cs", SearchOption.AllDirectories).Order(StringComparer.Ordinal))
        {
            var relative = Path.GetRelativePath(root, path).Replace('\\', '/');
            var bytes = File.ReadAllBytes(path); var source = System.Text.Encoding.UTF8.GetString(bytes);
            var tree = CSharpSyntaxTree.ParseText(source, new CSharpParseOptions(LanguageVersion.Preview), relative);
            var unit = tree.GetCompilationUnitRoot();
            var diagnostics = tree.GetDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error)
                .Select(d => d.ToString()).ToArray();
            files.Add(new(relative, Hash(bytes), diagnostics));
            foreach (var declaration in unit.DescendantNodes().Where(n => n is BaseTypeDeclarationSyntax or DelegateDeclarationSyntax))
            {
                var ancestors = declaration.Ancestors().Reverse().ToArray();
                var ns = string.Join('.', ancestors.OfType<BaseNamespaceDeclarationSyntax>().Select(n => n.Name.ToString()));
                var owners = ancestors.OfType<BaseTypeDeclarationSyntax>().Select(n => n.Identifier.ValueText);
                var identifier = declaration is BaseTypeDeclarationSyntax baseDeclaration ? baseDeclaration.Identifier : ((DelegateDeclarationSyntax)declaration).Identifier;
                var modifiers = declaration is BaseTypeDeclarationSyntax baseModifiers ? baseModifiers.Modifiers : ((DelegateDeclarationSyntax)declaration).Modifiers;
                var bases = declaration is BaseTypeDeclarationSyntax { BaseList: { } baseList } ? baseList.Types.Select(t => t.Type.ToString()).ToArray() : [];
                var full = string.Join('.', new[] { ns }.Concat(owners).Append(identifier.ValueText).Where(s => s.Length > 0));
                var members = declaration is TypeDeclarationSyntax typed ? typed.Members : default;
                var methods = members.OfType<BaseMethodDeclarationSyntax>().Select(method => new MethodEvidence(
                    method switch { MethodDeclarationSyntax m => m.Identifier.ValueText,
                        ConstructorDeclarationSyntax c => c.Identifier.ValueText,
                        _ => method.Kind().ToString() },
                    method switch { MethodDeclarationSyntax m => m.ReturnType.ToString(), _ => null },
                    method.ParameterList.ToString(), method.Modifiers.Select(t => t.Text).ToArray(),
                    method.GetLocation().GetLineSpan().StartLinePosition.Line + 1,
                    Hash(System.Text.Encoding.UTF8.GetBytes(method.ToFullString())),
                    method.DescendantNodes().OfType<InvocationExpressionSyntax>().Select(call => call.Expression.ToString())
                        .Distinct().Order(StringComparer.Ordinal).ToArray())).ToArray();
                if (declaration is DelegateDeclarationSyntax callback)
                    methods = [new(callback.Identifier.ValueText, callback.ReturnType.ToString(), callback.ParameterList.ToString(),
                        callback.Modifiers.Select(t => t.Text).ToArray(), callback.GetLocation().GetLineSpan().StartLinePosition.Line + 1,
                        Hash(System.Text.Encoding.UTF8.GetBytes(callback.ToFullString())), [])];
                var fields = members.OfType<FieldDeclarationSyntax>().SelectMany(field => field.Declaration.Variables
                    .Select(variable => new FieldEvidence(variable.Identifier.ValueText, field.Declaration.Type.ToString(),
                        field.Modifiers.Select(t => t.Text).ToArray()))).ToArray();
                var properties = members.OfType<PropertyDeclarationSyntax>().Select(property => new PropertyEvidence(
                    property.Identifier.ValueText, property.Type.ToString(), property.Modifiers.Select(t => t.Text).ToArray(),
                    property.GetLocation().GetLineSpan().StartLinePosition.Line + 1, Hash(System.Text.Encoding.UTF8.GetBytes(property.ToFullString())),
                    property.AccessorList?.Accessors.Select(a => a.Keyword.ValueText).ToArray() ?? [],
                    property.DescendantNodes().OfType<InvocationExpressionSyntax>().Select(call => call.Expression.ToString()).Distinct().Order(StringComparer.Ordinal).ToArray())).ToArray();
                var events = members.OfType<EventFieldDeclarationSyntax>().SelectMany(field => field.Declaration.Variables
                    .Select(variable => new FieldEvidence(variable.Identifier.ValueText, field.Declaration.Type.ToString(), field.Modifiers.Select(t => t.Text).ToArray())))
                    .Concat(members.OfType<EventDeclarationSyntax>().Select(e => new FieldEvidence(e.Identifier.ValueText, e.Type.ToString(), e.Modifiers.Select(t => t.Text).ToArray()))).ToArray();
                var references = declaration.DescendantNodes().OfType<GenericNameSyntax>()
                    .SelectMany(g => g.TypeArgumentList.Arguments.Select(t => t.ToString()))
                    .Concat(bases)
                    .Distinct().Order(StringComparer.Ordinal).ToArray();
                types.Add(new(full, Category(ns), relative, Hash(bytes), declaration.Kind().ToString(),
                    modifiers.Select(t => t.Text).ToArray(),
                    bases, fields, properties, events, methods,
                    references, unit.Usings.Select(u => u.Name?.ToString() ?? "").ToArray(),
                    ns.Contains(".Mocks", StringComparison.Ordinal) ? "source_mock_namespace" : "unclassified",
                    "not_migrated", false));
            }
        }
        var fingerprint = Hash(System.Text.Encoding.UTF8.GetBytes(string.Join('\n', files.Select(f => f.Path + ":" + f.Sha256))));
        return new("independent-rule-source-inventory/3", "syntax_inventory_only", fingerprint, files.Count,
            files.Sum(f => f.ParseErrors.Length), files, types.OrderBy(t => t.FullName, StringComparer.Ordinal).ToArray(),
            ["Syntax references are not a resolved call graph; semantic binding remains required.",
             "Neither source presence nor parse success establishes execution support.",
             "Nested, mock and deprecated types remain in the inventory; no reachability inferred."]);
    }

    private static string Category(string ns)
    {
        if (ns.StartsWith("MegaCrit.Sts2.Core.Models.", StringComparison.Ordinal))
            return "model/" + ns["MegaCrit.Sts2.Core.Models.".Length..].Replace('.', '/');
        if (ns == "MegaCrit.Sts2.Core.Models") return "model_base";
        if (ns.StartsWith("MegaCrit.Sts2.Core.Commands", StringComparison.Ordinal)) return "commands";
        if (ns.StartsWith("MegaCrit.Sts2.Core.Hooks", StringComparison.Ordinal)) return "hooks";
        return "dependency/" + ns;
    }
}

public sealed record Inventory(string SchemaVersion, string Scope, string Fingerprint, int Files, int ParseErrors,
    IReadOnlyList<FileEvidence> SourceFiles, IReadOnlyList<TypeEvidence> Types, string[] Limitations);
public sealed record FileEvidence(string Path, string Sha256, string[] ParseErrors);
public sealed record TypeEvidence(string FullName, string Category, string SourcePath, string SourceSha256,
    string Kind, string[] Modifiers, string[] Bases, FieldEvidence[] Fields, PropertyEvidence[] Properties, FieldEvidence[] Events, MethodEvidence[] Methods,
    string[] SyntacticTypeReferences, string[] Usings, string Classification, string MigrationStatus, bool InteractionVerified);
public sealed record MethodEvidence(string Name, string? ReturnType, string Parameters, string[] Modifiers,
    int Line, string SourceSha256, string[] SyntacticCalls);
public sealed record FieldEvidence(string Name, string Type, string[] Modifiers);
public sealed record PropertyEvidence(string Name, string Type, string[] Modifiers, int Line, string SourceSha256, string[] Accessors, string[] SyntacticCalls);

public static class SourceStage
{
    public static object Write(string sourceRoot, string outputDirectory, bool parameterize = false, IReadOnlyCollection<string>? selectedFiles = null)
    {
        var root = Path.TrimEndingDirectorySeparator(Path.GetFullPath(sourceRoot)); var output = Path.TrimEndingDirectorySeparator(Path.GetFullPath(outputDirectory));
        if (string.Equals(output, root, StringComparison.OrdinalIgnoreCase) || output.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
            || root.StartsWith(output + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
            throw new ArgumentException("Source and generated directories must be disjoint");
        var pending = new List<(string Relative, string Text, string SourceHash, string GeneratedHash)>();
        var parameters = new List<ParameterEntry>(); var unbound = new List<UnboundNumeric>();
        var constructorCosts = new List<ConstructorCostSite>();
        var canonicalStarCosts = new List<ConstructorCostSite>();
        if (selectedFiles?.Count == 0) throw new ArgumentException("Empty compilation unit selection");
        var paths = selectedFiles == null ? Directory.EnumerateFiles(root, "*.cs", SearchOption.AllDirectories)
            : selectedFiles.Select(relative => {
                if (Path.IsPathRooted(relative) || relative.Contains('$') || relative.Contains('%') || relative.Contains('\\')) throw new ArgumentException("Invalid source path: " + relative);
                var full = Path.GetFullPath(Path.Combine(root, relative));
                if (!full.StartsWith(root + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) || !full.EndsWith(".cs", StringComparison.Ordinal))
                    throw new ArgumentException("Source path escapes input or is not C#: " + relative);
                return full;
            }).Distinct(StringComparer.OrdinalIgnoreCase);
        foreach (var path in paths.Order(StringComparer.Ordinal))
        {
            var relative = Path.GetRelativePath(root, path).Replace('\\', '/');
            var bytes = File.ReadAllBytes(path);
            var tree = CSharpSyntaxTree.ParseText(System.Text.Encoding.UTF8.GetString(bytes));
            if (tree.GetDiagnostics().Any(d => d.Severity == DiagnosticSeverity.Error))
                throw new InvalidOperationException("Unparseable source: " + relative);
            var unit = tree.GetCompilationUnitRoot();
            if (parameterize)
            {
                var binding = ParameterExtraction.Rewrite(unit, relative, SourceInventory.Hash(bytes));
                unit = binding.Unit; parameters.AddRange(binding.Entries); unbound.AddRange(binding.Unbound);
                constructorCosts.AddRange(binding.ConstructorCosts);
                canonicalStarCosts.AddRange(binding.CanonicalStarCosts);
            }
            var rewritten = new NamespaceMigration().Visit(unit)!;
            if (selectedFiles != null) rewritten = new CanonicalCollectionMigration().Visit(rewritten)!;
            var text = (selectedFiles != null ? "// Generated migration unit; build and behavior verification are separate.\n" : "// Generated namespace migration only; engine dependencies and semantic migration remain unresolved.\n") + rewritten.ToFullString();
            pending.Add((selectedFiles == null ? relative : "Code/" + relative, text, SourceInventory.Hash(bytes), SourceInventory.Hash(System.Text.Encoding.UTF8.GetBytes(text))));
        }
        if (parameterize)
        {
            var sourceFingerprint = SourceInventory.Hash(System.Text.Encoding.UTF8.GetBytes(string.Join('\n', pending.Select(p =>
                (selectedFiles == null ? p.Relative : p.Relative["Code/".Length..]) + ":" + p.SourceHash))));
            var behaviorSha256 = SourceInventory.Hash(System.Text.Encoding.UTF8.GetBytes(string.Join('\n', pending.Select(p => p.Relative + ":" + p.GeneratedHash))));
            var package = new { schema_version = "autoslay-rule-parameters/1", source_fingerprint = sourceFingerprint,
                behavior_sha256 = behaviorSha256, contract_sha256 = ParameterExtraction.ContractHash(parameters), entries = parameters };
            if (selectedFiles != null)
            {
                var identity = "// Generated compiled numeric binding identity.\nnamespace AutoSlay.Rules.Generated;\ninternal static class CompiledUnitIdentity {\n"
                    + "internal const string BehaviorSha256 = \"" + behaviorSha256 + "\";\n"
                    + "internal const string ContractSha256 = \"" + package.contract_sha256 + "\";\n}\n";
                AddArtifact("CompiledUnitIdentity.cs", identity, sourceFingerprint);
                var compile = pending.Where(p => p.Relative.EndsWith(".cs", StringComparison.Ordinal)).Select(p =>
                    new System.Xml.Linq.XElement("Compile", new System.Xml.Linq.XAttribute("Include", "$(MSBuildThisFileDirectory)" + p.Relative)));
                var props = new System.Xml.Linq.XElement("Project", new System.Xml.Linq.XElement("ItemGroup", compile,
                    new System.Xml.Linq.XElement("EmbeddedResource", new System.Xml.Linq.XAttribute("Include", "$(MSBuildThisFileDirectory)rule-parameters.json"),
                        new System.Xml.Linq.XElement("LogicalName", "AutoSlay.Rules.Parameters.Default"))));
                AddArtifact("RuleUnits.props", props.ToString(), sourceFingerprint);
            }
            AddArtifact("rule-parameters.json", JsonSerializer.Serialize(package, SourceInventory.Json), sourceFingerprint);
            AddArtifact("parameter-scope.json", JsonSerializer.Serialize(new {
                schema_version = "autoslay-parameter-extraction-scope/1", extracted_bindings = parameters.Count,
                scope = "direct_scalar_model_vars_and_upgrade_arguments_plus_direct_CardModel_constructor_energy_and_canonical_star_cost",
                constructor_cost_sites = constructorCosts,
                canonical_star_cost_sites = canonicalStarCosts,
                unbound_numeric_literals = unbound, full_parameter_coverage = false, full_support = false,
                limitations = new[] { "Other members, indirect constants, calculated expressions and non-numeric data are not externalized.",
                    "Card constructor binding requires a non-partial direct CardModel subclass with one public parameterless source constructor and a direct int32 base cost literal; constructor graphs remain unbound.",
                    "Generated card constructors receive RuleParameters explicitly from the owned model factory; direct zero-argument construction remains a compile error until its caller is migrated.",
                    "Direct int32 CanonicalStarCost override getters bind only when a direct card constructor context is bound; indirect and complex getters remain source code with explicit gaps.",
                    "Generated model source still requires semantic migration, dependency closure, compilation and behavior verification.",
                    "ValueProp flags and PowerVar generic model identities remain C# expressions, not numeric effect instructions." }
            }, SourceInventory.Json), sourceFingerprint);
        }

        void AddArtifact(string relative, string content, string provenance)
        {
            var text = content + "\n";
            pending.Add((relative, text, provenance, SourceInventory.Hash(System.Text.Encoding.UTF8.GetBytes(text))));
        }
        var manifestPath = Path.Combine(output, "source-stage-manifest.json");
        var owned = new Dictionary<string, string>(StringComparer.Ordinal);
        if (File.Exists(manifestPath))
        {
            using var old = JsonDocument.Parse(File.ReadAllText(manifestPath));
            foreach (var file in old.RootElement.GetProperty("files").EnumerateArray())
                owned.Add(file.GetProperty("path").GetString()!, file.GetProperty("generated_sha256").GetString()!);
            if (old.RootElement.TryGetProperty("retained_obsolete_files", out var retained))
                foreach (var file in retained.EnumerateArray())
                    owned.Add(file.GetProperty("path").GetString()!, file.GetProperty("generated_sha256").GetString()!);
        }
        // Preflight all collisions before writing anything. An edited generated file is never overwritten.
        foreach (var item in pending)
        {
            var destination = Path.GetFullPath(Path.Combine(output, item.Relative));
            if (!destination.StartsWith(output + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException("Generated path escapes output root");
            if (File.Exists(destination) && (!owned.TryGetValue(item.Relative, out var expected)
                || SourceInventory.Hash(File.ReadAllBytes(destination)) != expected))
                throw new InvalidOperationException("Generated file has local edits or is not owned: " + item.Relative);
        }
        Directory.CreateDirectory(output);
        foreach (var item in pending)
        {
            var destination = Path.Combine(output, item.Relative);
            Directory.CreateDirectory(Path.GetDirectoryName(destination)!);
            if (!File.Exists(destination) || SourceInventory.Hash(File.ReadAllBytes(destination)) != item.GeneratedHash)
                File.WriteAllText(destination, item.Text, new System.Text.UTF8Encoding(false));
        }
        var records = pending.Select(p => new { path = p.Relative, source_sha256 = p.SourceHash, generated_sha256 = p.GeneratedHash }).ToArray();
        var obsolete = owned.Keys.Except(pending.Select(p => p.Relative)).Order(StringComparer.Ordinal).ToArray();
        var manifest = new { schema_version = "independent-rule-source-stage/1", tool_version = "1.6.0",
            scope = selectedFiles != null ? "selected_compilation_units_pending_build_and_behavior_verification" : parameterize ? "namespace_and_partial_numeric_binding_not_compiled_or_verified" : "namespace_translation_only_not_compiled_or_verified", files = records,
            obsolete_files = obsolete,
            retained_obsolete_files = obsolete.Select(path => new { path, generated_sha256 = owned[path] }).ToArray(),
            transformations = parameterize
                ? new[] { "AST-qualified-name replacement MegaCrit.Sts2.Core -> AutoSlay.Rules.Native", "Direct numeric Var/UpgradeValueBy, EnergyCost.UpgradeBy and UpgradeStarCostBy arguments bound to immutable parameters; all control flow and calls retained",
                    "Eligible direct CardModel energy and canonical star-cost literals bound through explicit immutable RuleParameters constructor context before constructor body execution; constructor bodies and expressions retained",
                    selectedFiles != null ? "CanonicalVars DynamicVar singleton and CanonicalKeywords CardKeyword singleton/array compiler helpers replaced by equivalent immediate arrays" : "No collection helper transformations" }
                : new[] { "AST-qualified-name replacement MegaCrit.Sts2.Core -> AutoSlay.Rules.Native; no method, condition, helper or statement removed" },
            source_runtime_calls_allowed = false, full_support = false };
        File.WriteAllText(manifestPath, JsonSerializer.Serialize(manifest, SourceInventory.Json) + "\n");
        return new { generated_files = pending.Count, manifest = manifestPath, compiled = false, semantic_completeness_certified = false };
    }

    private sealed class NamespaceMigration : CSharpSyntaxRewriter
    {
        public override SyntaxNode? VisitQualifiedName(QualifiedNameSyntax node)
        {
            if (node.ToString() is "MegaCrit.Sts2.Core" or "global::MegaCrit.Sts2.Core")
                return SyntaxFactory.ParseName(node.ToString().StartsWith("global::", StringComparison.Ordinal)
                    ? "global::AutoSlay.Rules.Native" : "AutoSlay.Rules.Native").WithTriviaFrom(node);
            return base.VisitQualifiedName(node);
        }
    }
    private sealed class CanonicalCollectionMigration : CSharpSyntaxRewriter
    {
        public override SyntaxNode? VisitPropertyDeclaration(PropertyDeclarationSyntax node)
        {
            var element = (node.Identifier.ValueText, node.Type.ToString()) switch {
                ("CanonicalVars", "IEnumerable<DynamicVar>") => "DynamicVar",
                ("CanonicalKeywords", "IEnumerable<CardKeyword>") => "CardKeyword",
                _ => null
            };
            if (element != null && node.ExpressionBody?.Expression is ObjectCreationExpressionSyntax creation
                && creation.ArgumentList?.Arguments.Count == 1)
            {
                var item = creation.ArgumentList.Arguments[0].Expression.ToFullString();
                ExpressionSyntax? replacement = creation.Type.ToString() == "global::_003C_003Ez__ReadOnlySingleElementList<" + element + ">"
                    ? SyntaxFactory.ParseExpression("new " + element + "[] { " + item + " }")
                    : element == "CardKeyword" && creation.Type.ToString() == "global::_003C_003Ez__ReadOnlyArray<CardKeyword>"
                        && creation.ArgumentList.Arguments[0].Expression is ArrayCreationExpressionSyntax
                        ? creation.ArgumentList.Arguments[0].Expression : null;
                if (replacement != null) return node.WithExpressionBody(node.ExpressionBody.WithExpression(replacement.WithTriviaFrom(creation)));
            }
            return base.VisitPropertyDeclaration(node);
        }
    }
}
