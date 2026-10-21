using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Entities.Cards;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Localization.DynamicVars;
using AutoSlay.Rules.Native.Hooks;
using System.Collections.Immutable;

namespace AutoSlay.Rules.Native.Models;

/// <summary>
/// Migrated ownership, pile context, variables, keywords, costs and absent-attachment upgrades.
/// Full play, enchantment/affliction pipelines and arbitrary private-state hydration remain pending.
/// </summary>
public abstract partial class CardModel : AbstractModel
{
    private Player? owner;
    private CardEnergyCost? energyCost;
    private DynamicVarSet? dynamicVars;
    private CardModel? canonicalInstance;
    private CardUpgradePreviewType upgradePreviewType;
    private HashSet<CardKeyword>? keywords;
    private bool localKeywordsObserved = true;
    private bool upgradeLevelObserved = true;
    private int currentUpgradeLevel;
    private bool dynamicVarsObserved = true;
    public virtual IEnumerable<CardKeyword> CanonicalKeywords => Array.Empty<CardKeyword>();
    private HashSet<CardKeyword> LocalKeywords
    {
        get
        {
            if (!localKeywordsObserved) throw new RuleBoundaryException("state_incomplete", "Card local keywords were not observed");
            return keywords ??= new(CanonicalKeywords);
        }
    }
    public IReadOnlySet<CardKeyword> Keywords => GetKeywordsWithSources(KeywordSources.All);
    public virtual int MaxUpgradeLevel => 1;
    public int CurrentUpgradeLevel
    {
        get => upgradeLevelObserved ? currentUpgradeLevel : throw new RuleBoundaryException("state_incomplete", "Card upgrade level was not observed");
        private set
        {
            AssertMutable();
            if (value > MaxUpgradeLevel) throw new InvalidOperationException($"{Id} cannot be upgraded past its MaxUpgradeLevel.");
            currentUpgradeLevel = value; upgradeLevelObserved = true;
        }
    }
    public bool IsUpgraded => CurrentUpgradeLevel > 0;
    public bool IsUpgradable => CurrentUpgradeLevel < MaxUpgradeLevel;
    protected virtual int CanonicalEnergyCost { get; }
    protected virtual bool HasEnergyCostX => false;
    public virtual CardType Type { get; }
    public virtual CardRarity Rarity { get; }
    public virtual TargetType TargetType { get; }
    public bool ShouldShowInCardLibrary { get; }
    public bool HasBeenRemovedFromState { get; set; }
    public CardModel CanonicalInstance => IsCanonical ? this : canonicalInstance!;
    public CardEnergyCost EnergyCost => energyCost ??= new(this, CanonicalEnergyCost, HasEnergyCostX);
    protected virtual IEnumerable<DynamicVar> CanonicalVars => Array.Empty<DynamicVar>();
    public DynamicVarSet DynamicVars
    {
        get
        {
            if (!dynamicVarsObserved) throw new RuleBoundaryException("state_incomplete", "Card variables were not observed");
            if (dynamicVars == null) { dynamicVars = new(CanonicalVars); dynamicVars.InitializeWithOwner(this); }
            return dynamicVars;
        }
    }
    public CardPile? Pile
    {
        get
        {
            if (owner == null) return null;
            owner.RequireCardInventory();
            return owner.Piles.FirstOrDefault(p => p.Cards.Contains(this));
        }
    }
    public ICombatState? CombatState
    {
        get
        {
            var pile = Pile;
            return pile?.IsCombatPile == true || UpgradePreviewType == CardUpgradePreviewType.Combat
                ? owner?.Creature.CombatState : null;
        }
    }
    public override bool ShouldReceiveCombatHooks => Pile?.IsCombatPile ?? false;
    public CardUpgradePreviewType UpgradePreviewType
    {
        get => upgradePreviewType;
        set
        {
            AssertMutable();
            if (!value.IsPreview() && upgradePreviewType.IsPreview())
                throw new InvalidOperationException("A card cannot go to from being upgrade preview. Consider making a new card model instead.");
            upgradePreviewType = value;
        }
    }
    public event Action? EnergyCostChanged;
    public event Action? KeywordsChanged;
    protected CardModel(int canonicalEnergyCost, CardType type, CardRarity rarity, TargetType targetType, bool shouldShowInCardLibrary = true,
        AutoSlay.Rules.Parameters.RuleParameters? ruleParameters = null) : base(ruleParameters)
    {
        CanonicalEnergyCost = canonicalEnergyCost; Type = type; Rarity = rarity;
        TargetType = targetType; ShouldShowInCardLibrary = shouldShowInCardLibrary;
    }
    public void InvokeEnergyCostChanged() => EnergyCostChanged?.Invoke();
    public IReadOnlySet<CardKeyword> GetKeywordsWithSources(KeywordSources sources)
    {
        var local = sources.HasFlag(KeywordSources.Local);
        if (!sources.HasFlag(KeywordSources.Global) || IsCanonical || CombatState == null)
            return local ? LocalKeywords : ImmutableHashSet<CardKeyword>.Empty;
        var result = local ? new HashSet<CardKeyword>(LocalKeywords) : new HashSet<CardKeyword>();
        Hook.ModifyKeywordsInCombat(CombatState, this, result); return result;
    }
    public void AddKeyword(CardKeyword keyword) { AssertMutable(); LocalKeywords.Add(keyword); KeywordsChanged?.Invoke(); }
    public void RemoveKeyword(CardKeyword keyword) { AssertMutable(); LocalKeywords.Remove(keyword); KeywordsChanged?.Invoke(); }
    internal void MarkCostScopeUnobservedCardState()
    { localKeywordsObserved = false; keywords = null; upgradeLevelObserved = false; dynamicVarsObserved = false; starCostsObserved = false; }
    internal void RestoreLocalKeywords(IEnumerable<CardKeyword> observed) { keywords = new(observed); localKeywordsObserved = true; }
    internal void RestoreUpgradeLevel(int observed) => CurrentUpgradeLevel = observed;
    internal DynamicVarSet DeclaredDynamicVars => dynamicVars ?? throw new RuleBoundaryException("clone_contract_missing", "Card scalar variable graph is missing");
    internal void MarkDynamicVarsObserved() => dynamicVarsObserved = true;
    public Player Owner
    {
        get { AssertMutable(); return owner!; }
        set
        {
            AssertMutable();
            if (owner != null && value != null) throw new InvalidOperationException("Card " + Id.Entry + " already has an owner.");
            if (value != null && value.RunState.World != World) throw new RuleBoundaryException("foreign_state", "Card owner belongs to another world");
            owner = value;
        }
    }
    public CardModel ToMutable() { AssertCanonical(); return (CardModel)MutableClone(); }
    protected override void DeepCloneFields()
    {
        base.DeepCloneFields(); keywords = new(GetKeywordsWithSources(KeywordSources.Local));
        dynamicVars = DynamicVars.Clone(this); energyCost = energyCost?.Clone(this);
        temporaryStarCosts = temporaryStarCosts.ToList();
    }
    protected override void AfterCloned()
    {
        base.AfterCloned(); canonicalInstance ??= ModelDb.GetById<CardModel>(Id);
        HasBeenRemovedFromState = false; EnergyCostChanged = null; KeywordsChanged = null; StarCostChanged = null; Upgraded = null;
    }
}
