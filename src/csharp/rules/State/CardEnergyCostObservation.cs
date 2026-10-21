using System.Reflection;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.State;

public sealed record LocalCostObservation(int Amount, LocalCostType Type, LocalCostModifierExpiration Expiration, bool IsReduceOnly);
public sealed record CardEnergyCostObservation(int Base, int CapturedXValue, bool WasJustUpgraded, LocalCostObservation[] LocalModifiers);

/// <summary>
/// Hydrates the owned, source-synchronized cost object without invoking cost-change callbacks.
/// The four compiled field contracts are fixed here; observations cannot select fields or code.
/// Native cost algorithms remain entirely in the unmodified generated source unit.
/// </summary>
internal static class CardEnergyCostHydrator
{
    private static FieldInfo Field(string name, Type expected)
    {
        var field = typeof(CardEnergyCost).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
        return field?.FieldType == expected ? field : throw new RuleBoundaryException("state_contract_missing", "Compiled card cost layout changed: " + name);
    }
    internal static void Restore(CardEnergyCost target, CardEnergyCostObservation observed)
    {
        if (observed == null || observed.LocalModifiers == null || observed.LocalModifiers.Any(m => m == null
            || m.Type is not (LocalCostType.Absolute or LocalCostType.Relative)
            || ((int)m.Expiration & ~6) != 0))
            throw new RuleBoundaryException("invalid_state", "Invalid local card cost observation");
        var baseField = Field("_base", typeof(int)); var captured = Field("_capturedXValue", typeof(int));
        var upgraded = Field("<WasJustUpgraded>k__BackingField", typeof(bool));
        var modifiers = Field("_localModifiers", typeof(List<LocalCostModifier>));
        baseField.SetValue(target, observed.Base); captured.SetValue(target, observed.CapturedXValue);
        upgraded.SetValue(target, observed.WasJustUpgraded);
        modifiers.SetValue(target, observed.LocalModifiers.Select(m => new LocalCostModifier(m.Amount, m.Type, m.Expiration, m.IsReduceOnly)).ToList());
    }
}
