// Generated migration unit; build and behavior verification are separate.
using AutoSlay.Rules.Native.Entities.Powers;

namespace AutoSlay.Rules.Native.Models.Powers;

public sealed class MinionPower : PowerModel
{
	public override PowerType Type => PowerType.Buff;

	public override PowerStackType StackType => PowerStackType.Single;

	public override bool ShouldPlayVfx => false;

	public override bool OwnerIsSecondaryEnemy => true;

	public override bool ShouldPowerBeRemovedAfterOwnerDeath()
	{
		return false;
	}

	public override bool ShouldOwnerDeathTriggerFatal()
	{
		return false;
	}
}
