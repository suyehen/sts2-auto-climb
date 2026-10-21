using AutoSlay.Rules.Native.ValueProps;

namespace AutoSlay.Rules.Native.Localization.DynamicVars;

// Numeric constructors retain native variable identities and argument types.
// Block/Damage preview hook pipelines are not migrated by these scalar definitions.
public class BlockVar : DynamicVar
{
    public const string defaultName = "Block";
    public ValueProp Props { get; }
    public BlockVar(decimal block, ValueProp props) : this("Block", block, props) { }
    public BlockVar(string name, decimal block, ValueProp props) : base(name, block) { Props = props; }
}
public class DamageVar : DynamicVar
{
    public const string defaultName = "Damage";
    public ValueProp Props { get; set; }
    public DamageVar(decimal damage, ValueProp props) : this("Damage", damage, props) { }
    public DamageVar(string name, decimal damage, ValueProp props) : base(name, damage) { Props = props; }
}
public class MaxHpVar : DynamicVar
{
    public const string defaultName = "MaxHp";
    public MaxHpVar(decimal maxHp) : this("MaxHp", maxHp) { }
    public MaxHpVar(string name, decimal maxHp) : base(name, maxHp) { }
}
public class HealVar : DynamicVar
{
    public const string defaultName = "Heal";
    public HealVar(decimal healAmount) : this("Heal", healAmount) { }
    public HealVar(string name, decimal healAmount) : base(name, healAmount) { }
}
public class CardsVar : DynamicVar
{
    public const string defaultName = "Cards";
    public CardsVar(int cards) : this("Cards", cards) { }
    public CardsVar(string name, int cards) : base(name, cards) { }
}
public class EnergyVar : DynamicVar
{
    public const string defaultName = "Energy";
    public string ColorPrefix { get; set; } = string.Empty;
    public EnergyVar(int energy) : this("Energy", energy) { }
    public EnergyVar(string name, int energy) : base(name, energy) { }
}
public class GoldVar : DynamicVar
{
    public const string defaultName = "Gold";
    public GoldVar(int gold) : this("Gold", gold) { }
    public GoldVar(string name, int gold) : base(name, gold) { }
}
public class RepeatVar : DynamicVar
{
    public const string defaultName = "Repeat";
    public RepeatVar(int times) : this("Repeat", times) { }
    public RepeatVar(string name, int times) : base(name, times) { }
}
public class HpLossVar : DynamicVar
{
    public const string defaultName = "HpLoss";
    public HpLossVar(decimal hpLoss) : this("HpLoss", hpLoss) { }
    public HpLossVar(string name, decimal hpLoss) : base(name, hpLoss) { }
}
public class ForgeVar : DynamicVar
{
    public const string defaultName = "Forge";
    public ForgeVar(int forge) : this("Forge", forge) { }
    public ForgeVar(string name, int forge) : base(name, forge) { }
}
public class StarsVar : DynamicVar
{
    public const string defaultName = "Stars";
    public StarsVar(int stars) : this("Stars", stars) { }
    public StarsVar(string name, int stars) : base(name, stars) { }
}
public class CalculationBaseVar(decimal baseValue) : DynamicVar("CalculationBase", baseValue)
{
    public const string defaultName = "CalculationBase";
}
public class CalculationExtraVar(decimal baseValue) : DynamicVar("CalculationExtra", baseValue)
{
    public const string defaultName = "CalculationExtra";
}
public class IntVar(string name, decimal amount) : DynamicVar(name, amount);
public class BoolVar : DynamicVar
{
    public bool BoolVal { get => Convert.ToBoolean(BaseValue); set => BaseValue = Convert.ToDecimal(value); }
    public BoolVar(string name) : base(name, 0m) { }
    public BoolVar(string name, bool value) : base(name, Convert.ToDecimal(value)) { }
}
public class StringVar(string name, string baseValue = "") : DynamicVar(name, 0m)
{
    public string StringValue { get; set; } = baseValue;
    public override string ToString() => StringValue;
}
