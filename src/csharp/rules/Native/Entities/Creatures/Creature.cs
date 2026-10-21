using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.ValueProps;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Entities.Powers;
using AutoSlay.Rules.Native.Hooks;

namespace AutoSlay.Rules.Native.Entities.Creatures;

/// <summary>Own-state resource arithmetic ported from Creature; never a live-game object.</summary>
public sealed class Creature
{
    private int currentHp;
    private int maxHp;
    private int block;
    private readonly List<PowerModel> powers = [];
    public IReadOnlyList<PowerModel> Powers => powers;
    public string InstanceId { get; }
    public int CurrentHp
    {
        get => currentHp;
        private set
        {
            if (value < 0) throw new ArgumentException("Current HP must be positive", nameof(value));
            if (currentHp != value) { var before = currentHp; currentHp = value; CurrentHpChanged?.Invoke(before, currentHp); }
        }
    }
    public int MaxHp
    {
        get => maxHp;
        private set { if (maxHp != value) { var before = maxHp; maxHp = value; MaxHpChanged?.Invoke(before, maxHp); } }
    }
    public int Block
    {
        get => block;
        private set
        {
            if (value < 0) throw new ArgumentException("Block must be positive", nameof(value));
            if (block != value) { var before = block; block = value; BlockChanged?.Invoke(before, block); }
        }
    }
    public bool IsDead => CurrentHp <= 0;
    public bool IsAlive => !IsDead;
    private CombatSide? observedSide;
    public CombatSide Side => observedSide ?? throw new RuleBoundaryException("state_incomplete", "Creature combat side was not observed");
    internal CombatSide? ObservedSide => observedSide;
    internal void RestoreSide(CombatSide side) => observedSide = side;
    public bool IsEnemy => Side == CombatSide.Enemy;
    public bool IsPrimaryEnemy => IsEnemy && !IsSecondaryEnemy;
    public bool IsSecondaryEnemy => IsEnemy && Powers.Any(p => p.OwnerIsSecondaryEnemy);
    public bool CanReceivePowers => CombatState != null && Hook.ShouldAllowHitting(CombatState, this);
    public bool IsPlayer => Player != null;
    public Player? Player { get; internal set; }
    public CombatState? CombatState { get; internal set; }
    internal SimulationWorld? AttachedWorld { get; set; }
    public SimulationWorld World => AttachedWorld ?? throw new RuleBoundaryException("foreign_state", "Creature has not been attached to an independent world");
    public event Action<Creature>? Revived;
    public event Action<int, int>? BlockChanged;
    public event Action<int, int>? CurrentHpChanged;
    public event Action<int, int>? MaxHpChanged;
    public event Action<PowerModel>? PowerApplied;
    public event Action<PowerModel, int, bool>? PowerIncreased;
    public event Action<PowerModel, bool>? PowerDecreased;
    public event Action<PowerModel>? PowerRemoved;

    public Creature(string instanceId, int hp, int maxHp, int block = 0)
    {
        if (string.IsNullOrWhiteSpace(instanceId) || maxHp < 0 || maxHp > 999999999 || hp < 0 || hp > maxHp || block < 0 || block > 999999999)
            throw new ArgumentException("Invalid creature resources or identity");
        InstanceId = instanceId; currentHp = hp; this.maxHp = maxHp; this.block = block;
    }

