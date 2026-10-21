using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Rooms;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Native.ValueProps;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Native.Models.Singleton;

/// <summary>Native initialization, lifecycle and scaling rules on owned Run/Combat references.</summary>
public class MultiplayerScalingModel : SingletonModel
{
    private RunState? runState;
    private CombatState? combatState;
    public override bool ShouldReceiveCombatHooks => true;
    public void Initialize(RunState state)
    {
        if (runState != null) throw new InvalidOperationException("Already initialized");
        if (state.World != World) throw new RuleBoundaryException("foreign_state", "Scaling model/run world mismatch");
        runState = state;
    }
    public void OnCombatEntered(CombatState state)
    {
        if (state.RunState.World != World) throw new RuleBoundaryException("foreign_state", "Scaling model/combat world mismatch");
        combatState = state;
    }
    public void OnCombatFinished() => combatState = null;
    public override decimal ModifyBlockMultiplicative(Creature target, decimal block, ValueProp props, CardModel? cardSource, CardPlay? cardPlay)
    {
        if (target != null && !target.IsPrimaryEnemy && !target.IsSecondaryEnemy) return 1;
        if (!props.IsPoweredCardOrMonsterMoveBlock()) return 1;
        var state = runState ?? throw new RuleBoundaryException("state_incomplete", "Scaling model run state was not initialized");
        var count = state.Players.Count;
        if (count <= 2) return count;
        var combat = combatState ?? throw new RuleBoundaryException("state_incomplete", "Scaling model combat state was not observed");
        return count * GetMultiplayerScaling(combat.Encounter, state.CurrentActIndex);
    }
    public static decimal GetMultiplayerScaling(EncounterModel? encounter, int actIndex) => actIndex switch
    {
        0 => 1.1m,
        1 => 1.2m,
        2 => encounter != null && encounter.RoomType == RoomType.Boss ? 1.3m : 1.2m,
        _ => throw new ArgumentOutOfRangeException(nameof(actIndex), actIndex, "Invalid act index for HP scaling")
    };
}
