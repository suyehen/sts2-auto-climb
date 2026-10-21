using System.Text.RegularExpressions;
using System.Text.Json;

namespace AutoSlay.Rules.Native.Models;

public sealed record ModelId : IComparable<ModelId>
{
    public string Category { get; }
    public string Entry { get; }
    public static readonly ModelId none = new("NONE", "NONE");
    public ModelId(string category, string entry)
    {
        if (category.EndsWith("_MODEL", StringComparison.Ordinal)) throw new ArgumentException("Category cannot end with '_MODEL'.", nameof(category));
        Category = category; Entry = entry;
    }
    public static ModelId Deserialize(string json)
    {
        var parts = json.Split('.');
        return parts.Length == 2 ? new(parts[0], parts[1]) : throw new JsonException("Invalid ModelId: " + json);
    }
    public override string ToString() => Category + "." + Entry;
    public int CompareTo(ModelId? other)
    {
        var category = string.Compare(Category, other?.Category, StringComparison.Ordinal);
        return category != 0 ? category : string.Compare(Entry, other?.Entry, StringComparison.Ordinal);
    }
    public static string SlugifyCategory(string category)
    {
        var result = Slugify(category);
        return result.EndsWith("_MODEL", StringComparison.Ordinal) ? result[..^6] : result;
    }
    // Same patterns and replacement order as the native StringHelper.Slugify; no engine dependency.
    internal static string Slugify(string text)
    {
        text = Regex.Replace(text.Trim(), "([A-Za-z0-9]|\\G(?!^))([A-Z])", "$1_$2");
        text = Regex.Replace(text.ToUpperInvariant(), "\\s+", "_");
        return Regex.Replace(text, "[^A-Z0-9_]", "");
    }
}
