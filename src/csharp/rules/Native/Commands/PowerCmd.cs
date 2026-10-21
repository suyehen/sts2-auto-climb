using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Powers;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.GameActions.Multiplayer;
using AutoSlay.Rules.Native.Hooks;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Native.Commands;

/// <summary>Native command order on owned state. Missing state domains fail before rule callbacks.</summary>
public static class PowerCmd
{
    public static async Task<IReadOnlyList<T>> Apply<T>(PlayerChoiceContext choiceContext, IEnumerable<Creature>? targets, decimal amount, Creature? applier, CardModel? cardSource, bool silent = false) where T : PowerModel
    {
        var applied = new List<T>();
        if (targets == null) return applied;
        foreach (var target in targets)
        {
            var power = await Apply<T>(choiceContext, target, amount, applier, cardSource, silent);
            if (power != null) applied.Add(power);
        }
        return applied;
    }
    public static async Task<T?> Apply<T>(PlayerChoiceContext choiceContext, Creature target, decimal amount, Creature? applier, CardModel? cardSource, bool silent = false) where T : PowerModel
    {
        var world = target.World; world.RequireExactScope(target);
        if (world.CombatState?.IsEnding == true) return null;
        RequireHookDomains(target, applier);
        if (!target.CanReceivePowers) return null;
        var canonical = world.Models.Power<T>();
        var power = FindExistingInstanceForStacking(canonical, target, applier);
        if (power == null)
        {
            if (amount != 0) Preflight(target, applier, cardSource);
            power = canonical.ToMutable(); world.RegisterPowerForCommand(power);
            await Apply(choiceContext, power, target, amount, applier, cardSource, silent);
        }
        else if (await ModifyAmount(choiceContext, power, amount, applier, cardSource, silent) == 0) power = null;
        // Native generic Apply returns a newly created object even when its application was blocked.
        return power as T;
    }
    public static async Task Apply(PlayerChoiceContext choiceContext, PowerModel power, Creature target, decimal amount, Creature? applier, CardModel? cardSource, bool silent = false)
    {
        var world = target.World; world.RequireExactScope(target);
        if (power.World != world) throw new RuleBoundaryException("foreign_state", "Power and target belong to different worlds");
        if (world.CombatState?.IsEnding == true || amount == 0) return;
        RequireHookDomains(target, applier);
        if (!target.CanReceivePowers) return;
        var combat = target.CombatState;
        if (combat == null) return;
        var existing = FindExistingInstanceForStacking(power, target, applier);
        if (existing != null)
        {
            // Native instance overload intentionally does not forward silent on this branch.
            await ModifyAmount(choiceContext, existing, amount, applier, cardSource); return;
        }
        Preflight(target, applier, cardSource);
        world.RegisterPowerForCommand(power); power.Applier = applier;
        await Hook.BeforePowerAmountChanged(combat, power, amount, target, applier, cardSource);
        var modified = amount; IEnumerable<AbstractModel>? given = null;
        if (applier != null && combat.ContainsCreature(applier))
            modified = Hook.ModifyPowerAmountGiven(combat, power, applier, modified, target, cardSource, out given);
        modified = Hook.ModifyPowerAmountReceived(combat, power, target, modified, applier, out var received);
        if (combat.Players.Count > 1 && (target.IsPrimaryEnemy || target.IsSecondaryEnemy) && power.ShouldScaleInMultiplayer)
            modified = power.GetScaledAmountForMultiplayer(combat, applier, modified, target, cardSource);
        using (world.EnterModel(power)) await power.BeforeApplied(target, modified, applier, cardSource);
        if (target.CanReceivePowers)
        {
            power.ApplyInternal(target, modified, silent);
            if (modified != 0) combat.History.PowerReceived(combat, power, modified, applier);
            // Pure visual scaled waits are omitted; no rule callbacks are removed or reordered.
            if (target.Side == CombatSide.Player && power.Type == PowerType.Debuff) power.SkipNextDurationTick = true;
            if (given != null) await Hook.AfterModifyingPowerAmountGiven(combat, given, power);
            await Hook.AfterModifyingPowerAmountReceived(combat, received, power);
            if (modified != 0)
            {
                using (world.EnterModel(power)) await power.AfterApplied(applier, cardSource);
                await Hook.AfterPowerAmountChanged(combat, choiceContext, power, modified, applier, cardSource);
            }
        }
    }
    public static async Task<int> ModifyAmount(PlayerChoiceContext choiceContext, PowerModel power, decimal offset, Creature? applier, CardModel? cardSource, bool silent = false)
    {
        var world = power.World; world.RequireExecution();
        if (world.CombatState?.IsEnding == true) return 0;
        var owner = power.Owner ?? throw new RuleBoundaryException("invalid_state", "Cannot modify an unapplied power");
        world.RequireOwn(owner);
        var combat = owner.CombatState;
        if (combat == null) return 0;
        Preflight(owner, applier, cardSource); world.RegisterPowerForCommand(power);
        await Hook.BeforePowerAmountChanged(combat, power, offset, owner, applier, cardSource);
        var modified = offset; IEnumerable<AbstractModel>? given = null;
        if (applier != null && combat.ContainsCreature(applier))
            modified = Hook.ModifyPowerAmountGiven(combat, power, applier, modified, owner, cardSource, out given);
        modified = Hook.ModifyPowerAmountReceived(combat, power, owner, modified, applier, out var received);
        combat.History.PowerReceived(combat, power, modified, applier);
        // Preserve native offset truncation before adding to current amount, and its unclamped return.
        var newAmount = unchecked(power.Amount + (int)modified); power.SetAmount(newAmount, silent);
        if (given != null) await Hook.AfterModifyingPowerAmountGiven(combat, given, power);
        await Hook.AfterModifyingPowerAmountReceived(combat, received, power);
        if ((int)modified != 0) await Hook.AfterPowerAmountChanged(combat, choiceContext, power, modified, applier, cardSource);
        if (power.ShouldRemoveDueToAmount()) await Remove(power);
        // Intent refresh via NCreature is omitted with the presentation layer. Native move-state
        // generation and all rule consumers of intents remain outside the currently migrated scope.
        return newAmount;
    }
    public static async Task Decrement(PowerModel power) => await ModifyAmount(new ThrowingPlayerChoiceContext(), power, -1, null, null);
    public static async Task TickDownDuration(PowerModel power)
    {
        power.World.RequireExecution(); power.World.RegisterPowerForCommand(power);
        if (power.SkipNextDurationTick) power.SkipNextDurationTick = false;
        else await Decrement(power);
    }
    public static async Task Remove<T>(Creature creature) where T : PowerModel
    { creature.World.RequireExactScope(creature); await Remove(creature.GetPower<T>()); }
    public static async Task Remove(PowerModel? power)
    {
        if (power == null) return;
        var world = power.World; world.RequireExecution(); world.RegisterPowerForCommand(power);
        if (power.Owner == null) throw new RuleBoundaryException("invalid_state", "Cannot remove an unapplied power");
        world.RequireOwn(power.Owner); power.RemoveInternal();
        using (world.EnterModel(power)) await power.AfterRemoved(power.Owner);
    }
    private static void Preflight(Creature target, Creature? applier, CardModel? cardSource)
    {
        RequireHookDomains(target, applier);
        if (cardSource != null) throw new RuleBoundaryException("mechanism_not_implemented", "Card-source state is not migrated");
        if (target.CombatState is { } combat) combat.History.RequireObserved(combat);
    }
    private static void RequireHookDomains(Creature target, Creature? applier)
    {
        var world = target.World; world.RequireExactScope(target);
        if (applier != null) world.RequireOwn(applier);
        if (target.CombatState is not { } combat) return;
        if (combat.RunState.World != world) throw new RuleBoundaryException("foreign_state", "Foreign combat state");
        (combat.HookInventory ?? throw new RuleBoundaryException("state_incomplete", "Native hook domains are required for Power commands")).RequireResolved();
    }
    public static PowerModel? FindExistingInstanceForStacking(PowerModel basePower, Creature target, Creature? applier) =>
        basePower.InstanceType switch
        {
            PowerInstanceType.Instanced => null,
            PowerInstanceType.InstancedPerApplier => target.GetPowerInstances(basePower.Id).FirstOrDefault(p => p.Applier == applier),
            PowerInstanceType.None => target.GetPower(basePower.Id),
            _ => throw new ArgumentOutOfRangeException("InstanceType")
        };
}
