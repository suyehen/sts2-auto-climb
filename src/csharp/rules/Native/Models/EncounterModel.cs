using AutoSlay.Rules.Native.Rooms;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Native.Models;

/// <summary>Native encounter classification surface. Encounter creation/RNG/spawn/rewards are not migrated.</summary>
public abstract class EncounterModel : AbstractModel
{
    public override bool ShouldReceiveCombatHooks => false;
    public abstract RoomType RoomType { get; }
    protected override void DeepCloneFields() => throw new RuleBoundaryException("mechanism_not_implemented", "Encounter creation lifecycle is not migrated");
}
