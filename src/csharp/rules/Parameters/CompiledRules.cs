using AutoSlay.Rules.Generated;

namespace AutoSlay.Rules.Parameters;

/// <summary>Compiled behavior/binding identity is authoritative; never read expected identity from the incoming package.</summary>
public static class CompiledRules
{
    public static RuleParameters LoadParameters(ReadOnlySpan<byte> utf8) =>
        RuleParameters.Load(utf8, CompiledUnitIdentity.BehaviorSha256, CompiledUnitIdentity.ContractSha256);
    public static RuleParameters LoadDefaultParameters()
    {
        using var stream = typeof(CompiledRules).Assembly.GetManifestResourceStream("AutoSlay.Rules.Parameters.Default")
            ?? throw new InvalidOperationException("Compiled unit parameter resource missing");
        using var buffer = new MemoryStream(); stream.CopyTo(buffer); return LoadParameters(buffer.ToArray());
    }
    public static void ValidateIdentity(RuleParameters parameters)
    {
        if (parameters.BehaviorSha256 != CompiledUnitIdentity.BehaviorSha256 || parameters.ContractSha256 != CompiledUnitIdentity.ContractSha256)
            throw new AutoSlay.Rules.Runtime.RuleBoundaryException("parameter_mismatch", "Package does not match this compiled rule unit identity");
    }
}
