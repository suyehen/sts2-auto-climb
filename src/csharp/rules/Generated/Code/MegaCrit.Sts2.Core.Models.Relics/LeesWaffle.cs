// Generated migration unit; build and behavior verification are separate.
using System.Collections.Generic;
using System.Threading.Tasks;
using AutoSlay.Rules.Native.Commands;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Localization.DynamicVars;

namespace AutoSlay.Rules.Native.Models.Relics;

public sealed class LeesWaffle : RelicModel
{
	public override RelicRarity Rarity => RelicRarity.Shop;

	public override bool HasUponPickupEffect => true;

	protected override IEnumerable<DynamicVar> CanonicalVars => new DynamicVar[] { new MaxHpVar(Parameters.Decimal("MegaCrit.Sts2.Core.Models.Relics/LeesWaffle.cs::LeesWaffle.CanonicalVars::0")) };

	public override async Task AfterObtained()
	{
		Creature creature = base.Owner.Creature;
		await CreatureCmd.GainMaxHp(creature, base.DynamicVars.MaxHp.BaseValue);
		await CreatureCmd.Heal(creature, creature.MaxHp - creature.CurrentHp);
	}
}
