using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.State;
using AutoSlay.Rules.Native.Entities.Powers;
using AutoSlay.Rules.Native.Localization.DynamicVars;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.GameActions.Multiplayer;
using AutoSlay.Rules.Native.Models.Powers;

public sealed class HpLedgerRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public List<decimal> Deltas { get; private set; } = [];
    public Creature? LastTarget { get; private set; }
    public HpLedgerRelic? Peer { get; set; }
    public override Task AfterCurrentHpChanged(Creature creature, decimal delta)
    { Deltas.Add(delta); LastTarget = creature; return Task.CompletedTask; }
    protected override void DeepCloneFields() { base.DeepCloneFields(); Deltas = new(Deltas); }
}

// No entity-specific clone implementation: a world fork must preserve and isolate private state.
public sealed class UnsafeRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public List<int> PrivateData { get; } = [1];
    private Ledger state = new();
    public int EventCount => state.Count;
    public int CreationResets { get; private set; }
    public void Attach(Creature creature) => creature.CurrentHpChanged += OnHpChanged;
    private void OnHpChanged(int before, int after) => state.Count++;
    public void ShareStateWith(UnsafeRelic peer) => peer.state = state;
    protected override void AfterCloned()
    {
        base.AfterCloned(); state = new(); CreationResets++;
    }
    private sealed class Ledger { public int Count; }
}

public sealed class OpaqueRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public OpaqueSequence PrivateData { get; } = new();
}
public sealed class OpaqueSequence : System.Collections.IEnumerable
{
    public int Enumerations { get; private set; }
    public System.Collections.IEnumerator GetEnumerator()
    { Enumerations++; throw new InvalidOperationException("Unmigrated enumerable must not be executed during clone validation"); }
}

public sealed class SignedCounterPower : PowerModel
{
    public override PowerType Type => PowerType.Buff;
    public override PowerStackType StackType => PowerStackType.Counter;
    public override bool AllowNegative => true;
    protected override IEnumerable<DynamicVar> CanonicalVars => [new MaxHpVar(3)];
    private sealed class Data
    {
        public List<decimal> Deltas = [];
        public Dictionary<string, Creature> Targets = new(StringComparer.Ordinal);
        public HashSet<int> Seen = [];
    }
    public int Initializations { get; private set; }
    protected override object InitInternalData() { Initializations++; return new Data(); }
    public IReadOnlyList<decimal> Deltas => GetInternalData<Data>().Deltas;
    public Creature? LastTarget => GetInternalData<Data>().Targets.GetValueOrDefault("last");
    public override Task AfterCurrentHpChanged(Creature creature, decimal delta)
    {
        var data = GetInternalData<Data>(); data.Deltas.Add(delta); data.Targets["last"] = creature;
        data.Seen.Add(data.Deltas.Count); return Task.CompletedTask;
    }
}
public sealed class DebuffCounterPower : PowerModel
{
    public override PowerType Type => PowerType.Debuff;
    public override PowerStackType StackType => PowerStackType.Counter;
}
public sealed class PerApplierPower : PowerModel
{
    public override PowerType Type => PowerType.Buff;
    public override PowerStackType StackType => PowerStackType.Counter;
    public override PowerInstanceType InstanceType => PowerInstanceType.InstancedPerApplier;
}
public sealed class AlwaysInstancedPower : PowerModel
{
    public override PowerType Type => PowerType.Buff;
    public override PowerStackType StackType => PowerStackType.Counter;
    public override PowerInstanceType InstanceType => PowerInstanceType.Instanced;
}

public sealed class IdentityLedgerRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public Dictionary<Creature, int> Ledger { get; } = [];
    public HashSet<Creature> Seen { get; } = [];
    public Dictionary<object, string> ReferenceLedger { get; } = new(ReferenceEqualityComparer.Instance);
    public Dictionary<CustomKey, int> CustomLedger { get; } = [];
    public sealed class CustomKey
    {
        public static int Invocations;
        public override int GetHashCode() { Invocations++; return 2; }
        public override bool Equals(object? obj) { Invocations++; return ReferenceEquals(this, obj); }
    }
}

