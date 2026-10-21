using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Models;

namespace AutoSlay.Rules.Native.Localization.DynamicVars;

/// <summary>Native calculation/owner/upgrade members. Card display preview and calculated damage/block specializations remain absent.</summary>
public class CalculatedVar : DynamicVar
{
    private Func<CardModel, Creature?, decimal>? multiplierCalc;
    public CalculatedVar(string name) : base(name, 0m) { }
    public override void SetOwner(AbstractModel owner)
    {
        base.SetOwner(owner);
        if (_owner != null) BaseValue = GetBaseVar().BaseValue;
    }
    public CalculatedVar WithMultiplier(Func<CardModel, Creature?, decimal> calculation)
    {
        if (multiplierCalc != null) throw new InvalidOperationException($"Tried to set extra multiplier calc on {this} twice!");
        if (calculation.Target is AbstractModel) throw new InvalidOperationException("Multiplier calc must be static!");
        multiplierCalc = calculation; return this;
    }
    public decimal Calculate(Creature? target)
    {
        if (multiplierCalc == null) throw new InvalidOperationException("Extra multiplier calc must be specified!");
        var card = (CardModel)_owner!;
        var multiplier = card.World.CombatState?.IsInProgress == true && card.CombatState != null ? multiplierCalc(card, target) : 0m;
        return GetBaseVar().BaseValue + GetExtraVar().BaseValue * multiplier;
    }
    public void RecalculateForUpgradeOrEnchant()
    {
        var value = GetBaseVar().BaseValue;
        if (value != BaseValue) WasJustUpgraded = true;
        BaseValue = value;
    }
    protected virtual DynamicVar GetBaseVar() => ((CardModel)_owner!).DynamicVars.CalculationBase;
    protected virtual DynamicVar GetExtraVar() => ((CardModel)_owner!).DynamicVars.CalculationExtra;
    protected override decimal GetBaseValueForIConvertible() => Calculate(null);
    public override string ToString() => Calculate(null).ToString();
}
