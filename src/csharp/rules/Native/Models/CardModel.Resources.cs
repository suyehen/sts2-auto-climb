using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Hooks;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.Native.Models;

public abstract partial class CardModel
{
    /// <summary>Native payment only. Play legality and the complete play wrapper remain separate.</summary>
    public async Task<(int, int)> SpendResources()
    {
        World.RequireExecution(); World.RequireOwnedCard(this);
        var combat = CombatState as CombatState ?? throw new RuleBoundaryException("state_incomplete", "Payment requires an owned combat card");
        var playerState = Owner.PlayerCombatState ?? throw new RuleBoundaryException("state_incomplete", "Payment requires player combat state");
        var energy = playerState.Energy;
        var energyToSpend = EnergyCost.GetAmountToSpend();
        var starsToSpend = Math.Max(0, GetStarCostWithModifiers());
        if (energyToSpend > energy && Hook.ShouldPayExcessEnergyCostWithStars(combat, Owner))
        { starsToSpend += (energyToSpend - energy) * 2; energyToSpend = energy; }
        // Validate finite observed state before starting the native mutation/callback sequence.
        RequireStarCosts();
        if (energyToSpend > 0 || starsToSpend > 0) combat.History.RequireResourcesObserved(combat);
        if (starsToSpend > 0) _ = playerState.Stars;
        await SpendEnergy(energyToSpend);
        await SpendStars(starsToSpend);
        return (energyToSpend, starsToSpend);
    }
    private async Task SpendEnergy(int amount)
    {
        if (EnergyCost.CostsX) EnergyCost.CapturedXValue = amount;
        if (amount > 0)
        {
            var combat = (CombatState)CombatState!;
            combat.History.EnergySpent(combat, amount, Owner);
            Owner.PlayerCombatState!.LoseEnergy(Math.Max(0, amount));
        }
        await Hook.AfterEnergySpent(CombatState!, this, amount);
    }
    private async Task SpendStars(int amount)
    {
        LastStarsSpent = amount;
        if (amount > 0)
        {
            Owner.PlayerCombatState!.LoseStars(amount);
            await Hook.AfterStarsSpent(Owner.Creature.CombatState!, amount, Owner);
        }
    }
}
