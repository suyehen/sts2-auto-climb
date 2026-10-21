namespace AutoSlay.Rules.Native.Localization.DynamicVars;

/// <summary>
/// Own-state numeric/upgrade semantics from native DynamicVar. Preview hooks and formatted text are
/// intentionally absent here; no execution may substitute PreviewValue for BaseValue.
/// </summary>
public class DynamicVar : IConvertible
{
    protected AutoSlay.Rules.Native.Models.AbstractModel? _owner;
    private decimal baseValue;
    private decimal enchantedValue;
    private decimal previewValue;
    internal bool HasCanonicalValueWrites { get; private set; }
    private void ObserveCanonicalWrite() { if (_owner?.IsCanonical == true) HasCanonicalValueWrites = true; }
    public string Name { get; }
    public decimal BaseValue
    {
        get => baseValue;
        set { ObserveCanonicalWrite(); baseValue = Math.Min(value, 999999999m); ResetToBase(); }
    }
    public decimal EnchantedValue { get => enchantedValue; set { ObserveCanonicalWrite(); enchantedValue = Math.Min(value, 999999999m); } }
    public decimal PreviewValue { get => previewValue; set { ObserveCanonicalWrite(); previewValue = Math.Min(value, 999999999m); } }
    public bool WasJustUpgraded { get; protected set; }
    public int IntValue => (int)BaseValue;
    public DynamicVar(string name, decimal baseValue) { Name = name; BaseValue = baseValue; ResetToBase(); }
    public void ResetToBase() { EnchantedValue = BaseValue; PreviewValue = BaseValue; }
    public virtual void SetOwner(AutoSlay.Rules.Native.Models.AbstractModel owner) => _owner = owner;
    internal void RestoreScalarState(decimal value, decimal enchanted, decimal preview, bool justUpgraded)
    {
        BaseValue = value; EnchantedValue = enchanted; PreviewValue = preview; WasJustUpgraded = justUpgraded;
    }
    public void UpgradeValueBy(decimal addend) { BaseValue += addend; WasJustUpgraded = true; }
    public void FinalizeUpgrade() => WasJustUpgraded = false;
    public DynamicVar Clone() { var result = (DynamicVar)MemberwiseClone(); result.ResetToBase(); return result; }
    public override string ToString() => IntValue.ToString(System.Globalization.CultureInfo.InvariantCulture);
    protected virtual decimal GetBaseValueForIConvertible() => BaseValue;
    public TypeCode GetTypeCode() => TypeCode.Object;
    public bool ToBoolean(IFormatProvider? provider) => throw new InvalidCastException($"Cannot convert {BaseValue} to Boolean");
    public byte ToByte(IFormatProvider? provider) => Convert.ToByte(GetBaseValueForIConvertible(), provider);
    public char ToChar(IFormatProvider? provider) => throw new InvalidCastException($"Cannot convert {BaseValue} to Char");
    public DateTime ToDateTime(IFormatProvider? provider) => throw new InvalidCastException($"Cannot convert {BaseValue} to DateTime");
    public decimal ToDecimal(IFormatProvider? provider) => GetBaseValueForIConvertible();
    public double ToDouble(IFormatProvider? provider) => Convert.ToDouble(GetBaseValueForIConvertible(), provider);
    public short ToInt16(IFormatProvider? provider) => Convert.ToInt16(GetBaseValueForIConvertible(), provider);
    public int ToInt32(IFormatProvider? provider) => Convert.ToInt32(GetBaseValueForIConvertible(), provider);
    public long ToInt64(IFormatProvider? provider) => Convert.ToInt64(GetBaseValueForIConvertible(), provider);
    public sbyte ToSByte(IFormatProvider? provider) => Convert.ToSByte(GetBaseValueForIConvertible(), provider);
    public float ToSingle(IFormatProvider? provider) => Convert.ToSingle(GetBaseValueForIConvertible(), provider);
    public string ToString(IFormatProvider? provider) => GetBaseValueForIConvertible().ToString(provider);
    public object ToType(Type conversionType, IFormatProvider? provider) => Convert.ChangeType(GetBaseValueForIConvertible(), conversionType, provider);
    public ushort ToUInt16(IFormatProvider? provider) => Convert.ToUInt16(GetBaseValueForIConvertible(), provider);
    public uint ToUInt32(IFormatProvider? provider) => Convert.ToUInt32(GetBaseValueForIConvertible(), provider);
    public ulong ToUInt64(IFormatProvider? provider) => Convert.ToUInt64(GetBaseValueForIConvertible(), provider);
}
