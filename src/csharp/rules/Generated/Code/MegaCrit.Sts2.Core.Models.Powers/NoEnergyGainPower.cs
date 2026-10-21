// Generated migration unit; build and behavior verification are separate.
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Commands;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Entities.Powers;
using AutoSlay.Rules.Native.GameActions.Multiplayer;

namespace AutoSlay.Rules.Native.Models.Powers;

public sealed class NoEnergyGainPower : PowerModel
{
	public override PowerType Type => PowerType.Debuff;

	public override PowerStackType StackType => PowerStackType.Single;

	public override decimal ModifyEnergyGain(Player player, decimal amount)
	{
		if (player != base.Owner.Player)
		{
			return amount;
		}
		return 0m;
	}

	public override Task AfterModifyingEnergyGain()
	{
		Flash();
		return Task.CompletedTask;
	}

	public override async Task AfterSideTurnEnd(PlayerChoiceContext choiceContext, CombatSide side, IEnumerable<Creature> participants)
	{
		if (participants.Contains(base.Owner))
		{
			await PowerCmd.Remove(this);
		}
	}
}
