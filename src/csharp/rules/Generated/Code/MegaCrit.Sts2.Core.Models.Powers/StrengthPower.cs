// Generated migration unit; build and behavior verification are separate.
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Powers;
using AutoSlay.Rules.Native.ValueProps;

namespace AutoSlay.Rules.Native.Models.Powers;

public sealed class StrengthPower : PowerModel
{
	public override PowerType Type => PowerType.Buff;

	public override PowerStackType StackType => PowerStackType.Counter;

	public override bool AllowNegative => true;

	public override decimal ModifyDamageAdditive(Creature? target, decimal amount, ValueProp props, Creature? dealer, CardModel? cardSource, CardPlay? cardPlay)
	{
		if (base.Owner != dealer)
		{
			return 0m;
		}
		if (!props.IsPoweredAttack())
		{
			return 0m;
		}
		return base.Amount;
	}
}
