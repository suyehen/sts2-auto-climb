using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Powers;
using AutoSlay.Rules.Native.Localization.DynamicVars;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Models.Singleton;

namespace AutoSlay.Rules.Native.Models;

/// <summary>
/// Native power state/callbacks and visibility, using explicit world-local identity and encounter/act
/// observations. Card source execution and complete surrounding lifecycle are not migrated.
/// </summary>
public abstract class PowerModel : AbstractModel
{
    private int amount;
    private int amountOnTurnStart;
    private bool skipNextDurationTick;
    private Creature? owner;
    private Creature? applier;
    private Creature? target;
    private DynamicVarSet? dynamicVars;
    private object? internalData;
    private PowerModel? canonicalInstance;

    public abstract PowerType Type { get; }
    public abstract PowerStackType StackType { get; }
    public virtual PowerInstanceType InstanceType => PowerInstanceType.None;
    public virtual bool AllowNegative => false;
    public virtual bool ShouldScaleInMultiplayer => false;
    public override bool ShouldReceiveCombatHooks => true;
    public virtual bool OwnerIsSecondaryEnemy => false;
    protected virtual bool IsVisibleInternal => true;
    public bool IsVisible => (Target == null || World.LocalContext.IsMe(Target) || Target.IsEnemy) && IsVisibleInternal;
    public virtual bool ShouldPlayVfx => Owner != null && Owner.IsAlive && World.CombatState?.IsInProgress == true && IsVisible;
    public int Amount => amount;
    public int AmountOnTurnStart
    { get => amountOnTurnStart; set { AssertMutable(); amountOnTurnStart = value; } }
    public virtual int DisplayAmount => Amount;
    public PowerType TypeForCurrentAmount => GetTypeForAmount(Amount);
    public bool SkipNextDurationTick
    { get => skipNextDurationTick; set { AssertMutable(); skipNextDurationTick = value; } }
    // Native mutable clones have no owner until ApplyInternal/observation restoration.
    public Creature Owner
    {
        get { AssertMutable(); return owner!; }
        private set
        {
            AssertMutable();
            if (owner != null && owner != value) throw new InvalidOperationException("Cannot move power " + Id.Entry + " from one owner to another");
            if (value.World != World) throw new RuleBoundaryException("foreign_state", "Power and owner have different worlds");
            owner = value;
        }
    }
    public CombatState? CombatState => Owner.CombatState;
    public Creature? Applier
    { get => applier; set { AssertMutable(); RequireOwn(value); applier = value; } }
    public Creature? Target
    { get => target; set { AssertMutable(); RequireOwn(value); target = value; } }
    public DynamicVarSet DynamicVars
    {
        get
        {
            if (dynamicVars == null) { dynamicVars = new(CanonicalVars); dynamicVars.InitializeWithOwner(this); }
            return dynamicVars;
        }
    }
    protected virtual IEnumerable<DynamicVar> CanonicalVars => Array.Empty<DynamicVar>();
    private PowerModel CanonicalInstance => IsCanonical ? this : canonicalInstance!;

    public event Action? PulsingStarted;
    public event Action? PulsingStopped;
    public event Action<PowerModel>? Flashed;
    public event Action? DisplayAmountChanged;
    public event Action? Removed;
    public void StartPulsing() => PulsingStarted?.Invoke();
    public void StopPulsing() => PulsingStopped?.Invoke();
    protected void Flash() => Flashed?.Invoke(this);
    protected void InvokeDisplayAmountChanged() => DisplayAmountChanged?.Invoke();

    public PowerType GetTypeForAmount(decimal customAmount)
    {
        if (StackType == PowerStackType.Counter && AllowNegative && customAmount < 0) return PowerType.Debuff;
        if (!AllowNegative && Type == PowerType.Debuff && customAmount < 0) return PowerType.Buff;
        return Type;
    }
    public bool ShouldRemoveDueToAmount() => AllowNegative ? Amount == 0 : Amount <= 0;
    protected virtual object? InitInternalData() => null;
    protected T GetInternalData<T>() => (T)internalData!;
    public void SetAmount(int value, bool silent = false)
    {
        AssertMutable(); value = Math.Clamp(value, -999999999, 999999999);
        var change = value - amount;
        if (change != 0)
        {
            amount = value; DisplayAmountChanged?.Invoke(); Owner.InvokePowerModified(this, change, silent);
        }
    }
    public PowerModel ToMutable(int initialAmount = 0)
    {
        AssertCanonical(); var copy = (PowerModel)MutableClone(); copy.canonicalInstance = this;
        copy.SetAmount(initialAmount); return copy;
    }
    public void ApplyInternal(Creature creature, decimal value, bool silent = false)
    {
        if (value != 0)
        {
            AssertMutable(); Owner = creature; SetAmount((int)value, silent); Owner.ApplyPowerInternal(this);
        }
    }
    public void RemoveInternal()
    { AssertMutable(); Removed?.Invoke(); Owner.RemovePowerInternal(this); }
    protected override void DeepCloneFields()
    {
        base.DeepCloneFields(); dynamicVars = DynamicVars.Clone(this); internalData = InitInternalData();
    }
    protected override void AfterCloned()
    {
        base.AfterCloned(); Flashed = null; DisplayAmountChanged = null; Removed = null;
        PulsingStarted = null; PulsingStopped = null; owner = null;
    }
    public virtual Task AfterRemoved(Creature oldOwner) => Task.CompletedTask;
    public virtual Task BeforeApplied(Creature target, decimal amount, Creature? applier, CardModel? cardSource) => Task.CompletedTask;
    public virtual Task AfterApplied(Creature? applier, CardModel? cardSource) => Task.CompletedTask;
    public virtual decimal GetScaledAmountForMultiplayer(ICombatState combatState, Creature? applier, decimal amount, Creature target, CardModel? cardSource)
        => amount * combatState.Players.Count * MultiplayerScalingModel.GetMultiplayerScaling(combatState.Encounter, combatState.RunState.CurrentActIndex);
    public virtual bool ShouldPowerBeRemovedAfterOwnerDeath() => true;
    public virtual bool ShouldOwnerDeathTriggerFatal() => true;

    private void RequireOwn(Creature? creature)
    { if (creature != null) World.RequireOwn(creature); }
    // Observation restoration is not native ApplyInternal: no application events/history/hooks replay.
    internal void RestoreOwnedState(Creature creature, int value, int turnStart, bool skipTick, Creature? source, Creature? powerTarget)
    {
        AssertMutable(); Owner = creature; Applier = source; Target = powerTarget;
        amount = value; amountOnTurnStart = turnStart; skipNextDurationTick = skipTick;
    }
}
