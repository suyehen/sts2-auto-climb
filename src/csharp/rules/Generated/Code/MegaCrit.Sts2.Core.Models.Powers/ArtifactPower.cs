// Generated migration unit; build and behavior verification are separate.
using System.Threading.Tasks;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Commands;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Powers;

namespace AutoSlay.Rules.Native.Models.Powers;

public sealed class ArtifactPower : PowerModel
{
	public override PowerType Type => PowerType.Buff;

	public override PowerStackType StackType => PowerStackType.Counter;

	public override bool ShouldScaleInMultiplayer => true;

	public override bool TryModifyPowerAmountReceived(PowerModel canonicalPower, Creature target, decimal amount, Creature? _, out decimal modifiedAmount)
	{
		if (target != base.Owner)
		{
			modifiedAmount = amount;
			return false;
		}
		if (canonicalPower.GetTypeForAmount(amount) != PowerType.Debuff)
		{
			modifiedAmount = amount;
			return false;
		}
		if (!canonicalPower.IsVisible)
		{
			modifiedAmount = amount;
			return false;
		}
		modifiedAmount = default(decimal);
		return true;
	}

	public override async Task AfterModifyingPowerAmountReceived(PowerModel power)
	{
		await PowerCmd.Decrement(this);
	}

	public override decimal GetScaledAmountForMultiplayer(ICombatState combatState, Creature? applier, decimal amount, Creature target, CardModel? cardSource)
	{
		return amount + (decimal)combatState.Players.Count - 1m;
	}
}
