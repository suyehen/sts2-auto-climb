using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;
using AutoSlay.Rules.Native.GameActions.Multiplayer;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.ValueProps;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Players;

namespace AutoSlay.Rules.Native.Models;

/// <summary>Initial native model lifecycle surface. Unmigrated APIs are absent, never empty stubs.</summary>
public abstract class AbstractModel : IComparable<AbstractModel>
{
    private SimulationWorld? world;
    private readonly RuleParameters? constructorParameters;
    public ModelId Id { get; }
    public string? InstanceId { get; internal set; }
    public bool IsMutable { get; private set; }
    public bool IsCanonical => !IsMutable;
    public virtual bool IsMock => false;
    public abstract bool ShouldReceiveCombatHooks { get; }
    public SimulationWorld World => world ?? throw new RuleBoundaryException("state_incomplete", "Model context is not initialized");
    protected ModelDb ModelDb => World.Models;
    protected RuleParameters Parameters => (world == null ? constructorParameters : world.Parameters)
        ?? throw new RuleBoundaryException("parameter_missing", "Model parameter package is missing");
    public event Action<AbstractModel>? ExecutionFinished;
    // Owned constructor adapter: immutable numeric context only, never an ambient world.
    protected AbstractModel(RuleParameters? parameters = null)
    {
        constructorParameters = parameters;
        Id = global::AutoSlay.Rules.Native.Models.ModelDb.GetId(GetType());
    }
    public int CompareTo(AbstractModel? other) => ReferenceEquals(this, other) ? 0 : other == null ? 1 : Id.CompareTo(other.Id);
    public void AssertMutable() { if (!IsMutable) throw new RuleBoundaryException("canonical_mutation", "Expected mutable model: " + Id); }
    public void AssertCanonical() { if (IsMutable) throw new RuleBoundaryException("mutable_model", "Expected canonical model: " + Id); }
    public AbstractModel ClonePreservingMutability() => IsMutable ? MutableClone() : this;
    public AbstractModel MutableClone()
    {
        var copy = (AbstractModel)MemberwiseClone(); copy.IsMutable = true;
        copy.DeepCloneFields(); copy.AfterCloned(); return copy;
    }
    protected virtual void DeepCloneFields() => AssertMutable();
    protected virtual void AfterCloned() => ExecutionFinished = null;
    public void InvokeExecutionFinished() => ExecutionFinished?.Invoke(this);
    // These two native base callbacks are genuinely empty; overrides execute compiled model code.
    public virtual Task AfterCurrentHpChanged(Creature creature, decimal delta) => Task.CompletedTask;
    public virtual Task BeforePowerAmountChanged(PowerModel power, decimal amount, Creature target, Creature? applier, CardModel? cardSource) => Task.CompletedTask;
    public virtual Task AfterPowerAmountChanged(PlayerChoiceContext choiceContext, PowerModel power, decimal amount, Creature? applier, CardModel? cardSource) => Task.CompletedTask;
    public virtual decimal ModifyPowerAmountGivenAdditive(PowerModel power, Creature giver, decimal amount, Creature? target, CardModel? cardSource) => 0;
    public virtual decimal ModifyPowerAmountGivenMultiplicative(PowerModel power, Creature giver, decimal amount, Creature? target, CardModel? cardSource) => 1;
    public virtual bool TryModifyPowerAmountReceived(PowerModel canonicalPower, Creature target, decimal amount, Creature? applier, out decimal modifiedAmount)
    { modifiedAmount = amount; return false; }
    public virtual Task AfterModifyingPowerAmountGiven(PowerModel power) => Task.CompletedTask;
    public virtual Task AfterModifyingPowerAmountReceived(PowerModel power) => Task.CompletedTask;
    public virtual bool ShouldAllowHitting(Creature creature) => true;
    public virtual decimal ModifyEnergyGain(Player player, decimal amount) => amount;
    public virtual Task AfterModifyingEnergyGain() => Task.CompletedTask;
    public virtual bool ShouldGainStars(decimal amount, Player player) => true;
    public virtual bool ShouldPayExcessEnergyCostWithStars(Player player) => false;
    public virtual Task AfterStarsGained(int amount, Player gainer) => Task.CompletedTask;
    public virtual Task AfterStarsSpent(int amount, Player spender) => Task.CompletedTask;
    public virtual Task AfterEnergySpent(CardModel card, int amount) => Task.CompletedTask;
    public virtual bool TryModifyEnergyCostInCombat(CardModel card, decimal originalCost, out decimal modifiedCost)
    { modifiedCost = originalCost; return false; }
    public virtual bool TryModifyEnergyCostInCombatLate(CardModel card, decimal originalCost, out decimal modifiedCost)
    { modifiedCost = originalCost; return false; }
    public virtual bool TryModifyKeywordsInCombat(CardModel card, ISet<CardKeyword> keywords) => false;
    public virtual bool TryModifyStarCost(CardModel card, decimal originalCost, out decimal modifiedCost)
    { modifiedCost = originalCost; return false; }
    public virtual decimal ModifyDamageAdditive(Creature? target, decimal amount, ValueProp props, Creature? dealer, CardModel? cardSource, CardPlay? cardPlay) => 0;
    public virtual decimal ModifyBlockMultiplicative(Creature target, decimal block, ValueProp props, CardModel? cardSource, CardPlay? cardPlay) => 1;
    public virtual Task AfterSideTurnEnd(PlayerChoiceContext choiceContext, CombatSide side, IEnumerable<Creature> participants) => Task.CompletedTask;
    internal void InitializeCanonical(SimulationWorld context)
    {
        AssertCanonical();
        if (world != null) throw new InvalidOperationException("Model already initialized");
        if (constructorParameters != null && !ReferenceEquals(constructorParameters, context.Parameters))
            throw new RuleBoundaryException("parameter_mismatch", "Constructor parameters belong to another world");
        world = context;
    }
}
