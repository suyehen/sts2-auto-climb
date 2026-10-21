using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Hooks;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;

namespace AutoSlay.Rules.Native.Models;

public abstract partial class CardModel
{
    private int baseStarCost;
    private bool starCostSet;
    private bool wasStarCostJustUpgraded;
    private int lastStarsSpent;
    private List<TemporaryCardCost> temporaryStarCosts = [];
    private bool starCostsObserved = true;
    public virtual int CanonicalStarCost => -1;
    public virtual bool HasStarCostX => false;
    private void RequireStarCosts()
    { if (!starCostsObserved) throw new RuleBoundaryException("state_incomplete", "Card star-cost state was not observed"); }
    public int BaseStarCost
    {
        get
        {
            RequireStarCosts();
            if (IsCanonical) return CanonicalStarCost;
            if (!starCostSet) { baseStarCost = CanonicalStarCost; starCostSet = true; }
            return baseStarCost;
        }
        private set
        {
            AssertMutable(); RequireStarCosts();
            if (!HasStarCostX) { baseStarCost = value; starCostSet = true; }
            StarCostChanged?.Invoke();
        }
    }
    public bool WasStarCostJustUpgraded { get { RequireStarCosts(); return wasStarCostJustUpgraded; } }
    public TemporaryCardCost? TemporaryStarCost { get { RequireStarCosts(); return temporaryStarCosts.LastOrDefault(); } }
    public virtual int CurrentStarCost
    {
        get
        {
            RequireStarCosts(); var temporary = temporaryStarCosts.LastOrDefault()?.Cost;
            if (temporary.HasValue) return temporary == 0 && BaseStarCost < 0 ? BaseStarCost : temporary.Value;
            return BaseStarCost;
        }
    }
    public int LastStarsSpent
    { get { RequireStarCosts(); return lastStarsSpent; } set { AssertMutable(); RequireStarCosts(); lastStarsSpent = value; } }
    public event Action? StarCostChanged;
    public void SetStarCostUntilPlayed(int cost) => AddTemporaryStarCost(TemporaryCardCost.UntilPlayed(cost));
    public void SetStarCostThisTurn(int cost) => AddTemporaryStarCost(TemporaryCardCost.ThisTurn(cost));
    public void SetStarCostThisCombat(int cost) => AddTemporaryStarCost(TemporaryCardCost.ThisCombat(cost));
    public int GetStarCostThisCombat()
    {
        RequireStarCosts();
        return temporaryStarCosts.FirstOrDefault(cost => cost != null && !cost.ClearsWhenTurnEnds && !cost.ClearsWhenCardIsPlayed)?.Cost ?? BaseStarCost;
    }
    private void AddTemporaryStarCost(TemporaryCardCost cost)
    { AssertMutable(); RequireStarCosts(); temporaryStarCosts.Add(cost); StarCostChanged?.Invoke(); }
    protected void UpgradeStarCostBy(int addend)
    {
        if (HasStarCostX) throw new InvalidOperationException("UpgradeStarCostBy called on " + Id.Entry + " which has star cost X.");
        if (addend == 0) return;
        var previous = BaseStarCost; BaseStarCost += addend; wasStarCostJustUpgraded = true;
        if (BaseStarCost < previous) temporaryStarCosts.RemoveAll(cost => cost.Cost > BaseStarCost);
    }
    public int GetStarCostWithModifiers()
    {
        if (HasStarCostX) return Owner.PlayerCombatState?.Stars ?? 0;
        return CombatState != null ? (int)Hook.ModifyStarCost(CombatState, this, CurrentStarCost) : CurrentStarCost;
    }
    internal void RestoreStarCosts(CardStarCostObservation state)
    {
        baseStarCost = state.Base; starCostSet = state.BaseWasSet; wasStarCostJustUpgraded = state.WasJustUpgraded; lastStarsSpent = state.LastStarsSpent;
        temporaryStarCosts = state.TemporaryCosts.Select(cost => cost.ClearsWhenTurnEnds ? TemporaryCardCost.ThisTurn(cost.Cost)
            : cost.ClearsWhenCardIsPlayed ? TemporaryCardCost.UntilPlayed(cost.Cost) : TemporaryCardCost.ThisCombat(cost.Cost)).ToList();
        starCostsObserved = true;
    }
}
