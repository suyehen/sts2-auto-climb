using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Commands;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Powers;
using AutoSlay.Rules.Native.GameActions.Multiplayer;

namespace AutoSlay.Rules.Native.Models.Powers;

/// <summary>Native temporary-strength rule members. Localization/hover-tip members are omitted.</summary>
public abstract class TemporaryStrengthPower : PowerModel, ITemporaryPower
{
    public override PowerType Type => IsPositive ? PowerType.Buff : PowerType.Debuff;
    public override PowerStackType StackType => PowerStackType.Counter;
    public abstract AbstractModel OriginModel { get; }
    public PowerModel InternallyAppliedPower => ModelDb.Power<StrengthPower>();
    protected virtual bool IsPositive => true;
    private int Sign => IsPositive ? 1 : -1;
    public override async Task BeforeApplied(Creature target, decimal amount, Creature? applier, CardModel? cardSource)
    {
        await PowerCmd.Apply<StrengthPower>(new ThrowingPlayerChoiceContext(), target, Sign * amount, applier, cardSource, silent: true);
    }
    public override async Task AfterPowerAmountChanged(PlayerChoiceContext choiceContext, PowerModel power, decimal amount, Creature? applier, CardModel? cardSource)
    {
        if (amount != Amount && power == this)
            await PowerCmd.Apply<StrengthPower>(choiceContext, Owner, Sign * amount, applier, cardSource, silent: true);
    }
    public override async Task AfterSideTurnEnd(PlayerChoiceContext choiceContext, CombatSide side, IEnumerable<Creature> participants)
    {
        if (participants.Contains(Owner))
        {
            Flash(); await PowerCmd.Remove(this);
            await PowerCmd.Apply<StrengthPower>(choiceContext, Owner, -Sign * Amount, Owner, null);
        }
    }
}
