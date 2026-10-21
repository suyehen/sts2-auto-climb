using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.GameActions.Multiplayer;

namespace AutoSlay.Rules.Native.Models;

public abstract partial class CardModel
{
    public event Action? Upgraded;
    // These native base callbacks are empty. The complete play wrapper is still not exposed.
    protected virtual Task OnPlay(PlayerChoiceContext choiceContext, CardPlay cardPlay) => Task.CompletedTask;
    protected virtual void OnUpgrade() { }
    protected virtual void AfterDowngraded() { }
    internal void RequireUpgradeState()
    {
        _ = CurrentUpgradeLevel; _ = DynamicVars; _ = LocalKeywords; RequireStarCosts();
    }
    public void UpgradeInternal()
    {
        AssertMutable(); RequireUpgradeState();
        CurrentUpgradeLevel++; OnUpgrade(); DynamicVars.RecalculateForUpgradeOrEnchant(); Upgraded?.Invoke();
    }
    public void FinalizeUpgradeInternal()
    {
        _ = DynamicVars; RequireStarCosts();
        DynamicVars.FinalizeUpgrade(); EnergyCost.FinalizeUpgrade(); wasStarCostJustUpgraded = false;
    }
    public void DowngradeInternal()
    {
        AssertMutable(); RequireUpgradeState(); CurrentUpgradeLevel = 0;
        var original = ModelDb.GetById<CardModel>(Id).ToMutable(); dynamicVars = original.DynamicVars.Clone(this);
        EnergyCost.ResetForDowngrade(); baseStarCost = original.CanonicalStarCost;
        keywords = original.GetKeywordsWithSources(KeywordSources.Local).ToHashSet();
        AfterDowngraded(); Upgraded?.Invoke();
        // Enchantment.ModifyCard/Affliction.AfterApplied are not skipped for present attachments:
        // the current creation/hydration scope admits only explicitly absent attachments.
    }
}
