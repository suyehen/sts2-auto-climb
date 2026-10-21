using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.GameActions.Multiplayer;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Entities.Cards;

namespace AutoSlay.Rules.Native.Hooks;

public static class Hook
{
    public static bool ShouldGainStars(ICombatState state, decimal amount, Player player)
    {
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            if (!model.ShouldGainStars(amount, player)) return false;
        }
        return true;
    }
    public static bool ShouldPayExcessEnergyCostWithStars(ICombatState state, Player player)
    {
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            if (model.ShouldPayExcessEnergyCostWithStars(player)) return true;
        }
        return false;
    }
    public static async Task AfterStarsGained(ICombatState state, int amount, Player gainer)
    {
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            await model.AfterStarsGained(amount, gainer); model.InvokeExecutionFinished();
        }
    }
    public static async Task AfterStarsSpent(ICombatState state, int amount, Player spender)
    {
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            await model.AfterStarsSpent(amount, spender); model.InvokeExecutionFinished();
        }
    }
    public static async Task AfterEnergySpent(ICombatState state, CardModel card, int amount)
    {
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            await model.AfterEnergySpent(card, amount); model.InvokeExecutionFinished();
        }
    }
    public static decimal ModifyStarCost(ICombatState state, CardModel card, decimal originalCost)
    {
        if (originalCost < 0) return originalCost;
        if (card.World != state.RunState.World) throw new RuleBoundaryException("foreign_state", "Foreign star-cost target");
        var result = originalCost;
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            model.TryModifyStarCost(card, result, out result);
        }
        return result;
    }
    public static void ModifyKeywordsInCombat(ICombatState state, CardModel card, ISet<CardKeyword> keywords)
    {
        if (card.World != state.RunState.World) throw new RuleBoundaryException("foreign_state", "Foreign keyword target");
        // Native keywords deliberately use the raw combat iterator, unlike the phase-gated cost hook.
        foreach (var model in state.IterateHookListeners())
        {
            using var scope = state.RunState.World.EnterModel(model);
            model.TryModifyKeywordsInCombat(card, keywords);
        }
    }
    public static decimal ModifyEnergyGain(ICombatState state, Player player, decimal originalAmount, out IEnumerable<AbstractModel> modifiers)
    {
        if (player.RunState.World != state.RunState.World) throw new RuleBoundaryException("foreign_state", "Foreign energy target");
        var result = originalAmount; var changed = new List<AbstractModel>();
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            var previous = result; result = model.ModifyEnergyGain(player, result);
            // The native notification criterion compares integer values, not decimal equality.
            if ((int)previous != (int)result) changed.Add(model);
        }
        modifiers = changed; return result;
    }
    public static async Task AfterModifyingEnergyGain(ICombatState state, IEnumerable<AbstractModel> modifiers)
    {
        foreach (var model in IterateCombatHookListeners(state))
            if (modifiers.Contains(model))
            {
                using var scope = state.RunState.World.EnterModel(model);
                await model.AfterModifyingEnergyGain(); model.InvokeExecutionFinished();
            }
    }
    public static decimal ModifyEnergyCostInCombat(ICombatState state, CardModel card, decimal originalCost)
    {
        if (originalCost < 0) return originalCost;
        if (card.World != state.RunState.World) throw new RuleBoundaryException("foreign_state", "Foreign card cost target");
        var modifiedCost = originalCost;
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            model.TryModifyEnergyCostInCombat(card, modifiedCost, out modifiedCost);
        }
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            model.TryModifyEnergyCostInCombatLate(card, modifiedCost, out modifiedCost);
        }
        return modifiedCost;
    }
    private static IEnumerable<AbstractModel> IterateCombatHookListeners(ICombatState state)
    {
        var combat = state as CombatState ?? throw new RuleBoundaryException("foreign_state", "Owned combat state required");
        // Check once at enumeration start. A dispatch already in progress keeps the native sequence.
        if (combat.IsOverOrEnding && !combat.IsStarting) yield break;
        foreach (var model in combat.IterateHookListeners()) yield return model;
    }
    public static bool ShouldAllowHitting(ICombatState state, Creature creature)
    {
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            if (!model.ShouldAllowHitting(creature)) return false;
        }
        return true;
    }
    public static async Task BeforePowerAmountChanged(ICombatState state, PowerModel power, decimal amount, Creature target, Creature? applier, CardModel? cardSource)
    {
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            await model.BeforePowerAmountChanged(power, amount, target, applier, cardSource); model.InvokeExecutionFinished();
        }
    }
    public static async Task AfterPowerAmountChanged(ICombatState state, PlayerChoiceContext choiceContext, PowerModel power, decimal amount, Creature? applier, CardModel? cardSource)
    {
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            await model.AfterPowerAmountChanged(choiceContext, power, amount, applier, cardSource); model.InvokeExecutionFinished();
        }
    }
    public static decimal ModifyPowerAmountGiven(ICombatState state, PowerModel power, Creature giver, decimal amount, Creature? target, CardModel? cardSource, out IEnumerable<AbstractModel> modifiers)
    {
        var result = amount; var changed = new List<AbstractModel>();
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            var additive = model.ModifyPowerAmountGivenAdditive(power, giver, result, target, cardSource);
            result += additive; if (additive != 0) changed.Add(model);
        }
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            var multiplicative = model.ModifyPowerAmountGivenMultiplicative(power, giver, result, target, cardSource);
            result *= multiplicative; if (multiplicative != 1) changed.Add(model);
        }
        modifiers = changed; return result;
    }
    public static decimal ModifyPowerAmountReceived(ICombatState state, PowerModel power, Creature target, decimal amount, Creature? giver, out IEnumerable<AbstractModel> modifiers)
    {
        var result = amount; var changed = new List<AbstractModel>();
        foreach (var model in IterateCombatHookListeners(state))
        {
            using var scope = state.RunState.World.EnterModel(model);
            if (model.TryModifyPowerAmountReceived(power, target, result, giver, out var replacement))
            { result = replacement; changed.Add(model); }
        }
        modifiers = changed; return result;
    }
    public static async Task AfterModifyingPowerAmountGiven(ICombatState state, IEnumerable<AbstractModel> modifiers, PowerModel modifiedPower)
    {
        foreach (var model in IterateCombatHookListeners(state))
            if (modifiers.Contains(model))
            {
                using var scope = state.RunState.World.EnterModel(model);
                await model.AfterModifyingPowerAmountGiven(modifiedPower); model.InvokeExecutionFinished();
            }
    }
    public static async Task AfterModifyingPowerAmountReceived(ICombatState state, IEnumerable<AbstractModel> modifiers, PowerModel modifiedPower)
    {
        foreach (var model in IterateCombatHookListeners(state))
            if (modifiers.Contains(model))
            {
                using var scope = state.RunState.World.EnterModel(model);
                await model.AfterModifyingPowerAmountReceived(modifiedPower); model.InvokeExecutionFinished();
            }
    }
    public static async Task AfterCurrentHpChanged(RunState runState, CombatState? combatState, Creature creature, decimal delta)
    {
        foreach (var model in runState.IterateHookListeners(combatState))
        {
            using var scope = runState.World.EnterModel(model);
            await model.AfterCurrentHpChanged(creature, delta);
            model.InvokeExecutionFinished();
        }
    }
}