public sealed class DelegateGraphRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public Func<int>? Callback { get; private set; }
    public Func<int>? Alias { get; private set; }
    public Action? Subscriber { get; private set; }
    public Action? Multicast { get; private set; }
    public void Initialize()
    {
        var count = 0; Func<int>? recursive = null;
        recursive = () => {
            if (!ReferenceEquals(recursive, Callback)) throw new InvalidOperationException("Captured callback alias was broken");
            return ++count;
        };
        Callback = recursive; Alias = recursive;
        Subscriber = () => recursive!(); Multicast = Subscriber + Subscriber;
    }
    public void InitializeArrayTarget()
    {
        object[] state = [null!, 0];
        var method = typeof(DelegateGraphRelic).GetMethod(nameof(InvokeArray), System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Static)!;
        Callback = Alias = method.CreateDelegate<Func<int>>(state); state[0] = Callback;
    }
    private static int InvokeArray(object[] state)
    {
        if (!ReferenceEquals(((Delegate)state[0]).Target, state)) throw new InvalidOperationException("Array target cycle was broken");
        return (int)(state[1] = (int)state[1] + 1);
    }
}

public class TemporaryGainFixture : TemporaryStrengthPower
{
    public override AbstractModel OriginModel => ModelDb.Relic<AutoSlay.Rules.Native.Models.Relics.Mango>();
}
public sealed class TemporaryLossFixture : TemporaryStrengthPower
{
    public override AbstractModel OriginModel => ModelDb.Relic<AutoSlay.Rules.Native.Models.Relics.Mango>();
    protected override bool IsPositive => false;
}
public sealed class InvisibleDebuffFixture : PowerModel
{
    public override PowerType Type => PowerType.Debuff;
    public override PowerStackType StackType => PowerStackType.Counter;
    protected override bool IsVisibleInternal => false;
}
public sealed class PowerOrderRelic : RelicModel
{
    public override RelicRarity Rarity => RelicRarity.Event;
    public List<string> Calls { get; private set; } = [];
    protected override void DeepCloneFields() { base.DeepCloneFields(); Calls = new(Calls); }
    public decimal Additive { get; set; }
    public decimal Multiplier { get; set; } = 1;
    public bool PreventHitting { get; set; }
    public bool EndOnBefore { get; set; }
    public bool DeactivateOnBefore { get; set; }
    public bool ThrowOnBefore { get; set; }
    public override bool ShouldAllowHitting(Creature creature) => !PreventHitting;
    public override Task BeforePowerAmountChanged(PowerModel power, decimal amount, Creature target, Creature? applier, CardModel? cardSource)
    {
        Calls.Add("before:" + amount);
        if (EndOnBefore) typeof(CombatState).GetProperty(nameof(CombatState.Phase))!.SetValue(target.CombatState, CombatPhase.Ending);
        if (DeactivateOnBefore) Owner.DeactivateHooks();
        if (ThrowOnBefore) throw new InvalidOperationException("fixture before failure");
        return Task.CompletedTask;
    }
    public override decimal ModifyPowerAmountGivenAdditive(PowerModel power, Creature giver, decimal amount, Creature? target, CardModel? cardSource)
    { Calls.Add("add:" + amount); return Additive; }
    public override decimal ModifyPowerAmountGivenMultiplicative(PowerModel power, Creature giver, decimal amount, Creature? target, CardModel? cardSource)
    { Calls.Add("multiply:" + amount); return Multiplier; }
    public override Task AfterModifyingPowerAmountGiven(PowerModel power)
    { Calls.Add("modified:" + power.Amount); return Task.CompletedTask; }
    public override Task AfterPowerAmountChanged(PlayerChoiceContext choiceContext, PowerModel power, decimal amount, Creature? applier, CardModel? cardSource)
    { Calls.Add("after:" + amount); return Task.CompletedTask; }
}
public sealed class UnportedCardFixture : CardModel
{
    public UnportedCardFixture() : base(1, AutoSlay.Rules.Native.Entities.Cards.CardType.Skill,
        AutoSlay.Rules.Native.Entities.Cards.CardRarity.Common, AutoSlay.Rules.Native.Entities.Cards.TargetType.Self) { }
    public override bool ShouldReceiveCombatHooks => false;
}
public sealed class BossEncounterFixture : EncounterModel
{
    public override AutoSlay.Rules.Native.Rooms.RoomType RoomType => AutoSlay.Rules.Native.Rooms.RoomType.Boss;
}
