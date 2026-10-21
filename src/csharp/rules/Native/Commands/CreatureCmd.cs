using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Hooks;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Native.Commands;

/// <summary>Own-state async command surface. Presentation is omitted; unsupported lifecycle paths fail explicitly.</summary>
public static class CreatureCmd
{
    public static async Task Heal(Creature creature, decimal amount, bool playAnim = true)
    {
        var world = creature.World; world.RequireExactScope(creature);
        if (world.CombatState?.IsEnding == true && !creature.IsPlayer) return;
        PreflightHeal(creature, amount);
        var hp = creature.CurrentHp; var maximum = creature.MaxHp; var block = creature.Block;
        var amountHealed = Math.Min(amount, creature.MaxHp - creature.CurrentHp);
        creature.HealInternal(amount);
        world.Record("CreatureCmd.Heal", creature, amount, hp, maximum, block);
        var history = creature.Player?.RunState.CurrentMapPointHistoryEntry;
        if (history != null && amountHealed > 0) history.GetEntry(creature.Player!.NetId).HpHealed += (int)amountHealed;
        if (amount > 0 && creature.CombatState != null)
            await Hook.AfterCurrentHpChanged(world.RunState, creature.CombatState, creature, amount);
    }
    public static Task<decimal> SetMaxHp(Creature creature, decimal amount)
    {
        var world = creature.World; world.RequireExactScope(creature);
        PreflightMaxHp(amount);
        var hp = creature.CurrentHp; var maximum = creature.MaxHp; var block = creature.Block;
        creature.SetMaxHpInternal(Math.Max(0m, amount));
        world.Record("CreatureCmd.SetMaxHp", creature, amount, hp, maximum, block);
        return Task.FromResult<decimal>(creature.MaxHp - maximum);
    }
    public static async Task GainMaxHp(Creature creature, decimal amount)
    {
        creature.World.RequireExactScope(creature);
        if (amount < 0) throw new ArgumentException("amount must be non-negative. Use LoseMaxHp for max HP loss.");
        var desired = (decimal)creature.MaxHp + amount; PreflightMaxHp(desired);
        var actual = Math.Min((int)desired, 999999999) - creature.MaxHp;
        // Known missing dependencies must fail before SetMaxHp/history mutate the branch.
        if (!(creature.World.CombatState?.IsEnding == true && !creature.IsPlayer)) PreflightHeal(creature, actual, Math.Min((int)desired, 999999999));
        RequireHistory(creature);
        var changed = await SetMaxHp(creature, desired);
        var history = creature.Player?.RunState.CurrentMapPointHistoryEntry;
        if (history != null) history.GetEntry(creature.Player!.NetId).MaxHpGained += (int)changed;
        await Heal(creature, changed);
    }
    public static async Task SetCurrentHp(Creature creature, decimal amount)
    {
        var world = creature.World; world.RequireExactScope(creature);
        if ((int)Math.Min(amount, creature.MaxHp) <= 0)
            throw new RuleBoundaryException("mechanism_not_implemented", "SetCurrentHp requires the unmigrated Kill pipeline");
        if (amount != creature.CurrentHp) _ = world.RunState.IterateHookListeners(creature.CombatState).ToArray();
        var hp = creature.CurrentHp; var maximum = creature.MaxHp; var block = creature.Block;
        creature.SetCurrentHpInternal(amount);
        world.Record("CreatureCmd.SetCurrentHp", creature, amount, hp, maximum, block);
        if (amount != hp) await Hook.AfterCurrentHpChanged(world.RunState, creature.CombatState, creature, amount - hp);
    }
    private static void PreflightMaxHp(decimal amount)
    {
        if ((int)Math.Max(0m, amount) <= 0)
            throw new RuleBoundaryException("mechanism_not_implemented", "SetMaxHp requires the unmigrated Kill pipeline when integer maximum reaches zero");
    }
    private static void PreflightHeal(Creature creature, decimal amount, int? maxHpAfter = null)
    {
        RequireHistory(creature);
        if (creature.IsDead && (int)Math.Min(creature.CurrentHp + amount, maxHpAfter ?? creature.MaxHp) > 0)
            throw new RuleBoundaryException("mechanism_not_implemented", "Revival listener activation is not migrated");
        if (amount > 0 && creature.CombatState != null)
            _ = creature.World.RunState.IterateHookListeners(creature.CombatState).ToArray();
    }
    private static void RequireHistory(Creature creature)
    {
        if (creature.Player == null) return;
        creature.Player.RunState.RequireHistory();
        _ = creature.Player.RunState.CurrentMapPointHistoryEntry?.GetEntry(creature.Player.NetId);
    }
}
