using System.Globalization;
using System.Text;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace AutoSlay.RuleSourceSync;

/// <summary>Offline numeric binding only. Never translates behavior into an effect program.</summary>
public static class ParameterExtraction
{
    public static ParameterRewrite Rewrite(CompilationUnitSyntax unit, string relative, string sourceSha256)
    {
        var replacements = new Dictionary<ExpressionSyntax, ExpressionSyntax>();
        var constructors = new Dictionary<ConstructorDeclarationSyntax, string>();
        var constructorSites = new List<ConstructorCostSite>();
        var starCostSites = new List<ConstructorCostSite>();
        var entries = new List<ParameterEntry>(); var unbound = new List<UnboundNumeric>();
        foreach (var type in unit.DescendantNodes().OfType<ClassDeclarationSyntax>())
        {
            var ns = string.Join('.', type.Ancestors().OfType<BaseNamespaceDeclarationSyntax>().Reverse().Select(n => n.Name.ToString()));
            if (!ns.StartsWith("MegaCrit.Sts2.Core.Models.", StringComparison.Ordinal)) continue;
            BindCardConstructor(type, relative, sourceSha256, replacements, constructors, entries, constructorSites);
            BindCanonicalStarCost(type, relative, sourceSha256, replacements, constructors, entries, starCostSites);
            foreach (var member in type.Members.Where(m => m is PropertyDeclarationSyntax { Identifier.ValueText: "CanonicalVars" }
                or MethodDeclarationSyntax { Identifier.ValueText: "OnUpgrade" }))
            {
                var name = member is PropertyDeclarationSyntax p ? p.Identifier.ValueText : ((MethodDeclarationSyntax)member).Identifier.ValueText;
                if (member.Modifiers.Any(SyntaxKind.StaticKeyword)) throw new InvalidOperationException("Static parameter context: " + relative + "::" + name);
                var ordinal = 0;
                foreach (var argument in member.DescendantNodes().OfType<ArgumentSyntax>())
                {
                    if (!IsParameterArgument(argument, name)) continue;
                    // Keep arbitrary arithmetic, delegates, model references and flags as original C#.
                    var expression = argument.Expression;
                    var number = expression is PrefixUnaryExpressionSyntax signed
                        && signed.Kind() is SyntaxKind.UnaryMinusExpression or SyntaxKind.UnaryPlusExpression ? signed.Operand : expression;
                    if (number is not LiteralExpressionSyntax literal || !literal.IsKind(SyntaxKind.NumericLiteralExpression)) continue;
                    var kind = literal.Token.Value is decimal ? "decimal" : TryInt32Literal(expression, out _) ? "int32" : null;
                    if (kind == null) throw new InvalidOperationException("Unsupported numeric parameter type at " + relative + ":" + Line(literal));
                    var value = Convert.ToDecimal(literal.Token.Value, CultureInfo.InvariantCulture);
                    if (expression.IsKind(SyntaxKind.UnaryMinusExpression)) value = -value;
                    var owners = string.Join('.', type.AncestorsAndSelf().OfType<ClassDeclarationSyntax>().Reverse().Select(c => c.Identifier.ValueText));
                    var key = relative + "::" + owners + "." + name + "::" + ordinal++;
                    entries.Add(new(key, kind, value, relative, sourceSha256, Line(expression)));
                    var method = kind == "decimal" ? "Decimal" : "Int32";
                    var access = SyntaxFactory.InvocationExpression(SyntaxFactory.ParseExpression("Parameters." + method),
                        SyntaxFactory.ArgumentList(SyntaxFactory.SingletonSeparatedList(SyntaxFactory.Argument(
                            SyntaxFactory.LiteralExpression(SyntaxKind.StringLiteralExpression, SyntaxFactory.Literal(key))))));
                    replacements.Add(expression, access.WithTriviaFrom(expression));
                }
                foreach (var literal in member.DescendantNodes().OfType<LiteralExpressionSyntax>().Where(n => n.IsKind(SyntaxKind.NumericLiteralExpression)))
                    if (!replacements.Keys.Any(e => e == literal || literal.Ancestors().Contains(e)))
                        unbound.Add(new(relative, Line(literal), name, literal.Token.Text, "not_a_direct_scalar_var_or_upgrade_argument"));
            }
        }
        var nodes = replacements.Keys.Cast<SyntaxNode>().Concat(constructors.Keys);
        var rewritten = unit.ReplaceNodes(nodes, (original, updated) => {
            if (original is not ConstructorDeclarationSyntax constructor) return replacements[(ExpressionSyntax)original];
            var parameterName = constructors[constructor];
            var result = ((ConstructorDeclarationSyntax)updated).AddParameterListParameters(
                SyntaxFactory.Parameter(SyntaxFactory.Identifier(parameterName))
                    .WithType(SyntaxFactory.ParseTypeName("global::AutoSlay.Rules.Parameters.RuleParameters").WithTrailingTrivia(SyntaxFactory.Space)));
            return result.WithInitializer(result.Initializer!.AddArgumentListArguments(
                SyntaxFactory.Argument(SyntaxFactory.IdentifierName(parameterName))
                    .WithNameColon(SyntaxFactory.NameColon("ruleParameters"))));
        });
        return new(rewritten, entries, unbound, constructorSites, starCostSites);
    }

