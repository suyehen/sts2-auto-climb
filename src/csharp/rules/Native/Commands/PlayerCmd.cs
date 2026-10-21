using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Hooks;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Native.Commands;

/// <summary>Native energy/star command members. Sound is omitted; gold, pets and rest commands are not migrated APIs.</summary>
public static class PlayerCmd
{
    public static async Task GainEnergy(decimal amount, Player player)
    {
        var world = player.RunState.World; world.RequireExactScope(player.Creature);
        if (amount <= 0 || world.CombatState?.IsEnding == true) return;
        var combat = player.Creature.CombatState ?? throw new RuleBoundaryException("state_incomplete", "Energy gain requires owned combat context");
        var finalAmount = Hook.ModifyEnergyGain(combat, player, amount, out var modifiers);
        await Hook.AfterModifyingEnergyGain(combat, modifiers);
        if (finalAmount > 0) RequireCombatState(player).GainEnergy(finalAmount);
    }
    public static Task LoseEnergy(decimal amount, Player player)
    {
        var world = player.RunState.World; world.RequireExactScope(player.Creature);
        if (amount <= 0 || world.CombatState?.IsEnding == true) return Task.CompletedTask;
        RequireCombatState(player).LoseEnergy(amount); return Task.CompletedTask;
    }
    public static async Task SetEnergy(decimal amount, Player player)
    {
        var world = player.RunState.World; world.RequireExactScope(player.Creature);
        if (world.CombatState?.IsEnding == true) return;
        var energy = RequireCombatState(player).Energy;
        if (energy < amount) await GainEnergy(amount - energy, player);
        else if (energy > amount) await LoseEnergy(energy - amount, player);
    }
    public static async Task GainStars(decimal amount, Player player)
    {
        var world = player.RunState.World; world.RequireExactScope(player.Creature);
        if (world.CombatState?.IsEnding == true) return;
        var combat = player.Creature.CombatState ?? throw new RuleBoundaryException("state_incomplete", "Star gain requires owned combat context");
        if (Hook.ShouldGainStars(combat, amount, player))
        {
            RequireCombatState(player).GainStars(amount);
            await Hook.AfterStarsGained(combat, (int)amount, player);
        }
    }
    public static Task LoseStars(decimal amount, Player player)
    {
        var world = player.RunState.World; world.RequireExactScope(player.Creature);
        if (world.CombatState?.IsEnding == true) return Task.CompletedTask;
        RequireCombatState(player).LoseStars(amount); return Task.CompletedTask;
    }
    public static async Task SetStars(decimal amount, Player player)
    {
        var world = player.RunState.World; world.RequireExactScope(player.Creature);
        if (world.CombatState?.IsEnding == true) return;
        var stars = RequireCombatState(player).Stars;
        if (stars < amount) await GainStars(amount - stars, player);
        else if (stars > amount) await LoseStars(stars - amount, player);
    }
    private static PlayerCombatState RequireCombatState(Player player) => player.PlayerCombatState
        ?? throw new RuleBoundaryException("state_incomplete", "Player has no combat resource state");
}
