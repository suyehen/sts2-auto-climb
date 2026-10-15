using System.Security.Cryptography;
using System.Text.Json;

namespace AutoSlayBridge;

public sealed record ActionDefinition(string Type, string DefinitionId, string Group,
    IReadOnlyList<string> SourceKinds, IReadOnlyList<string> Phases, string TargetRule, string CompletionBoundary,
    string ExecutionAdapter, string Simulation, bool AgentAllowed);

public sealed record ActionOrigin(string Kind, string? EntityId, string? InstanceId);

/// <summary>The embedded resource is the same versioned JSON Python reads.</summary>
public static class ActionCatalog
{
    private static readonly byte[] Raw = ReadResource();
    private static readonly IReadOnlyDictionary<string, ActionDefinition> Definitions = Parse(Raw);
    public static string Sha256 { get; } = Convert.ToHexString(SHA256.HashData(Raw)).ToLowerInvariant();
    public static string Version => "1.0.0";
    public static IReadOnlyCollection<string> Types => Definitions.Keys.ToArray();

    private static byte[] ReadResource()
    {
        using var stream = typeof(ActionCatalog).Assembly.GetManifestResourceStream("AutoSlay.ActionCatalog.json")
            ?? throw new InvalidOperationException("ACTION_CATALOG_MISSING");
        using var buffer = new MemoryStream();
        stream.CopyTo(buffer);
        return buffer.ToArray();
    }

    private static IReadOnlyDictionary<string, ActionDefinition> Parse(byte[] raw)
    {
        using var document = JsonDocument.Parse(raw);
        var root = document.RootElement;
        if (root.GetProperty("schema_version").GetString() != "action-catalog/1.0"
            || root.GetProperty("version").GetString() != "1.0.0")
            throw new InvalidOperationException("UNSUPPORTED_ACTION_CATALOG");
        var rows = new Dictionary<string, ActionDefinition>();
        foreach (var row in root.GetProperty("actions").EnumerateArray())
        {
            string Text(string key) => row.GetProperty(key).GetString()
                ?? throw new InvalidOperationException("INVALID_ACTION_DEFINITION");
            string[] Texts(string key) => row.GetProperty(key).EnumerateArray().Select(x => x.GetString()!).ToArray();
            var kind = Text("type");
            var value = new ActionDefinition(kind, Text("definition_id"), Text("group"),
                Array.AsReadOnly(Texts("source_kinds")), Array.AsReadOnly(Texts("phases")), Text("target_rule"), Text("completion_boundary"),
                Text("execution_adapter"), Text("simulation"), row.GetProperty("agent_allowed").GetBoolean());
            if (value.DefinitionId != "ACTION." + kind || !root.GetProperty("target_rules").TryGetProperty(value.TargetRule, out _)
                || !rows.TryAdd(kind, value)) throw new InvalidOperationException("INVALID_ACTION_DEFINITION");
        }
        return rows;
    }

    public static ActionDefinition Require(string kind) => Definitions.TryGetValue(kind, out var row)
        ? row : throw new InvalidOperationException("UNKNOWN_ACTION_TYPE:" + kind);

    public static void ValidateBinding(string kind, string[] targets, bool allowsNoTarget)
    {
        var row = Require(kind);
        if (targets.Distinct().Count() != targets.Length || targets.Any(string.IsNullOrWhiteSpace)
            || (!allowsNoTarget && targets.Length == 0)
            || (row.TargetRule == "none" && (targets.Length != 0 || !allowsNoTarget)))
            throw new InvalidOperationException("ACTION_TARGET_RULE_MISMATCH:" + kind);
    }
}
