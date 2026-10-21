using AutoSlay.Rules.Native.Localization.DynamicVars;
using AutoSlay.Rules.Native.ValueProps;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.State;

public sealed record CardVariableObservation(string Name, string Type, decimal Base, decimal Enchanted, decimal Preview, bool WasJustUpgraded, int? Props = null);
public sealed record TemporaryStarCostObservation(int Cost, bool ClearsWhenTurnEnds, bool ClearsWhenCardIsPlayed);
public sealed record CardStarCostObservation(int Base, bool BaseWasSet, bool WasJustUpgraded, int LastStarsSpent, TemporaryStarCostObservation[] TemporaryCosts);

internal static class CardRuleHydrator
{
    // These are migrated variable mechanisms, not card IDs or effect bindings. New variable behavior
    // needs a real port before an observation may pretend three scalar values describe its state.
    private static readonly HashSet<Type> ScalarTypes = [typeof(DynamicVar), typeof(BlockVar), typeof(DamageVar), typeof(MaxHpVar),
        typeof(HealVar), typeof(CardsVar), typeof(EnergyVar), typeof(GoldVar), typeof(RepeatVar), typeof(HpLossVar), typeof(ForgeVar),
        typeof(StarsVar), typeof(CalculationBaseVar), typeof(CalculationExtraVar), typeof(IntVar), typeof(CalculatedVar)];
    internal static void RestoreVariables(DynamicVarSet target, IReadOnlyCollection<CardVariableObservation> values)
    {
        if (values == null || values.Any(v => v == null || string.IsNullOrWhiteSpace(v.Name) || string.IsNullOrWhiteSpace(v.Type))
            || values.Count != target.Count || values.Select(v => v.Name).Distinct(StringComparer.Ordinal).Count() != values.Count)
            throw new RuleBoundaryException("invalid_state", "Card variable observation does not cover the declared set");
        foreach (var value in values)
        {
            if (!target.TryGetValue(value.Name, out var variable) || value.Type != variable.GetType().FullName
                || value.Base > 999999999m || value.Enchanted > 999999999m || value.Preview > 999999999m)
                throw new RuleBoundaryException("invalid_state", "Card variable type/name/value differs from the compiled variable contract");
            if (!ScalarTypes.Contains(variable.GetType())) throw new RuleBoundaryException("mechanism_not_implemented", "Variable state layout is not migrated: " + value.Type);
            if (variable is BlockVar block ? value.Props != (int)block.Props : variable is DamageVar ? value.Props == null : value.Props != null)
                throw new RuleBoundaryException("invalid_state", "Card variable props do not match its mechanism");
        }
        foreach (var value in values)
        {
            var variable = target[value.Name]; variable.RestoreScalarState(value.Base, value.Enchanted, value.Preview, value.WasJustUpgraded);
            if (variable is DamageVar damage) damage.Props = (ValueProp)value.Props!.Value;
        }
    }
    internal static void ValidateStars(CardStarCostObservation state)
    {
        if (state == null || state.TemporaryCosts == null || state.TemporaryCosts.Any(cost => cost == null || cost.Cost < 0
            || cost.ClearsWhenTurnEnds && !cost.ClearsWhenCardIsPlayed))
            throw new RuleBoundaryException("invalid_state", "Invalid native temporary star-cost observation");
    }
}
