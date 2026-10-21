// Generated migration unit; build and behavior verification are separate.
using System.Collections.Generic;
using System.Threading.Tasks;
using AutoSlay.Rules.Native.Commands;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Localization.DynamicVars;

namespace AutoSlay.Rules.Native.Models.Relics;

public sealed class Pear : RelicModel
{
	public override RelicRarity Rarity => RelicRarity.Uncommon;

	public override bool HasUponPickupEffect => true;

	protected override IEnumerable<DynamicVar> CanonicalVars => new DynamicVar[] { new MaxHpVar(Parameters.Decimal("MegaCrit.Sts2.Core.Models.Relics/Pear.cs::Pear.CanonicalVars::0")) };

	public override async Task AfterObtained()
	{
		await CreatureCmd.GainMaxHp(base.Owner.Creature, base.DynamicVars.MaxHp.BaseValue);
	}
}
