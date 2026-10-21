using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace AutoSlay.RuleSourceSync;

/// <summary>Strict source table/PE inheritance set comparison. No ModelDb or input code is executed.</summary>
public static class RosterInventory
{
    public static RosterReport Build(string assemblyPath, string sourceTable)
    {
        var metadata = PeInventory.Build(assemblyPath); var bytes = File.ReadAllBytes(sourceTable);
        var tree = CSharpSyntaxTree.ParseText(System.Text.Encoding.UTF8.GetString(bytes));
        if (tree.GetDiagnostics().Any(d => d.Severity == DiagnosticSeverity.Error))
            throw new ArgumentException("Generated model table has parse errors");
        var unit = tree.GetCompilationUnitRoot();
        var table = unit.DescendantNodes().OfType<ClassDeclarationSyntax>().Single(c => c.Identifier.ValueText == "AbstractModelSubtypes");
        if (unit.DescendantNodes().OfType<BaseTypeDeclarationSyntax>().Count() != 1
            || unit.DescendantNodes().OfType<DelegateDeclarationSyntax>().Any()
            || unit.DescendantNodes().OfType<UsingDirectiveSyntax>().Any(u => !unit.Usings.Contains(u)))
            throw new ArgumentException("Generated model table must be a single class with compilation-unit imports");
        var ns = string.Join('.', table.Ancestors().OfType<BaseNamespaceDeclarationSyntax>().Reverse().Select(n => n.Name.ToString()));
        if (unit.Usings.Any(u => u.Alias != null || !u.StaticKeyword.IsKind(SyntaxKind.None)))
            throw new ArgumentException("Generated model table alias/static imports need an explicit source contract");
        var namespaces = unit.Usings.Select(u => u.Name!.ToString()).Append(ns).Distinct().ToArray();
        var knownTypes = metadata.Types.Select(t => t.FullName).ToHashSet(StringComparer.Ordinal);
        string Resolve(TypeSyntax syntax)
        {
            if (syntax is not (IdentifierNameSyntax or QualifiedNameSyntax or AliasQualifiedNameSyntax))
                throw new ArgumentException("Unsupported generated model type reference: " + syntax);
            var name = syntax.ToString();
            var candidates = name.StartsWith("global::", StringComparison.Ordinal)
                ? new[] { name[8..] }
                : namespaces.Select(n => n + "." + name).Prepend(name).ToArray();
            var matches = candidates.Where(knownTypes.Contains).Distinct().ToArray();
            return matches.Length == 1 ? matches[0] : throw new ArgumentException("Missing or ambiguous generated model type: " + name);
        }
        var declarations = new Dictionary<string, string>(StringComparer.Ordinal);
        ArrayCreationExpressionSyntax? array = null;
        foreach (var field in table.Members.OfType<FieldDeclarationSyntax>())
        {
            if (!field.Modifiers.Any(SyntaxKind.StaticKeyword) || !field.Modifiers.Any(SyntaxKind.ReadOnlyKeyword))
                throw new ArgumentException("Generated model fields must be static readonly");
            foreach (var variable in field.Declaration.Variables)
            {
                if (variable.Identifier.ValueText == "_subtypes")
                {
                    array = variable.Initializer?.Value as ArrayCreationExpressionSyntax
                        ?? throw new ArgumentException("Generated subtype array must have a direct array initializer");
                    continue;
                }
                if (variable.Initializer?.Value is not TypeOfExpressionSyntax reference)
                    throw new ArgumentException("Generated type field must contain a direct typeof");
                declarations.Add(variable.Identifier.ValueText, Resolve(reference.Type));
            }
        }
        var initializer = array?.Initializer ?? throw new ArgumentException("Missing generated subtype array");
        var slots = initializer.Expressions.Select(expression => expression is IdentifierNameSyntax name && declarations.TryGetValue(name.Identifier.ValueText, out var resolved)
            ? new RosterSlot(name.Identifier.ValueText, resolved)
            : throw new ArgumentException("Generated subtype array must reference a declared type field")).ToArray();
        var sizeExpression = array!.Type.RankSpecifiers.Single().Sizes.Single();
        var size = LiteralInt(sizeExpression);
        var countProperty = table.Members.OfType<PropertyDeclarationSyntax>().Single(p => p.Identifier.ValueText == "Count");
        var count = LiteralInt(countProperty.ExpressionBody?.Expression ?? throw new ArgumentException("Generated Count must be a literal"));
        var all = table.Members.OfType<PropertyDeclarationSyntax>().Single(p => p.Identifier.ValueText == "All");
        if (all.ExpressionBody?.Expression is not IdentifierNameSyntax { Identifier.ValueText: "_subtypes" })
            throw new ArgumentException("Generated All must return the subtype array directly");
        var duplicates = slots.GroupBy(s => s.FullName).Where(g => g.Count() > 1).Select(g => g.Key).Order(StringComparer.Ordinal).ToArray();
        var missingFields = declarations.Keys.Except(slots.Select(s => s.Field)).Order(StringComparer.Ordinal).ToArray();
        var sourceSet = slots.Select(s => s.FullName).ToHashSet(StringComparer.Ordinal);
        var peSet = metadata.ModelCandidates.Select(m => m.FullName).ToHashSet(StringComparer.Ordinal);
        var sourceOnly = sourceSet.Except(peSet).Order(StringComparer.Ordinal).ToArray();
        var peOnly = peSet.Except(sourceSet).Order(StringComparer.Ordinal).ToArray();
        return new("independent-rule-static-model-roster/1", metadata.InputAssemblySha256, SourceInventory.Hash(bytes),
            count, size, declarations.Count, slots, metadata.ModelCandidates.Count, duplicates, missingFields, sourceOnly, peOnly,
            count == size && count == slots.Length && declarations.Count == count && missingFields.Length == 0 && duplicates.Length == 0
                && sourceOnly.Length == 0 && peOnly.Length == 0,
            false, false, false,
            ["Only the direct source-generated table and concrete PE inheritance set are compared, without invoking source code or loading the input assembly.",
             "Static set agreement does not certify a live ModelDb roster: mods, Init(injectedModelTypes), Inject and Remove can change it.",
             "Mocks/deprecated models remain in the set; runtime reachability, availability and semantic support are not evaluated."]);
    }
    private static int LiteralInt(ExpressionSyntax expression) => expression is LiteralExpressionSyntax { Token.Value: int value } && value >= 0
        ? value : throw new ArgumentException("Nonnegative int literal required for generated roster size/count");
}
public sealed record RosterSlot(string Field, string FullName);
public sealed record RosterReport(string SchemaVersion, string InputAssemblySha256, string SourceTableSha256, int DeclaredCount,
    int ArraySize, int TypeFieldCount, RosterSlot[] Slots, int PeInheritanceCandidates, string[] DuplicateSourceTypes,
    string[] UnusedTypeFields, string[] SourceOnlyTypes, string[] PeOnlyTypes, bool StaticSetVerified,
    bool RuntimeModelRosterVerified, bool SemanticSupportCertified, bool GameRulesExecuted, string[] Limitations);
