using System.Text;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

namespace AutoSlay.RuleSourceSync;

/// <summary>Offline semantic analysis of source with framework references only; no game DLL loading.</summary>
public static class DependencyInventory
{
    public static DependencyGraph Build(string directory)
    {
        var root = Path.GetFullPath(directory);
        var inputs = Directory.EnumerateFiles(root, "*.cs", SearchOption.AllDirectories).Order(StringComparer.Ordinal)
            .Select(path => (Path: Path.GetRelativePath(root, path).Replace('\\', '/'), Bytes: File.ReadAllBytes(path))).ToArray();
        var trees = inputs.Select(i => CSharpSyntaxTree.ParseText(Encoding.UTF8.GetString(i.Bytes),
            new CSharpParseOptions(LanguageVersion.Preview), i.Path)).ToArray();
        var framework = ((string?)AppContext.GetData("TRUSTED_PLATFORM_ASSEMBLIES") ?? throw new InvalidOperationException("Framework reference inventory unavailable"))
            .Split(Path.PathSeparator)
            // Do not accidentally bind game symbols against the tool, tests or an engine loaded by a caller.
            .Where(path => Path.GetDirectoryName(path) == Path.GetDirectoryName(typeof(object).Assembly.Location))
            .Select(path => MetadataReference.CreateFromFile(path));
        var compilation = CSharpCompilation.Create("OfflineNativeDependencyInventory", trees, framework,
            new CSharpCompilationOptions(OutputKind.DynamicallyLinkedLibrary, allowUnsafe: true));
        var types = new List<DependencyType>();
        foreach (var tree in trees)
        {
            var semantic = compilation.GetSemanticModel(tree, ignoreAccessibility: true);
            foreach (var type in tree.GetRoot().DescendantNodes().Where(n => n is BaseTypeDeclarationSyntax or DelegateDeclarationSyntax))
            {
                var declared = semantic.GetDeclaredSymbol(type) as INamedTypeSymbol;
                if (declared == null) continue;
                var signature = declared.ToDisplayString();
                var dependencies = new HashSet<string>(StringComparer.Ordinal);
                var unresolvedTypes = new HashSet<string>(StringComparer.Ordinal);
                void AddType(ITypeSymbol? symbol)
                {
                    if (symbol == null) return;
                    if (symbol.TypeKind == TypeKind.Error) { unresolvedTypes.Add(symbol.ToDisplayString()); return; }
                    if (symbol is IArrayTypeSymbol array) { AddType(array.ElementType); return; }
                    if (symbol is IPointerTypeSymbol pointer) { AddType(pointer.PointedAtType); return; }
                    if (symbol is not INamedTypeSymbol named) return;
                    if (named.ToDisplayString() != signature) dependencies.Add(named.OriginalDefinition.ToDisplayString());
                    foreach (var argument in named.TypeArguments) if (argument is not ITypeParameterSymbol) AddType(argument);
                }
                AddType(declared.BaseType); foreach (var inherited in declared.Interfaces) AddType(inherited);
                foreach (var member in declared.GetMembers())
                {
                    switch (member)
                    {
                        case IPropertySymbol property: AddType(property.Type); break;
                        case IFieldSymbol field: AddType(field.Type); break;
                        case IEventSymbol evt: AddType(evt.Type); break;
                        case IMethodSymbol method:
                            AddType(method.ReturnType); foreach (var parameter in method.Parameters) AddType(parameter.Type); break;
                    }
                }
                var calls = new List<DependencyCall>();
                foreach (var node in type.DescendantNodes(n => n == type || n is not BaseTypeDeclarationSyntax)
                    .Where(n => n is InvocationExpressionSyntax or ObjectCreationExpressionSyntax or ImplicitObjectCreationExpressionSyntax))
                {
                    var info = semantic.GetSymbolInfo(node);
                    var target = info.Symbol;
                    if (target != null) AddType(target.ContainingType);
                    if (target is IMethodSymbol calledMethod)
                    {
                        AddType(calledMethod.ReturnType);
                        foreach (var typeArgument in calledMethod.TypeArguments) AddType(typeArgument);
                        foreach (var parameter in calledMethod.Parameters) AddType(parameter.Type);
                    }
                    var ownerNode = node.Ancestors().FirstOrDefault(n => n is BaseMethodDeclarationSyntax or PropertyDeclarationSyntax or EventDeclarationSyntax);
                    var owner = ownerNode == null ? signature : semantic.GetDeclaredSymbol(ownerNode)?.ToDisplayString() ?? signature;
                    calls.Add(new(owner, node.GetLocation().GetLineSpan().StartLinePosition.Line + 1, node.ToString(),
                        target?.ToDisplayString(), target?.Locations.Any(l => l.IsInSource) == true ? "source" : target == null ? "unresolved" : "framework",
                        target == null ? info.CandidateReason.ToString() : null,
                        info.CandidateSymbols.Select(s => s.ToDisplayString()).Order(StringComparer.Ordinal).ToArray()));
                }
                // Static property/field references (including ModelDb dependencies) are real type edges too.
                foreach (var node in type.DescendantNodes(n => n == type || n is not BaseTypeDeclarationSyntax).OfType<MemberAccessExpressionSyntax>())
                {
                    var symbol = semantic.GetSymbolInfo(node).Symbol;
                    if (symbol != null) AddType(symbol.ContainingType);
                    if (symbol is IPropertySymbol property) AddType(property.Type);
                    if (symbol is IFieldSymbol field) AddType(field.Type);
                }
                types.Add(new(signature, tree.FilePath, dependencies.Order(StringComparer.Ordinal).ToArray(),
                    unresolvedTypes.Order(StringComparer.Ordinal).ToArray(), calls));
            }
        }
        var diagnostics = compilation.GetDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error).ToArray();
        return new("independent-rule-dependencies/1", SourceInventory.Hash(Encoding.UTF8.GetBytes(string.Join('\n', inputs.Select(i => i.Path + ":" + SourceInventory.Hash(i.Bytes))))),
            "source_and_framework_only_no_native_assembly", types, types.Sum(t => t.Calls.Count(c => c.Scope == "unresolved")), diagnostics.Length,
            diagnostics.GroupBy(d => d.Id).OrderBy(g => g.Key, StringComparer.Ordinal).ToDictionary(g => g.Key, g => g.Count()),
            diagnostics.Select(d => d.ToString()).ToArray(),
            ["Binding diagnostics and unresolved calls are retained; resolved source edges alone do not establish dependency closure.",
             "This is an offline source analysis, not compilation support, runtime coverage or the definitive ModelDb denominator."]);
    }
}
public sealed record DependencyGraph(string SchemaVersion, string SourceFingerprint, string ReferenceScope,
    IReadOnlyList<DependencyType> Types, int UnresolvedCalls, int CompilationErrors, IReadOnlyDictionary<string, int> ErrorCounts,
    string[] Diagnostics, string[] Limitations);
public sealed record DependencyType(string FullName, string SourcePath, string[] Dependencies, string[] UnresolvedMemberTypes, IReadOnlyList<DependencyCall> Calls);
public sealed record DependencyCall(string Caller, int Line, string Expression, string? Target, string Scope, string? CandidateReason, string[] Candidates);
