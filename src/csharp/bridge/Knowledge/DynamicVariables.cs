using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;

namespace AutoSlayBridge;

public static partial class Bridge
{
    // Type parameters describe the variable, not the command executed by a model.
    // FlexPotion's StrengthPower variable and FlexPotionPower effect must stay distinct.
    private static object ReferenceVariable(string name, DynamicVar variable)
    {
        var type = variable.GetType();
        var arguments = new List<Type>();
        for (var current = type; current != null && current != typeof(DynamicVar); current = current.BaseType)
            if (current.IsGenericType) arguments.AddRange(current.GetGenericArguments());
        return new {
            name, kind = type.Name, full_type = type.FullName,
            generic_arguments = arguments.Select(t => t.FullName).Distinct().ToArray(),
            variable_model_references = arguments.Where(t => typeof(AbstractModel).IsAssignableFrom(t))
                .Select(t => ModelDb.GetId(t).ToString()).Distinct().ToArray(),
            base_value = variable.BaseValue, enchanted_value = variable.EnchantedValue,
            decimal_base_value = variable.BaseValue.ToString(System.Globalization.CultureInfo.InvariantCulture),
            preview_value = variable.PreviewValue,
            value_props = variable is BlockVar block ? block.Props.ToString()
                : variable is DamageVar damage ? damage.Props.ToString() : null,
            effect_binding = "defined_by_model_commands_not_variable_type"
        };
    }
}