    private static void BindCanonicalStarCost(ClassDeclarationSyntax type, string relative, string sourceSha256,
        Dictionary<ExpressionSyntax, ExpressionSyntax> replacements, Dictionary<ConstructorDeclarationSyntax, string> constructors,
        List<ParameterEntry> entries, List<ConstructorCostSite> sites)
    {
        foreach (var property in type.Members.OfType<PropertyDeclarationSyntax>()
            .Where(p => p.Identifier.ValueText == "CanonicalStarCost" && p.Modifiers.Any(SyntaxKind.OverrideKeyword)))
        {
            var member = type.Identifier.ValueText + ".CanonicalStarCost";
            void Gap(string reason) => sites.Add(new(relative, Line(property), member, "unbound", reason, null));
            if (!type.Members.OfType<ConstructorDeclarationSyntax>().Any(constructors.ContainsKey))
            { Gap("requires_bound_direct_card_constructor_context"); continue; }
            var getter = property.AccessorList?.Accessors.SingleOrDefault();
            var expression = property.ExpressionBody?.Expression
                ?? (getter?.IsKind(SyntaxKind.GetAccessorDeclaration) == true
                    ? getter.ExpressionBody?.Expression ?? (getter.Body?.Statements is { Count: 1 } statements
                        && statements[0] is ReturnStatementSyntax returned ? returned.Expression : null) : null);
            if (property.Type.ToString() != "int" || expression == null || !TryInt32Literal(expression, out var value))
            { Gap("star_cost_not_direct_int32_getter_literal"); continue; }
            var owners = string.Join('.', type.AncestorsAndSelf().OfType<ClassDeclarationSyntax>().Reverse().Select(c => c.Identifier.ValueText));
            var key = relative + "::" + owners + ".CanonicalStarCost::0";
            entries.Add(new(key, "int32", value, relative, sourceSha256, Line(expression)));
            var read = SyntaxFactory.InvocationExpression(SyntaxFactory.ParseExpression("Parameters.Int32"),
                SyntaxFactory.ArgumentList(SyntaxFactory.SingletonSeparatedList(SyntaxFactory.Argument(
                    SyntaxFactory.LiteralExpression(SyntaxKind.StringLiteralExpression, SyntaxFactory.Literal(key))))));
            replacements.Add(expression, read.WithTriviaFrom(expression));
            sites.Add(new(relative, Line(expression), member, "bound", "explicit_immutable_constructor_parameter_context", key));
        }
    }

    private static void BindCardConstructor(ClassDeclarationSyntax type, string relative, string sourceSha256,
        Dictionary<ExpressionSyntax, ExpressionSyntax> replacements, Dictionary<ConstructorDeclarationSyntax, string> constructors,
        List<ParameterEntry> entries, List<ConstructorCostSite> sites)
    {
        if (type.BaseList?.Types.Any(b => b.Type.ToString() is "CardModel" or "MegaCrit.Sts2.Core.Models.CardModel"
            or "global::MegaCrit.Sts2.Core.Models.CardModel") != true) return;
        var instanceConstructors = type.Members.OfType<ConstructorDeclarationSyntax>()
            .Where(c => !c.Modifiers.Any(SyntaxKind.StaticKeyword)).ToArray();
        var member = type.Identifier.ValueText + ".ctor.CanonicalEnergyCost";
        void Gap(SyntaxNode site, string reason) => sites.Add(new(relative, Line(site), member, "unbound", reason, null));
        // Constructor chaining/partial declarations need a graph-wide rewrite. Do not guess which
        // constructor owns the cost, add ambient world state, or introduce an entity-specific adapter.
        if (type.Modifiers.Any(SyntaxKind.PartialKeyword)) { Gap(type, "partial_type_requires_constructor_graph"); return; }
        if (instanceConstructors.Length != 1) { Gap(type, "requires_single_instance_constructor"); return; }
        var ctor = instanceConstructors[0];
        if (!ctor.Modifiers.Any(SyntaxKind.PublicKeyword) || ctor.ParameterList.Parameters.Count != 0)
        { Gap(ctor, "requires_public_parameterless_source_constructor"); return; }
        if (ctor.Initializer is not { } initializer || !initializer.IsKind(SyntaxKind.BaseConstructorInitializer))
        { Gap(ctor, "requires_direct_base_initializer"); return; }
        var arguments = initializer.ArgumentList.Arguments;
        var argument = arguments.FirstOrDefault(a => a.NameColon?.Name.Identifier.ValueText == "canonicalEnergyCost")
            ?? (arguments.Count > 0 && arguments[0].NameColon == null ? arguments[0] : null);
        if (argument == null || !TryInt32Literal(argument.Expression, out var value))
        { Gap(initializer, "cost_not_direct_int32_literal"); return; }
        var parameterName = "__ruleParameters";
        var identifiers = type.DescendantTokens().Where(t => t.IsKind(SyntaxKind.IdentifierToken)).Select(t => t.ValueText).ToHashSet(StringComparer.Ordinal);
        for (var suffix = 1; identifiers.Contains(parameterName); suffix++) parameterName = "__ruleParameters" + suffix.ToString(CultureInfo.InvariantCulture);
        var owners = string.Join('.', type.AncestorsAndSelf().OfType<ClassDeclarationSyntax>().Reverse().Select(c => c.Identifier.ValueText));
        var key = relative + "::" + owners + ".ctor.CanonicalEnergyCost::0";
        entries.Add(new(key, "int32", value, relative, sourceSha256, Line(argument.Expression)));
        var read = SyntaxFactory.InvocationExpression(SyntaxFactory.ParseExpression(parameterName + ".Int32"),
            SyntaxFactory.ArgumentList(SyntaxFactory.SingletonSeparatedList(SyntaxFactory.Argument(
                SyntaxFactory.LiteralExpression(SyntaxKind.StringLiteralExpression, SyntaxFactory.Literal(key))))));
        replacements.Add(argument.Expression, read.WithTriviaFrom(argument.Expression));
        constructors.Add(ctor, parameterName);
        sites.Add(new(relative, Line(argument.Expression), member, "bound", "explicit_factory_parameter_before_base_constructor", key));
    }

