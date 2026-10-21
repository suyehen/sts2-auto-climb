using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Localization.DynamicVars;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;

namespace AutoSlay.Rules.Native.Models;

/// <summary>Initial relic state/clone/callback surface; acquisition, bags and room lifecycle remain separate.</summary>
public abstract class RelicModel : AbstractModel
{
    private Player? owner;
    private DynamicVarSet? dynamicVars;
    private RelicModel? canonicalInstance;
    public abstract RelicRarity Rarity { get; }
    public virtual bool HasUponPickupEffect => false;
    public virtual bool IsStackable => false;
    public bool IsWax { get; internal set; }
    public bool IsMelted { get; internal set; }
    public bool HasBeenRemovedFromState { get; private set; }
    public int FloorAddedToDeck { get; internal set; }
    public int StackCount { get; internal set; } = 1;
    public RelicStatus Status { get; internal set; }
    public override bool ShouldReceiveCombatHooks => true;
    public Player Owner
    {
        get { AssertMutable(); return owner ?? throw new RuleBoundaryException("state_incomplete", "Relic owner is missing: " + Id); }
        set
        {
            AssertMutable();
            if (owner != null && owner != value) throw new InvalidOperationException("Cannot move relic to another owner");
            if (value.RunState.World != World) throw new RuleBoundaryException("foreign_state", "Relic and owner have different worlds");
            owner = value;
        }
    }
    public RelicModel CanonicalInstance => canonicalInstance ?? (IsCanonical ? this : ModelDb.GetById<RelicModel>(Id));
    public DynamicVarSet DynamicVars
    {
        get
        {
            if (dynamicVars == null) { dynamicVars = new(CanonicalVars); dynamicVars.InitializeWithOwner(this); }
            return dynamicVars;
        }
    }
    protected virtual IEnumerable<DynamicVar> CanonicalVars => Array.Empty<DynamicVar>();
    public virtual Task AfterObtained() => Task.CompletedTask;
    public virtual Task AfterRemoved() => Task.CompletedTask;
    public RelicModel ToMutable() { AssertCanonical(); return (RelicModel)MutableClone(); }
    protected override void DeepCloneFields() { base.DeepCloneFields(); dynamicVars = DynamicVars.Clone(this); }
    protected override void AfterCloned()
    {
        base.AfterCloned(); canonicalInstance ??= ModelDb.GetById<RelicModel>(Id); HasBeenRemovedFromState = false;
    }
}
