using System.Collections;
using System.Diagnostics.CodeAnalysis;

namespace AutoSlay.Rules.Native.Localization.DynamicVars;

/// <summary>Scalar variable set. Owner-dependent calculated variables need the full model graph.</summary>
public sealed class DynamicVarSet : IReadOnlyDictionary<string, DynamicVar>
{
    private readonly Dictionary<string, DynamicVar> variables = new(StringComparer.Ordinal);
    public DynamicVarSet(IEnumerable<DynamicVar> vars)
    {
        foreach (var variable in vars)
            if (!variables.TryAdd(variable.Name, variable)) throw new ArgumentException("DynamicVarSet contains duplicate key '" + variable.Name + "'");
    }
    public DynamicVar this[string key] => variables[key];
    public IEnumerable<string> Keys => variables.Keys;
    public IEnumerable<DynamicVar> Values => variables.Values;
    public int Count => variables.Count;
    public BlockVar Block => (BlockVar)this["Block"];
    public DamageVar Damage => (DamageVar)this["Damage"];
    public CardsVar Cards => (CardsVar)this["Cards"];
    public EnergyVar Energy => (EnergyVar)this["Energy"];
    public GoldVar Gold => (GoldVar)this["Gold"];
    public HealVar Heal => (HealVar)this["Heal"];
    public MaxHpVar MaxHp => (MaxHpVar)this["MaxHp"];
    public RepeatVar Repeat => (RepeatVar)this["Repeat"];
    public HpLossVar HpLoss => (HpLossVar)this["HpLoss"];
    public ForgeVar Forge => (ForgeVar)this["Forge"];
    public StarsVar Stars => (StarsVar)this["Stars"];
    public CalculationBaseVar CalculationBase => (CalculationBaseVar)this["CalculationBase"];
    public CalculationExtraVar CalculationExtra => (CalculationExtraVar)this["CalculationExtra"];
    public bool ContainsKey(string key) => variables.ContainsKey(key);
    public bool TryGetValue(string key, [MaybeNullWhen(false)] out DynamicVar value) => variables.TryGetValue(key, out value);
    public IEnumerator<KeyValuePair<string, DynamicVar>> GetEnumerator() => variables.GetEnumerator();
    IEnumerator IEnumerable.GetEnumerator() => GetEnumerator();
    public void ClearPreview() { foreach (var variable in Values) variable.ResetToBase(); }
    public void FinalizeUpgrade() { foreach (var variable in Values) variable.FinalizeUpgrade(); }
    public void RecalculateForUpgradeOrEnchant()
    { foreach (var variable in Values.OfType<CalculatedVar>()) variable.RecalculateForUpgradeOrEnchant(); }
    public void InitializeWithOwner(AutoSlay.Rules.Native.Models.AbstractModel owner)
    { foreach (var variable in Values) variable.SetOwner(owner); }
    public DynamicVarSet Clone(AutoSlay.Rules.Native.Models.AbstractModel owner)
    {
        var copy = new DynamicVarSet(Values.Select(v => v.Clone())); copy.InitializeWithOwner(owner); return copy;
    }
    // Called only for the current scalar-only set. Full world cloning must rebind owner/model references.
    public DynamicVarSet CloneScalars()
    {
        foreach (var variable in Values)
            if (variable.GetType().Assembly != typeof(DynamicVar).Assembly)
                throw new AutoSlay.Rules.Runtime.RuleBoundaryException("mechanism_not_implemented", "Unknown variable clone semantics: " + variable.GetType().FullName);
        return new(Values.Select(v => v.Clone()));
    }
}
