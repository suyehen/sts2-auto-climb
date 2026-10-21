using System.Text.Json;

namespace AutoSlay.RuleSourceSync;

public static class ParameterDifference
{
    public static ParameterChange Compare(string oldPath, string newPath)
    {
        var oldPack = Read(oldPath); var newPack = Read(newPath);
        var removed = oldPack.Entries.Keys.Except(newPack.Entries.Keys).Order(StringComparer.Ordinal).ToArray();
        var added = newPack.Entries.Keys.Except(oldPack.Entries.Keys).Order(StringComparer.Ordinal).ToArray();
        var changed = oldPack.Entries.Keys.Intersect(newPack.Entries.Keys).Order(StringComparer.Ordinal)
            .Where(k => oldPack.Entries[k] != newPack.Entries[k]).Select(k => new NumericChange(k,
                oldPack.Entries[k].Kind, newPack.Entries[k].Kind, oldPack.Entries[k].Value, newPack.Entries[k].Value)).ToArray();
        var classification = oldPack.Contract != newPack.Contract ? "binding_contract_changed_recompile_required"
            : oldPack.Behavior != newPack.Behavior ? "behavior_changed_semantic_migration_required"
            : changed.Length != 0 ? "numeric_data_only_within_extracted_scope" : "no_numeric_or_compiled_behavior_change";
        return new("autoslay-rule-parameter-difference/1", classification, oldPack.Fingerprint, newPack.Fingerprint,
            oldPack.Behavior, newPack.Behavior, oldPack.Contract, newPack.Contract, added, removed, changed,
            false, "Classification applies only to the parameterized staged source. It does not certify full parameter coverage, model support, deployment or regression success.");
    }
    private static Package Read(string path)
    {
        using var document = JsonDocument.Parse(File.ReadAllBytes(path)); var root = document.RootElement;
        if (root.GetProperty("schema_version").GetString() != "autoslay-rule-parameters/1") throw new InvalidOperationException("Unsupported parameter schema");
        var entries = new Dictionary<string, Scalar>(StringComparer.Ordinal);
        foreach (var item in root.GetProperty("entries").EnumerateArray())
        {
            var kind = item.GetProperty("kind").GetString()!;
            if (kind is not ("int32" or "decimal")) throw new InvalidOperationException("Unknown parameter kind");
            var value = item.GetProperty("value").GetDecimal();
            if (kind == "int32" && (value != decimal.Truncate(value) || value < int.MinValue || value > int.MaxValue))
                throw new InvalidOperationException("Invalid integer parameter");
            entries.Add(item.GetProperty("key").GetString()!, new(kind, value));
        }
        var contract = root.GetProperty("contract_sha256").GetString()!;
        var actual = ParameterExtraction.ContractHash(entries.Select(p => new ParameterEntry(p.Key, p.Value.Kind, p.Value.Value, "", "", 0)));
        if (actual != contract) throw new InvalidOperationException("Parameter binding contract differs from entries");
        return new(root.GetProperty("source_fingerprint").GetString()!, root.GetProperty("behavior_sha256").GetString()!, contract, entries);
    }
    private sealed record Scalar(string Kind, decimal Value);
    private sealed record Package(string Fingerprint, string Behavior, string Contract, Dictionary<string, Scalar> Entries);
}
public sealed record NumericChange(string Key, string OldKind, string NewKind, decimal OldValue, decimal NewValue);
public sealed record ParameterChange(string SchemaVersion, string Classification, string OldSourceFingerprint, string NewSourceFingerprint,
    string OldBehaviorSha256, string NewBehaviorSha256, string OldContractSha256, string NewContractSha256,
    string[] AddedKeys, string[] RemovedKeys, NumericChange[] ChangedValues, bool FullSupport, string Limitation);