    // Only used while allocating a world graph; its powers, references and internal subscribers are
    // filled by OwnedStateCopier before the new world becomes visible.
    internal Creature CreateCloneShell() => new(InstanceId, CurrentHp, MaxHp, Block);
    public bool HasPower<T>() where T : PowerModel => powers.Any(p => p is T);
    public bool HasPower(ModelId id) => powers.Any(p => p.Id == id);
    public T? GetPower<T>() where T : PowerModel => powers.OfType<T>().FirstOrDefault();
    public PowerModel? GetPower(ModelId id) => powers.FirstOrDefault(p => p.Id == id);
    public IEnumerable<T> GetPowerInstances<T>() where T : PowerModel => powers.OfType<T>();
    public IEnumerable<PowerModel> GetPowerInstances(ModelId id) => powers.Where(p => p.Id == id);
    public PowerModel? GetPowerById(ModelId id) => GetPower(id);
    public int GetPowerAmount<T>() where T : PowerModel => GetPower<T>()?.Amount ?? 0;
    public void ApplyPowerInternal(PowerModel power)
    {
        if (power.Owner != this) throw new InvalidOperationException("ONLY CALL THIS FROM PowerModel.ApplyInternal!");
        if (power.InstanceType == PowerInstanceType.None && powers.Any(p => p.GetType() == power.GetType()))
            throw new InvalidOperationException("Trying to add multiple instances of a non-instanced power to a creature.");
        powers.Add(power); CombatState?.HookInventory?.PowerListChanged(this); AttachedWorld?.InvalidateHookProjection(); PowerApplied?.Invoke(power);
    }
    public void InvokePowerModified(PowerModel power, int change, bool silent)
    {
        if (change > 0 || power.StackType == PowerStackType.Counter && power.AllowNegative && change < 0)
            PowerIncreased?.Invoke(power, change, silent);
        else PowerDecreased?.Invoke(power, silent);
    }
    public void RemovePowerInternal(PowerModel power)
    {
        if (power.Owner != this) throw new InvalidOperationException("ONLY CALL THIS FROM PowerModel.RemoveInternal!");
        powers.Remove(power); CombatState?.HookInventory?.PowerListChanged(this); AttachedWorld?.InvalidateHookProjection(); PowerRemoved?.Invoke(power);
    }
    public IEnumerable<PowerModel> RemoveAllPowersInternalExcept(IEnumerable<PowerModel>? except = null)
    {
        var removed = powers.Except(except ?? Array.Empty<PowerModel>()).ToList();
        foreach (var power in removed) power.RemoveInternal(); return removed;
    }
    public void BeforeTurnStart(CombatSide side)
    { foreach (var power in powers) power.AmountOnTurnStart = power.Amount; }
    internal void RestorePower(PowerModel power) => powers.Add(power);
    public DamageResult LoseHpInternal(decimal amount, ValueProp props)
    {
        var killed = CurrentHp > 0 && amount >= CurrentHp;
        var before = CurrentHp;
        var damage = (int)Math.Clamp(amount, 0m, 999999999m);
        CurrentHp = Math.Max(CurrentHp - damage, 0);
        return new(this, props) { UnblockedDamage = before - CurrentHp, WasTargetKilled = killed,
            OverkillDamage = killed ? Math.Max(damage - before, 0) : 0 };
    }
    public decimal DamageBlockInternal(decimal amount, ValueProp props)
    {
        var blocked = props.HasFlag(ValueProp.Unblockable) ? 0m : Math.Min(Block, amount);
        Block -= (int)blocked; return blocked;
    }
    public void GainBlockInternal(decimal amount)
    {
        if (amount < 0) throw new ArgumentException("Use LoseBlock for block loss");
        Block = (int)Math.Min(Block + amount, 999999999m);
    }
    public void LoseBlockInternal(decimal amount)
    {
        if (amount < 0) throw new ArgumentException("Use GainBlock for block gain");
        Block = (int)Math.Max(Block - amount, 0m);
    }
    public void HealInternal(decimal amount)
    {
        var wasDead = IsDead; SetCurrentHpInternal(CurrentHp + amount);
        if (wasDead && !IsDead) { Player?.ActivateHooks(); Revived?.Invoke(this); }
    }
    public void SetCurrentHpInternal(decimal amount)
    {
        // Native truncation is applied to the sum, not to the healing operand.
        CurrentHp = (int)Math.Min(amount, MaxHp);
    }
    public void SetMaxHpInternal(decimal amount)
    {
        if (amount < 0) throw new ArgumentException("Max HP must be nonnegative");
        MaxHp = Math.Min((int)amount, 999999999);
        CurrentHp = Math.Min(CurrentHp, MaxHp);
    }
}
