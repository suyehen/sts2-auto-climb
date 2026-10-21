namespace AutoSlay.Rules.Runtime;

public sealed class RuleBoundaryException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}

public sealed record RuleIdentity(string SourceVersion, string SourceAssemblySha256, string RulesSha256,
    string ParametersSha256, string ProtocolVersion);

public sealed record CommandTrace(long Sequence, string Operation, string Source, string Target,
    decimal Requested, int HpBefore, int HpAfter, int MaxHpBefore, int MaxHpAfter, int BlockBefore, int BlockAfter);
