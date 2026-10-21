// Generated migration unit; build and behavior verification are separate.
using System.Collections.Generic;
using System.Threading.Tasks;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.GameActions.Multiplayer;

namespace AutoSlay.Rules.Native.Models.Cards;

public sealed class Enlightenment : CardModel
{
	public override IEnumerable<CardKeyword> CanonicalKeywords => new CardKeyword[] { CardKeyword.Exhaust };

	public Enlightenment(global::AutoSlay.Rules.Parameters.RuleParameters __ruleParameters)
		: base(__ruleParameters.Int32("MegaCrit.Sts2.Core.Models.Cards/Enlightenment.cs::Enlightenment.ctor.CanonicalEnergyCost::0"), CardType.Skill, CardRarity.Event, TargetType.Self,ruleParameters:__ruleParameters)
	{
	}

	protected override Task OnPlay(PlayerChoiceContext choiceContext, CardPlay cardPlay)
	{
		foreach (CardModel card in PileType.Hand.GetPile(base.Owner).Cards)
		{
			if (base.IsUpgraded)
			{
				card.EnergyCost.SetThisCombat(1, reduceOnly: true);
			}
			else
			{
				card.EnergyCost.SetThisTurnOrUntilPlayed(1, reduceOnly: true);
			}
		}
		return Task.CompletedTask;
	}
}
