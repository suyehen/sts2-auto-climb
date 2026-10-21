using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Native.Context;

/// <summary>Native local-player predicates. The native process global becomes explicit world state.</summary>
public sealed class LocalContext
{
    private bool observed;
    private ulong? netId;
    internal bool IsObserved => observed;
    public ulong? NetId => observed ? netId
        : throw new RuleBoundaryException("state_incomplete", "Local player identity was not observed");
    internal void Restore(ulong? value) { netId = value; observed = true; }
    public bool IsMe(Player? player) => player != null && NetId.HasValue && player.NetId == NetId;
    public bool IsMe(Creature? creature) => IsMe(creature?.Player);
}
