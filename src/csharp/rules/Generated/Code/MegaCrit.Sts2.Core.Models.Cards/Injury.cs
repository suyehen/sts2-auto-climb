// Generated migration unit; build and behavior verification are separate.
using System.Collections.Generic;
using AutoSlay.Rules.Native.Entities.Cards;

namespace AutoSlay.Rules.Native.Models.Cards;

public sealed class Injury : CardModel
{
	public override int MaxUpgradeLevel => 0;

	public override IEnumerable<CardKeyword> CanonicalKeywords => new CardKeyword[] { CardKeyword.Unplayable };

	public Injury(global::AutoSlay.Rules.Parameters.RuleParameters __ruleParameters)
		: base(__ruleParameters.Int32("MegaCrit.Sts2.Core.Models.Cards/Injury.cs::Injury.ctor.CanonicalEnergyCost::0"), CardType.Curse, CardRarity.Curse, TargetType.None,ruleParameters:__ruleParameters)
	{
	}
}
