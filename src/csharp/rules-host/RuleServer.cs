using System.Security.Cryptography;
using System.Text.Json;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Parameters;

namespace AutoSlay.RulesHost;

public sealed class RuleServer
{
    public const string Protocol = "autoslay-rules-host/1";
    private readonly string epoch = Guid.NewGuid().ToString("N");
    private readonly RuleParameters parameters;
    private readonly string rulesSha256 = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(typeof(RuleIdentity).Assembly.Location))).ToLowerInvariant();
    private readonly string hostSha256 = Convert.ToHexString(SHA256.HashData(File.ReadAllBytes(typeof(RuleServer).Assembly.Location))).ToLowerInvariant();
    public RuleServer(RuleParameters? parameters = null)
    {
        this.parameters = parameters ?? CompiledRules.LoadDefaultParameters(); CompiledRules.ValidateIdentity(this.parameters);
    }
    public object Process(JsonElement request)
    {
        if (request.ValueKind != JsonValueKind.Object || !request.TryGetProperty("request_id", out var id)
            || id.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(id.GetString()) || id.GetString()!.Length > 128)
            throw new RuleBoundaryException("invalid_request", "A string request_id is required");
        var names = new HashSet<string>(StringComparer.Ordinal);
        if (request.EnumerateObject().Any(p => !names.Add(p.Name) || p.Name is not ("protocol_version" or "request_id" or "operation" or "payload")))
            return Error(id, "invalid_request", "Unknown or duplicate request field");
        if (request.TryGetProperty("payload", out var payload) && payload.ValueKind != JsonValueKind.Object)
            return Error(id, "invalid_request", "Object payload required");
        if (!request.TryGetProperty("protocol_version", out var protocol) || protocol.ValueKind != JsonValueKind.String || protocol.GetString() != Protocol)
            return Error(id, "protocol_mismatch", "Unsupported rules protocol");
        if (!request.TryGetProperty("operation", out var operation) || operation.ValueKind != JsonValueKind.String || string.IsNullOrWhiteSpace(operation.GetString()))
            return Error(id, "invalid_request", "Operation required");
        if (operation.GetString() == "hello" && request.TryGetProperty("payload", out var helloPayload) && helloPayload.EnumerateObject().Any())
            return Error(id, "invalid_request", "hello does not accept payload fields");
        return operation.GetString() switch
        {
            "hello" => new { protocol_version = Protocol, request_id = id.GetString(), ok = true, result = new {
                host_epoch = epoch, engine = "independent_csharp", source_version = "0.111.0",
                source_assembly_sha256 = "0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9",
                rules_sha256 = rulesSha256, host_sha256 = hostSha256,
                parameter_schema = RuleParameters.Schema, parameters_sha256 = parameters.Sha256,
                parameter_contract_sha256 = parameters.ContractSha256, parameter_behavior_sha256 = parameters.BehaviorSha256,
                parameter_source_fingerprint = parameters.SourceFingerprint,
                full_support = false, capabilities = new[] { "identity_handshake" },
                limitations = new[] { "Only identity handshake is exposed. Compiled callback units do not establish complete model, listener or flow support; no production cutover." }
            } },
            _ => Error(id, "operation_not_implemented", "Rule operation not migrated: " + operation.GetString())
        };
    }
    private static object Error(JsonElement id, string code, string message) => new {
        protocol_version = Protocol, request_id = id.GetString(), ok = false, error = new { code, message }
    };
}
