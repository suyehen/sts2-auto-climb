using System.Reflection;

namespace AutoSlayBridge;

// Read primitive instance fields only. Never invoke arbitrary property getters or RNG.
internal static class RuntimeFields
{
    // Observed card-play progress, not presentation counters or future RNG.
    public static object ReadRelicCounters(object? model)
    {
        var values = new Dictionary<string, object>();
        var missing = new List<string>();
        var names = new HashSet<string> { "_attacksPlayed", "_attacksPlayedThisTurn", "_skillsPlayedThisTurn", "_activatedThisCombat" };
        for (var type = model?.GetType(); type != null && type.Name != "RelicModel"; type = type.BaseType)
        {
            foreach (var field in type.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
            {
                if (field.FieldType != typeof(int) && field.FieldType != typeof(bool) || !names.Contains(field.Name) || values.ContainsKey(field.Name)) continue;
                try { values[field.Name] = field.GetValue(model)!; }
                catch { missing.Add(field.Name); }
            }
        }
        return new { fields = values, unavailable_fields = missing, source = "observed_relic_progress" };
    }

    public static object Read(object? model)
    {
        var values = new Dictionary<string, object?>();
        var missing = new List<string>();
        for (var type = model?.GetType(); type != null && type.Name != "MonsterModel" && type.Name != "EventModel"; type = type.BaseType)
        {
            foreach (var field in type.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
            {
                var scalar = Nullable.GetUnderlyingType(field.FieldType) ?? field.FieldType;
                if (scalar != typeof(bool) && scalar != typeof(int) && scalar != typeof(decimal)
                    && scalar != typeof(float) && scalar != typeof(double)) continue;
                var name = field.Name;
                if (name.StartsWith('<') && name.Contains(">k__BackingField")) name = name[1..name.IndexOf('>')];
                if (values.ContainsKey(name)) continue;
                try
                {
                    var value = field.GetValue(model);
                    if (value is float f && !float.IsFinite(f) || value is double d && !double.IsFinite(d))
                        missing.Add(name);
                    else values[name] = value;
                }
                catch { missing.Add(name); }
            }
        }
        return new { fields = values, unavailable_fields = missing, source = "game_instance_fields" };
    }
}