    private static bool TryInt32Literal(ExpressionSyntax expression, out int result)
    {
        result = 0;
        var number = expression is PrefixUnaryExpressionSyntax signed
            && signed.Kind() is SyntaxKind.UnaryMinusExpression or SyntaxKind.UnaryPlusExpression ? signed.Operand : expression;
        if (number is not LiteralExpressionSyntax literal || !literal.IsKind(SyntaxKind.NumericLiteralExpression)) return false;
        // Roslyn stores the operand of the C# decimal-literal int.MinValue special case as uint.
        // Other uint/suffixed literals are not int constructor arguments; do not silently repair them.
        var minValueOperand = literal.Token.Value is uint wide && wide == 2147483648U
            && expression.IsKind(SyntaxKind.UnaryMinusExpression) && literal.Token.Text.Replace("_", "").All(char.IsDigit);
        if (literal.Token.Value is not int && !minValueOperand) return false;
        var value = Convert.ToDecimal(literal.Token.Value, CultureInfo.InvariantCulture);
        if (expression.IsKind(SyntaxKind.UnaryMinusExpression)) value = -value;
        if (value < int.MinValue || value > int.MaxValue) return false;
        result = (int)value; return true;
    }

    private static bool IsParameterArgument(ArgumentSyntax argument, string member) => argument.Parent?.Parent switch
    {
        ObjectCreationExpressionSyntax creation when member == "CanonicalVars" => creation.Type switch
        {
            IdentifierNameSyntax id => id.Identifier.ValueText.EndsWith("Var", StringComparison.Ordinal),
            GenericNameSyntax generic => generic.Identifier.ValueText.EndsWith("Var", StringComparison.Ordinal),
            QualifiedNameSyntax qualified => qualified.Right.Identifier.ValueText.EndsWith("Var", StringComparison.Ordinal),
            _ => false
        },
        InvocationExpressionSyntax { Expression: MemberAccessExpressionSyntax access } when member == "OnUpgrade"
            => access.Name.Identifier.ValueText == "UpgradeValueBy"
                || access.Name.Identifier.ValueText == "UpgradeStarCostBy"
                || access.Name.Identifier.ValueText == "UpgradeBy" && access.Expression is MemberAccessExpressionSyntax { Name.Identifier.ValueText: "EnergyCost" }
                || access.Name.Identifier.ValueText == "UpgradeBy" && access.Expression is IdentifierNameSyntax { Identifier.ValueText: "EnergyCost" },
        InvocationExpressionSyntax { Expression: IdentifierNameSyntax { Identifier.ValueText: "UpgradeStarCostBy" } } when member == "OnUpgrade" => true,
        _ => false
    };
    private static int Line(SyntaxNode node) => node.GetLocation().GetLineSpan().StartLinePosition.Line + 1;
    public static string ContractHash(IEnumerable<ParameterEntry> entries) => SourceInventory.Hash(Encoding.UTF8.GetBytes(
        string.Join('\n', entries.OrderBy(e => e.Key, StringComparer.Ordinal).Select(e => e.Key + ":" + e.Kind))));
}

public sealed record ParameterEntry(string Key, string Kind, decimal Value, string SourcePath, string SourceSha256, int Line);
public sealed record UnboundNumeric(string SourcePath, int Line, string Member, string Literal, string Reason);
public sealed record ConstructorCostSite(string SourcePath, int Line, string Member, string Status, string Reason, string? Key);
public sealed record ParameterRewrite(CompilationUnitSyntax Unit, IReadOnlyList<ParameterEntry> Entries, IReadOnlyList<UnboundNumeric> Unbound,
    IReadOnlyList<ConstructorCostSite> ConstructorCosts, IReadOnlyList<ConstructorCostSite> CanonicalStarCosts);
