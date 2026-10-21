using System.Collections.Frozen;
using System.Globalization;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Parameters;

/// <summary>Immutable numeric data. No effect instructions, callbacks, or live game objects.</summary>
public sealed class RuleParameters
{
    public const string Schema = "autoslay-rule-parameters/1";
    private readonly FrozenDictionary<string, Entry> entries;
    public string Sha256 { get; }
    public string SourceFingerprint { get; }
    public string BehaviorSha256 { get; }
    public string ContractSha256 { get; }
    public int Count => entries.Count;

    private RuleParameters(byte[] bytes, string source, string behavior, string contract, Dictionary<string, Entry> values)
    {
        Sha256 = Hash(bytes); SourceFingerprint = source; BehaviorSha256 = behavior;
        ContractSha256 = contract; entries = values.ToFrozenDictionary(StringComparer.Ordinal);
    }

    public static RuleParameters Load(ReadOnlySpan<byte> utf8, string expectedBehaviorSha256, string expectedContractSha256)
    {
        try
        {
            using var document = JsonDocument.Parse(utf8.ToArray());
            var root = document.RootElement;
            Fields(root, ["schema_version", "source_fingerprint", "behavior_sha256", "contract_sha256", "entries"]);
            if (Text(root, "schema_version") != Schema) Fail("parameter_mismatch", "Unsupported parameter schema");
            var source = Text(root, "source_fingerprint"); var behavior = Text(root, "behavior_sha256");
            var contract = Text(root, "contract_sha256");
            if (!IsHash(source) || !IsHash(behavior) || !IsHash(contract)) Fail("invalid_parameters", "Invalid fingerprint");
            if (behavior != expectedBehaviorSha256 || contract != expectedContractSha256)
                Fail("parameter_mismatch", "Parameters do not belong to the compiled behavior/binding contract");
            var data = root.GetProperty("entries");
            if (data.ValueKind != JsonValueKind.Array) Fail("invalid_parameters", "Parameter entries must be an array");
            var values = new Dictionary<string, Entry>(StringComparer.Ordinal);
            foreach (var item in data.EnumerateArray())
            {
                Fields(item, ["key", "kind", "value", "source_path", "source_sha256", "line"]);
                var key = Text(item, "key"); var kind = Text(item, "kind");
                var path = Text(item, "source_path"); var sha = Text(item, "source_sha256");
                if (key.Length == 0 || string.IsNullOrWhiteSpace(path) || !IsHash(sha)
                    || !item.GetProperty("line").TryGetInt32(out var line) || line < 1)
                    Fail("invalid_parameters", "Missing parameter provenance: " + key);
                var number = item.GetProperty("value");
                if (number.ValueKind != JsonValueKind.Number || !number.TryGetDecimal(out var value))
                    Fail("invalid_parameters", "A finite decimal parameter is required: " + key);
                else
                {
                    if (kind is not ("decimal" or "int32")) Fail("invalid_parameters", "Unsupported scalar type: " + kind);
                    if (kind == "int32" && (decimal.Truncate(value) != value || value < int.MinValue || value > int.MaxValue))
                        Fail("invalid_parameters", "int32 parameter out of range: " + key);
                    if (!values.TryAdd(key, new(kind, value))) Fail("invalid_parameters", "Duplicate parameter: " + key);
                }
            }
            if (ContractHash(values.Select(p => (p.Key, p.Value.Kind))) != contract)
                Fail("parameter_mismatch", "Parameter entries differ from the binding contract");
            return new(utf8.ToArray(), source, behavior, contract, values);
        }
        catch (Exception error) when (error is JsonException or KeyNotFoundException or InvalidOperationException or FormatException)
        { throw new RuleBoundaryException("invalid_parameters", error.Message); }
    }

    public decimal Decimal(string key) => Get(key, "decimal").Value;
    public int Int32(string key) => (int)Get(key, "int32").Value;
    private Entry Get(string key, string kind)
    {
        if (!entries.TryGetValue(key, out var entry)) throw new RuleBoundaryException("parameter_missing", "Missing parameter: " + key);
        if (entry.Kind != kind) throw new RuleBoundaryException("parameter_type_mismatch", "Expected " + kind + ": " + key);
        return entry;
    }

    public static string ContractHash(IEnumerable<(string Key, string Kind)> bindings) =>
        Hash(Encoding.UTF8.GetBytes(string.Join('\n', bindings.OrderBy(p => p.Key, StringComparer.Ordinal).Select(p => p.Key + ":" + p.Kind))));
    private static string Hash(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
    private static bool IsHash(string text) => text.Length == 64 && text.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f');
    private static string Text(JsonElement item, string name) => item.GetProperty(name).ValueKind == JsonValueKind.String
        ? item.GetProperty(name).GetString()! : throw new RuleBoundaryException("invalid_parameters", "String required: " + name);
    private static void Fields(JsonElement item, string[] names)
    {
        if (item.ValueKind != JsonValueKind.Object) Fail("invalid_parameters", "Object required");
        var seen = new HashSet<string>(StringComparer.Ordinal);
        foreach (var property in item.EnumerateObject())
            if (!names.Contains(property.Name, StringComparer.Ordinal) || !seen.Add(property.Name))
                Fail("invalid_parameters", "Unknown or duplicate field: " + property.Name);
        if (seen.Count != names.Length) Fail("invalid_parameters", "Missing required parameter field");
    }
    private static void Fail(string code, string message) => throw new RuleBoundaryException(code, message);
    private sealed record Entry(string Kind, decimal Value);
}
