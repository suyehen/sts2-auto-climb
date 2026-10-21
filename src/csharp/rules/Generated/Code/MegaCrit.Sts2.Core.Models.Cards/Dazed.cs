// Generated migration unit; build and behavior verification are separate.
using System.Collections.Generic;
using AutoSlay.Rules.Native.Entities.Cards;

namespace AutoSlay.Rules.Native.Models.Cards;

public sealed class Dazed : CardModel
{
	public override int MaxUpgradeLevel => 0;

	public override IEnumerable<CardKeyword> CanonicalKeywords => new CardKeyword[2]
	{
		CardKeyword.Ethereal,
		CardKeyword.Unplayable
	};

	public Dazed(global::AutoSlay.Rules.Parameters.RuleParameters __ruleParameters)
		: base(__ruleParameters.Int32("MegaCrit.Sts2.Core.Models.Cards/Dazed.cs::Dazed.ctor.CanonicalEnergyCost::0"), CardType.Status, CardRarity.Status, TargetType.None,ruleParameters:__ruleParameters)
	{
	}
}
