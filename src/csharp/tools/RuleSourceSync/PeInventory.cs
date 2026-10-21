using System.Collections.Immutable;
using System.Reflection;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;
using System.Text.RegularExpressions;

namespace AutoSlay.RuleSourceSync;

/// <summary>Reads PE metadata/IL bytes only. Never loads the input as a CLR assembly or invokes its code.</summary>
public static class PeInventory
{
    private const string ModelRoot = "MegaCrit.Sts2.Core.Models.AbstractModel";
    public static PeReport Build(string path)
    {
        var bytes = File.ReadAllBytes(path);
        using var image = new MemoryStream(bytes, writable: false);
        using var pe = new PEReader(image);
        if (!pe.HasMetadata) throw new BadImageFormatException("Managed PE metadata required");
        var reader = pe.GetMetadataReader(); var names = new Names(reader); var baseNames = new Names(reader, true);
        var types = new List<PeType>();
        foreach (var handle in reader.TypeDefinitions)
        {
            var type = reader.GetTypeDefinition(handle); var fullName = names.Definition(handle);
            var methods = new List<PeMethod>();
            foreach (var methodHandle in type.GetMethods())
            {
                var method = reader.GetMethodDefinition(methodHandle);
                var il = method.RelativeVirtualAddress == 0 ? null : pe.GetMethodBody(method.RelativeVirtualAddress).GetILBytes();
                var signature = method.DecodeSignature(names, (object?)null);
                methods.Add(new(reader.GetString(method.Name), MetadataTokens.GetToken(methodHandle), method.Attributes.ToString(),
                    signature.ReturnType, signature.ParameterTypes.ToArray(), il == null ? null : SourceInventory.Hash(il),
                    ConstantBool(il)));
            }
            var fields = type.GetFields().Select(h => {
                var field = reader.GetFieldDefinition(h);
                return new PeField(reader.GetString(field.Name), MetadataTokens.GetToken(h), field.Attributes.ToString(), field.DecodeSignature(names, (object?)null));
            }).ToArray();
            var generated = type.GetCustomAttributes().Any(h => AttributeName(reader, names, reader.GetCustomAttribute(h).Constructor)
                == "System.Runtime.CompilerServices.CompilerGeneratedAttribute");
            types.Add(new(fullName, reader.GetString(type.Name), MetadataTokens.GetToken(handle), type.Attributes.ToString(),
                baseNames.Entity(type.BaseType), (type.Attributes & TypeAttributes.Abstract) != 0, type.GetGenericParameters().Count,
                generated, fields, methods.ToArray()));
        }
        var lookup = types.ToDictionary(t => t.FullName, StringComparer.Ordinal);
        PeType? Parent(PeType type) => type.BaseType == null ? null : lookup.GetValueOrDefault(type.BaseType);
        string? Category(PeType type)
        {
            var visited = new HashSet<string>(StringComparer.Ordinal);
            for (var current = type; current != null && visited.Add(current.FullName); current = Parent(current))
                if (current.BaseType == ModelRoot) return current.FullName;
            return null;
        }
        bool? Mock(PeType type)
        {
            var visited = new HashSet<string>(StringComparer.Ordinal);
            for (var current = type; current != null && visited.Add(current.FullName); current = Parent(current))
                if (current.Methods.FirstOrDefault(m => m.Name == "get_IsMock") is { } getter)
                {
                    var flags = reader.GetMethodDefinition((MethodDefinitionHandle)MetadataTokens.EntityHandle(getter.MetadataToken)).Attributes;
                    if (current.FullName == ModelRoot || (flags & MethodAttributes.Virtual) != 0 && (flags & MethodAttributes.NewSlot) == 0)
                        return getter.ReturnType == "Boolean" && getter.Parameters.Length == 0 ? getter.ConstantBooleanReturn : null;
                }
            return null;
        }
        var candidates = types.Where(t => !t.IsAbstract && t.GenericParameters == 0 && Category(t) != null)
            .Select(t => {
                var category = Category(t)!; var simple = lookup[category].Name;
                var slug = Slugify(simple); if (slug.EndsWith("_MODEL", StringComparison.Ordinal)) slug = slug[..^6];
                return new PeModelCandidate(t.FullName, slug + "." + Slugify(t.Name), category, Mock(t),
                    t.FullName.Contains(".Deprecated", StringComparison.Ordinal) || t.Name.Contains("Deprecated", StringComparison.Ordinal));
            }).OrderBy(t => t.ModelId, StringComparer.Ordinal).ThenBy(t => t.FullName, StringComparer.Ordinal).ToArray();
        var assembly = reader.GetAssemblyDefinition();
        return new("independent-rule-pe-inventory/1", SourceInventory.Hash(bytes), reader.GetString(assembly.Name), assembly.Version.ToString(),
            "PEReader_metadata_and_IL_bytes_only_no_input_assembly_execution", types.OrderBy(t => t.FullName, StringComparer.Ordinal).ToArray(),
            candidates, candidates.GroupBy(c => c.CategoryType).OrderBy(g => g.Key, StringComparer.Ordinal).ToDictionary(g => g.Key, g => g.Count()),
            candidates.GroupBy(c => c.ModelId).Where(g => g.Count() > 1).ToDictionary(g => g.Key, g => g.Select(c => c.FullName).ToArray()),
            false, false,
            ["Concrete inheritance candidates include mocks/deprecated types; no dynamic ModelDb registration, IsAllowed or model getter was executed.",
             "ConstantBooleanReturn is a narrow IL constant pattern, not evaluation of arbitrary getters.",
             "Raw compiler-generated metadata names and fields are retained; this inventory is not migrated source or rule support."]);
    }
    private static bool? ConstantBool(byte[]? il)
    {
        if (il == null) return null;
        var code = il.Where(b => b != 0).ToArray(); // Permit only nops around a literal bool and ret.
        return code.Length == 2 && code[1] == 0x2a ? code[0] switch { 0x16 => false, 0x17 => true, _ => null } : null;
    }
    private static string Slugify(string text)
    {
        text = Regex.Replace(text.Trim(), "([A-Za-z0-9]|\\G(?!^))([A-Z])", "$1_$2");
        return Regex.Replace(Regex.Replace(text.ToUpperInvariant(), "\\s+", "_"), "[^A-Z0-9_]", "");
    }
    private static string? AttributeName(MetadataReader reader, Names names, EntityHandle constructor) => constructor.Kind switch
    {
        HandleKind.MemberReference => names.Entity(reader.GetMemberReference((MemberReferenceHandle)constructor).Parent),
        HandleKind.MethodDefinition => names.Definition(reader.GetMethodDefinition((MethodDefinitionHandle)constructor).GetDeclaringType()),
        _ => null
    };
    private sealed class Names(MetadataReader reader, bool omitGenericArguments = false) : ISignatureTypeProvider<string, object?>
    {
        public string Definition(TypeDefinitionHandle handle)
        {
            var type = reader.GetTypeDefinition(handle); var name = reader.GetString(type.Name); var parent = type.GetDeclaringType();
            return !parent.IsNil ? Definition(parent) + "+" + name : Qualify(reader.GetString(type.Namespace), name);
        }
        private string Reference(TypeReferenceHandle handle)
        {
            var type = reader.GetTypeReference(handle); var name = reader.GetString(type.Name);
            return type.ResolutionScope.Kind == HandleKind.TypeReference ? Reference((TypeReferenceHandle)type.ResolutionScope) + "+" + name
                : Qualify(reader.GetString(type.Namespace), name);
        }
        public string? Entity(EntityHandle handle) => handle.IsNil ? null : handle.Kind switch
        {
            HandleKind.TypeDefinition => Definition((TypeDefinitionHandle)handle),
            HandleKind.TypeReference => Reference((TypeReferenceHandle)handle),
            HandleKind.TypeSpecification => reader.GetTypeSpecification((TypeSpecificationHandle)handle).DecodeSignature(this, null),
            _ => "unresolved_metadata_handle:" + handle.Kind
        };
        private static string Qualify(string ns, string name) => ns.Length == 0 ? name : ns + "." + name;
        public string GetArrayType(string elementType, ArrayShape shape) => elementType + "[" + new string(',', shape.Rank - 1) + "]";
        public string GetByReferenceType(string elementType) => elementType + "&";
        public string GetFunctionPointerType(MethodSignature<string> signature) => "delegate*<" + string.Join(',', signature.ParameterTypes.Append(signature.ReturnType)) + ">";
        public string GetGenericInstantiation(string genericType, ImmutableArray<string> typeArguments) => omitGenericArguments ? genericType : genericType + "<" + string.Join(',', typeArguments) + ">";
        public string GetGenericMethodParameter(object? genericContext, int index) => "!!" + index;
        public string GetGenericTypeParameter(object? genericContext, int index) => "!" + index;
        public string GetModifiedType(string modifier, string unmodifiedType, bool isRequired) => unmodifiedType + (isRequired ? " modreq(" : " modopt(") + modifier + ")";
        public string GetPinnedType(string elementType) => elementType + " pinned";
        public string GetPointerType(string elementType) => elementType + "*";
        public string GetPrimitiveType(PrimitiveTypeCode typeCode) => typeCode.ToString();
        public string GetSZArrayType(string elementType) => elementType + "[]";
        public string GetTypeFromDefinition(MetadataReader _, TypeDefinitionHandle handle, byte rawTypeKind) => Definition(handle);
        public string GetTypeFromReference(MetadataReader _, TypeReferenceHandle handle, byte rawTypeKind) => Reference(handle);
        public string GetTypeFromSpecification(MetadataReader _, object? genericContext, TypeSpecificationHandle handle, byte rawTypeKind)
            => reader.GetTypeSpecification(handle).DecodeSignature(this, genericContext);
    }
}
public sealed record PeField(string Name, int MetadataToken, string Attributes, string Type);
public sealed record PeMethod(string Name, int MetadataToken, string Attributes, string ReturnType, string[] Parameters, string? IlSha256, bool? ConstantBooleanReturn);
public sealed record PeType(string FullName, string Name, int MetadataToken, string Attributes, string? BaseType, bool IsAbstract,
    int GenericParameters, bool CompilerGenerated, PeField[] Fields, PeMethod[] Methods);
public sealed record PeModelCandidate(string FullName, string ModelId, string CategoryType, bool? IsMockConstant, bool DeprecatedNameHint);
public sealed record PeReport(string SchemaVersion, string InputAssemblySha256, string AssemblyName, string AssemblyVersion, string Scope,
    IReadOnlyList<PeType> Types, IReadOnlyList<PeModelCandidate> ModelCandidates, IReadOnlyDictionary<string, int> Categories,
    IReadOnlyDictionary<string, string[]> DuplicateCandidateIds, bool RuntimeModelRosterVerified, bool GameRulesExecuted, string[] Limitations);
