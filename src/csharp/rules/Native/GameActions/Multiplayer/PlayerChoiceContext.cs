using AutoSlay.Rules.Native.Entities.Multiplayer;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Models;

namespace AutoSlay.Rules.Native.GameActions.Multiplayer;

public abstract class PlayerChoiceContext
{
    private Stack<AbstractModel>? modelStack;
    public IEnumerable<AbstractModel>? ModelStack => modelStack;
    public AbstractModel? LastInvolvedModel => modelStack?.TryPeek(out var model) == true ? model : null;
    public abstract ulong? OwnerId { get; }
    public void PushModel(AbstractModel model) => (modelStack ??= new()).Push(model);
    public void PopModel(AbstractModel model)
    {
        // Native mismatches only log a diagnostic and leave the stack intact. Logging is omitted.
        if (modelStack?.TryPeek(out var current) == true && current == model) modelStack.Pop();
    }
    public abstract Task SignalPlayerChoiceBegun(Player chooser, PlayerChoiceOptions options);
    public abstract Task SignalPlayerChoiceEnded();
}
/// <summary>Native no-choice context. This is not a choice solver or a resumable continuation.</summary>
public class ThrowingPlayerChoiceContext : PlayerChoiceContext
{
    public override ulong? OwnerId => null;
    public override Task SignalPlayerChoiceBegun(Player chooser, PlayerChoiceOptions options) => throw new NotImplementedException();
    public override Task SignalPlayerChoiceEnded() => throw new NotImplementedException();
}
