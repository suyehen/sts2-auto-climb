using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Entities.Relics;
using AutoSlay.Rules.Native.Entities.Powers;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Combat.History;
using AutoSlay.Rules.Native.Rooms;
using AutoSlay.Rules.Native.Models.Singleton;
using AutoSlay.Rules.Native.Context;
using AutoSlay.Rules.Native.Entities.Cards;

namespace AutoSlay.Rules.State;

public sealed class SimulationWorld
{
    private readonly Dictionary<string, Creature> creatures = new(StringComparer.Ordinal);
    private readonly Dictionary<string, AbstractModel> instances = new(StringComparer.Ordinal);
    private readonly List<CommandTrace> trace = [];
    private readonly SemaphoreSlim gate = new(1, 1);
    private readonly AsyncLocal<ExecutionFrame?> execution = new();
    private long nextModelInstance;
    public RuleParameters? Parameters { get; }
    public ModelRegistry Registry { get; }
    public ModelDb Models { get; }
    public LocalContext LocalContext { get; } = new();
    public RunState RunState { get; }
    public CombatState? CombatState { get; private set; }
    public IReadOnlyCollection<Creature> Creatures => creatures.Values;
    internal IReadOnlyCollection<AbstractModel> ModelInstances => instances.Values;
    public IReadOnlyList<CommandTrace> Trace => trace.AsReadOnly();
    public bool IsFaulted { get; private set; }

    public SimulationWorld(RuleParameters? parameters = null, ModelRegistry? registry = null)
    {
        Parameters = parameters; Registry = registry ?? new ModelRegistry(typeof(AbstractModel).Assembly);
        Models = new(this, Registry); RunState = new(this);
    }
    public void Add(Creature creature)
    {
        using var hydration = EnterHydration();
        if (creature.AttachedWorld != null || !creatures.TryAdd(creature.InstanceId, creature))
            throw new RuleBoundaryException("foreign_state", "Creature is already attached or has a duplicate instance ID");
        creature.AttachedWorld = this;
    }
    public Creature Get(string instance) => creatures.TryGetValue(instance, out var value) ? value
        : throw new RuleBoundaryException("state_incomplete", "Creature not observed: " + instance);
    public void RestoreLocalPlayer(ulong? netId)
    { using var hydration = EnterHydration(); LocalContext.Restore(netId); }
    public Player RestorePlayer(ulong netId, string creatureId, bool isActiveForHooks)
    {
        using var hydration = EnterHydration(); var creature = Get(creatureId);
        if (RunState.Players.Any(p => p.NetId == netId) || creature.Player != null)
            throw new RuleBoundaryException("invalid_state", "Duplicate player/creature relationship");
        var player = new Player(netId, creature, RunState, isActiveForHooks); creature.RestoreSide(CombatSide.Player);
        RunState.Add(player); InvalidateHookProjection(); return player;
    }
    public void RestoreRoom(string id, RoomType type, int totalFloor)
    {
        using var hydration = EnterHydration(); if (string.IsNullOrWhiteSpace(id) || !Enum.IsDefined(type) || totalFloor < 0) throw new ArgumentException("Invalid room observation");
        RunState.CurrentRoom = new(id, type); RunState.TotalFloor = totalFloor;
    }
    public void RestoreActIndex(int actIndex)
    {
        using var hydration = EnterHydration();
        if (actIndex < 0) throw new RuleBoundaryException("invalid_state", "Negative act index");
        RunState.ObservedActIndex = actIndex;
    }
    public void RestoreAbsentCombatEncounter()
    {
        using var hydration = EnterHydration();
        (CombatState ?? throw new RuleBoundaryException("state_incomplete", "Combat state is missing")).EncounterStatus = ObservationStatus.ObservedAbsent;
    }
    public MultiplayerScalingModel RestoreMultiplayerScaling(string instanceId, bool attachedToCombat)
    {
        using var hydration = EnterHydration();
        if (RunState.MultiplayerScalingModel != null || string.IsNullOrWhiteSpace(instanceId) || instances.ContainsKey(instanceId)
            || attachedToCombat && CombatState == null) throw new RuleBoundaryException("invalid_state", "Invalid singleton observation");
        var model = (MultiplayerScalingModel)Models.Singleton<MultiplayerScalingModel>().MutableClone();
        model.InstanceId = instanceId; model.Initialize(RunState); instances.Add(instanceId, model); RunState.MultiplayerScalingModel = model;
        if (attachedToCombat) { CombatState!.MultiplayerScalingModel = model; model.OnCombatEntered(CombatState); }
        InvalidateHookProjection(); return model;
    }
    public void RestoreHistory(bool entryExists)
    {
        using var hydration = EnterHydration();
        RunState.HistoryStatus = entryExists ? ObservationStatus.ObservedPresent : ObservationStatus.ObservedAbsent;
        RunState.CurrentMapPointHistoryEntry = entryExists ? new(RunState.Players.Select(p => p.NetId)) : null;
    }
    public void RestoreEmptyCardUpgradeHistory()
    {
        using var hydration = EnterHydration(); RunState.RequireHistory();
        if (RunState.CurrentMapPointHistoryEntry == null) throw new RuleBoundaryException("invalid_state", "No current map entry to hold card history");
        var entries = RunState.CurrentMapPointHistoryEntry.PlayerStats.ToArray();
        if (entries.Any(e => e.CardUpgradeHistoryObserved)) throw new RuleBoundaryException("invalid_state", "Card upgrade history was already restored");
        foreach (var entry in entries) entry.CardUpgradeHistoryObserved = true;
    }
    public CombatState RestoreCombat(string id, CombatPhase phase, params string[] creatureIds)
    {
        using var hydration = EnterHydration();
        if (CombatState != null || string.IsNullOrWhiteSpace(id) || !Enum.IsDefined(phase) || creatureIds.Distinct().Count() != creatureIds.Length)
            throw new RuleBoundaryException("invalid_state", "Invalid combat identity or membership");
        var members = creatureIds.Select(Get).ToArray(); var combat = new CombatState(id, RunState, phase);
        foreach (var member in members) combat.Add(member);
        CombatState = combat; InvalidateHookProjection(); return combat;
    }
    /// <summary>Restores observed inventory; does not run acquisition, bag removal, payment or AfterObtained.</summary>
    public RelicModel RestoreRelic(Player player, RelicObservation observed)
    {
        using var hydration = EnterHydration(); RequireOwn(player.Creature);
        if (!RunState.Players.Contains(player) || string.IsNullOrWhiteSpace(observed.InstanceId) || instances.ContainsKey(observed.InstanceId)
            || !Enum.IsDefined(observed.Status) || observed.StackCount < 1)
            throw new RuleBoundaryException("invalid_state", "Invalid relic observation or owner");
        var relic = Models.GetById<RelicModel>(observed.ModelId).ToMutable();
        if (!relic.IsStackable && observed.StackCount != 1) throw new RuleBoundaryException("invalid_state", "Non-stackable relic has multiple stacks");
        relic.InstanceId = observed.InstanceId; relic.IsWax = observed.IsWax; relic.IsMelted = observed.IsMelted;
        relic.FloorAddedToDeck = observed.FloorAddedToDeck; relic.StackCount = observed.StackCount; relic.Status = observed.Status;
        player.AddRelicInternal(relic); instances.Add(observed.InstanceId, relic); InvalidateHookProjection(); return relic;
    }
    public AbstractModel RequireModel(string id) => instances.TryGetValue(id, out var model) ? model
        : throw new RuleBoundaryException("model_not_migrated", "Observed listener instance not hydrated: " + id);

    public void RestorePlayerCombatPresence(Player player, ObservationStatus status)
    {
        using var hydration = EnterHydration(); RequireOwn(player.Creature);
        if (status == ObservationStatus.ObservedPresent && player.Creature.CombatState != CombatState
            || status == ObservationStatus.ObservedPresent && CombatState == null)
            throw new RuleBoundaryException("invalid_state", "Present player combat state requires owned combat membership");
        player.RestoreCombatPresence(status);
    }
    public void RestorePlayerEnergy(Player player, int energy)
    {
        using var hydration = EnterHydration(); RequireOwn(player.Creature);
        (player.PlayerCombatState ?? throw new RuleBoundaryException("invalid_state", "Absent player combat state has no energy")).RestoreEnergy(energy);
    }
    public void RestorePlayerStars(Player player, int stars)
    {
        using var hydration = EnterHydration(); RequireOwn(player.Creature);
        (player.PlayerCombatState ?? throw new RuleBoundaryException("invalid_state", "Absent player combat state has no stars")).RestoreStars(stars);
    }
    public void RestoreDeckCardInventory(Player player, IReadOnlyList<string> cardIds)
    {
        using var hydration = EnterHydration(); RequireOwn(player.Creature);
        if (cardIds == null || !cardIds.SequenceEqual(player.Deck.Cards.Select(c => c.InstanceId)))
            throw new RuleBoundaryException("invalid_state", "Observed deck inventory differs from owned order");
        player.ObservedDeckCards = cardIds.ToArray();
    }
    /// <summary>
    /// Finite card-cost/pile observation, not full private card or play-state restoration. Nonempty
    /// enchantments/afflictions are rejected. Concrete card sources remain blocked in PowerCmd.
    /// </summary>
    public CardModel RestoreCardCostScope(Player player, string instanceId, ModelId modelId, PileType pileType,
        CardEnergyCostObservation energyCost, ObservationStatus enchantment, ObservationStatus affliction)
    {
        using var hydration = EnterHydration(); RequireOwn(player.Creature);
        if (string.IsNullOrWhiteSpace(instanceId) || instances.ContainsKey(instanceId) || !Enum.IsDefined(pileType))
            throw new RuleBoundaryException("invalid_state", "Invalid card identity/pile observation");
        if (enchantment != ObservationStatus.ObservedAbsent || affliction != ObservationStatus.ObservedAbsent)
            throw new RuleBoundaryException(enchantment == ObservationStatus.Unobserved || affliction == ObservationStatus.Unobserved
                ? "state_incomplete" : "mechanism_not_implemented", "Cost scope requires observed absent card enchantment and affliction");
        var pile = CardPile.Get(pileType, player);
        if (pileType != PileType.None && pile == null) throw new RuleBoundaryException("invalid_state", "Card pile does not exist");
        var card = Models.GetById<CardModel>(modelId).ToMutable();
        CardEnergyCostHydrator.Restore(card.EnergyCost, energyCost);
        card.MarkCostScopeUnobservedCardState();
        card.InstanceId = instanceId; card.Owner = player; instances.Add(instanceId, card);
        pile?.AddInternal(card, silent: true); InvalidateHookProjection(); return card;
    }
    public void RestoreCardKeywords(CardModel card, IReadOnlyCollection<CardKeyword> localKeywords)
    {
        using var hydration = EnterHydration(); card.AssertMutable();
        if (card.World != this || card.InstanceId == null || !ReferenceEquals(RequireModel(card.InstanceId), card))
            throw new RuleBoundaryException("foreign_state", "Keyword target is not an owned observed card");
        if (localKeywords == null || localKeywords.Distinct().Count() != localKeywords.Count || localKeywords.Any(k => !Enum.IsDefined(k)))
            throw new RuleBoundaryException("invalid_state", "Invalid card keyword observation");
        card.RestoreLocalKeywords(localKeywords);
    }
    public void RestoreCardUpgradeLevel(CardModel card, int level)
    {
        using var hydration = EnterHydration(); card.AssertMutable();
        if (card.World != this || card.InstanceId == null || !ReferenceEquals(RequireModel(card.InstanceId), card))
            throw new RuleBoundaryException("foreign_state", "Upgrade target is not an owned observed card");
        if (level < 0 || level > card.MaxUpgradeLevel) throw new RuleBoundaryException("invalid_state", "Invalid observed card upgrade level");
        card.RestoreUpgradeLevel(level);
    }
    public void RestoreCardDynamicVars(CardModel card, IReadOnlyCollection<CardVariableObservation> values)
    {
        using var hydration = EnterHydration(); RequireOwnedCard(card);
        CardRuleHydrator.RestoreVariables(card.DeclaredDynamicVars, values); card.MarkDynamicVarsObserved();
    }
    public void RestoreCardStarCosts(CardModel card, CardStarCostObservation state)
    {
        using var hydration = EnterHydration(); RequireOwnedCard(card);
        CardRuleHydrator.ValidateStars(state); card.RestoreStarCosts(state);
    }
    internal void RequireOwnedCard(CardModel card)
    {
        card.AssertMutable();
        if (card.World != this || card.InstanceId == null || !instances.TryGetValue(card.InstanceId, out var own) || !ReferenceEquals(own, card))
            throw new RuleBoundaryException("foreign_state", "Card is not an owned observed instance");
    }

    /// <summary>Restores observed power state without application callbacks, events, history or scaling.</summary>
    public PowerModel RestorePower(Creature creature, PowerObservation observed)
    {
        using var hydration = EnterHydration(); RequireOwn(creature);
        if (string.IsNullOrWhiteSpace(observed.InstanceId) || instances.ContainsKey(observed.InstanceId)
            || observed.Amount is < -999999999 or > 999999999)
            throw new RuleBoundaryException("invalid_state", "Invalid power observation");
        var source = observed.ApplierId == null ? null : Get(observed.ApplierId);
        var target = observed.TargetId == null ? null : Get(observed.TargetId);
        var canonical = Models.GetById<PowerModel>(observed.ModelId);
        if (canonical.InstanceType == PowerInstanceType.None && creature.HasPower(observed.ModelId))
            throw new RuleBoundaryException("invalid_state", "Duplicate non-instanced power observation");
        var power = canonical.ToMutable(); power.InstanceId = observed.InstanceId;
        power.RestoreOwnedState(creature, observed.Amount, observed.AmountOnTurnStart, observed.SkipNextDurationTick, source, target);
        creature.RestorePower(power); instances.Add(observed.InstanceId, power); InvalidateHookProjection(); return power;
    }

    /// <summary>Offline ordered projection only; not a replacement for full native inventory hydration.</summary>
    public void RestoreHookProjection(IReadOnlyList<string> runOrder, IReadOnlyList<string>? combatOrder = null)
    {
        using var hydration = EnterHydration();
        if (CombatState == null && combatOrder != null || CombatState != null && combatOrder == null)
            throw new RuleBoundaryException("state_incomplete", "Combat hook projection does not match observed combat");
        var run = runOrder.ToArray(); var combat = combatOrder?.ToArray(); var all = run.Concat(combat ?? []).ToArray();
        foreach (var id in all) _ = RequireModel(id);
        foreach (var player in RunState.Players.Where(p => p.IsActiveForHooks))
            foreach (var relic in player.Relics.Where(r => !r.IsMelted && !r.HasBeenRemovedFromState))
                if (!all.Contains(relic.InstanceId, StringComparer.Ordinal)) throw new RuleBoundaryException("state_incomplete", "Hook projection omits owned relic: " + relic.InstanceId);
        if (CombatState != null)
            foreach (var power in CombatState.Creatures.SelectMany(c => c.Powers))
                if (power.InstanceId == null || !all.Contains(power.InstanceId, StringComparer.Ordinal))
                    throw new RuleBoundaryException("state_incomplete", "Hook projection omits owned power: " + power.Id);
        if (RunState.MultiplayerScalingModel is { } scaling && !all.Contains(scaling.InstanceId, StringComparer.Ordinal))
            throw new RuleBoundaryException("state_incomplete", "Hook projection omits the run scaling model");
        RunState.ObservedHookOrder = run;
        if (CombatState != null) CombatState.ObservedHookOrder = combat;
    }
    internal void InvalidateHookProjection()
    { RunState.ObservedHookOrder = null; if (CombatState != null) CombatState.ObservedHookOrder = null; }

    public void RestoreCombatHookInventory(CombatHookObservation observation)
    {
        using var hydration = EnterHydration();
        if (observation == null || observation.Players?.Any(p => p == null) == true)
            throw new RuleBoundaryException("invalid_state", "Null hook observation or player domain");
        var combat = CombatState ?? throw new RuleBoundaryException("state_incomplete", "Combat state is missing");
        // Own a defensive copy. Unknown domains are retained; resolution is required by commands.
        var copy = observation with {
            Allies = observation.Allies?.ToArray(), Enemies = observation.Enemies?.ToArray(),
            Players = observation.Players?.Select(p => p with { DeckCards = p.DeckCards?.ToArray(), Relics = p.Relics?.ToArray(),
                PotionSlots = p.PotionSlots?.ToArray(), Orbs = p.Orbs?.ToArray(), CombatPiles = p.CombatPiles?.Select(pile => pile?.ToArray()!).ToArray() }).ToArray(),
            RunSubscribers = observation.RunSubscribers?.ToArray(), CombatSubscribers = observation.CombatSubscribers?.ToArray(),
            Modifiers = observation.Modifiers?.ToArray(), Badges = observation.Badges?.ToArray(),
            CreaturePowers = observation.CreaturePowers?.ToDictionary(p => p.Key, p => p.Value?.ToArray(), StringComparer.Ordinal)
        };
        if (copy.Players != null)
        {
            if (copy.Players.Select(p => p.PlayerId).Distinct().Count() != copy.Players.Length)
                throw new RuleBoundaryException("invalid_state", "Duplicate hook player domains");
            // Validate the whole presence observation before changing any owned player shell.
            foreach (var domains in copy.Players)
            {
                var player = RunState.Players.SingleOrDefault(p => p.NetId == domains.PlayerId)
                    ?? throw new RuleBoundaryException("invalid_state", "Hook observation references an unknown player");
                if (!Enum.IsDefined(domains.CombatStateStatus) || domains.CombatStateStatus != ObservationStatus.Unobserved
                    && player.CombatStateStatus != ObservationStatus.Unobserved && domains.CombatStateStatus != player.CombatStateStatus)
                    throw new RuleBoundaryException("invalid_state", "Invalid or conflicting player combat-state presence");
                if (domains.CombatStateStatus == ObservationStatus.ObservedPresent && player.Creature.CombatState != combat)
                    throw new RuleBoundaryException("invalid_state", "Present player combat state requires owned combat membership");
            }
            foreach (var domains in copy.Players)
                RunState.Players.Single(p => p.NetId == domains.PlayerId).RestoreCombatPresence(domains.CombatStateStatus);
        }
        combat.HookInventory = new(combat, copy); InvalidateHookProjection();
        if (copy.Allies != null && copy.Enemies != null)
        {
            var ids = copy.Allies.Concat(copy.Enemies).ToArray();
            if (ids.Distinct(StringComparer.Ordinal).Count() == ids.Length
                && ids.Order(StringComparer.Ordinal).SequenceEqual(combat.Creatures.Select(c => c.InstanceId).Order(StringComparer.Ordinal)))
            {
                foreach (var id in copy.Allies) Get(id).RestoreSide(CombatSide.Player);
                foreach (var id in copy.Enemies) Get(id).RestoreSide(CombatSide.Enemy);
            }
        }
    }
    public void RestoreCombatTimeline(int roundNumber, CombatSide currentSide, IReadOnlyDictionary<ulong, int> playerTurnNumbers)
    {
        using var hydration = EnterHydration();
        var combat = CombatState ?? throw new RuleBoundaryException("state_incomplete", "Combat state is missing");
        if (roundNumber < 0 || !Enum.IsDefined(currentSide) || playerTurnNumbers.Values.Any(t => t < 0)
            || !playerTurnNumbers.Keys.Order().SequenceEqual(RunState.Players.Select(p => p.NetId).Order()))
            throw new RuleBoundaryException("invalid_state", "Invalid combat timeline observation");
        combat.Timeline = new(roundNumber, currentSide, new Dictionary<ulong, int>(playerTurnNumbers));
    }
    public void RestoreEmptyCombatPowerHistory()
    {
        using var hydration = EnterHydration();
        (CombatState ?? throw new RuleBoundaryException("state_incomplete", "Combat state is missing")).History.ObserveEmpty();
    }
    public void RestoreEmptyCombatResourceHistory()
    {
        using var hydration = EnterHydration();
        (CombatState ?? throw new RuleBoundaryException("state_incomplete", "Combat state is missing")).History.ObserveEmptyResources();
    }
    internal void RegisterPowerForCommand(PowerModel power)
    {
        RequireExecution(); power.AssertMutable();
        if (power.World != this || Registry.Require(power.Id) != power.GetType())
            throw new RuleBoundaryException("foreign_state", "Power belongs to another world or registry");
        if (power.InstanceId != null)
        {
            if (!instances.TryGetValue(power.InstanceId, out var existing) || !ReferenceEquals(existing, power))
                throw new RuleBoundaryException("foreign_state", "Power instance identity is not owned by this world");
            return;
        }
        string id;
        do { id = "$power:" + (++nextModelInstance).ToString(System.Globalization.CultureInfo.InvariantCulture); } while (instances.ContainsKey(id));
        power.InstanceId = id; instances.Add(id, power);
    }

    public async Task ExecuteAsync(Func<Task> operation, AbstractModel? source = null, CancellationToken cancellationToken = default)
    {
        if (!await gate.WaitAsync(0, cancellationToken)) throw new RuleBoundaryException("world_busy", "World already has an active transition");
        var lease = new ExecutionLease(cancellationToken);
        try
        {
            if (IsFaulted) throw new RuleBoundaryException("world_faulted", "Failed/cancelled branch cannot continue");
            if (source != null && (source.World != this || source.InstanceId == null || !instances.TryGetValue(source.InstanceId, out var own) || !ReferenceEquals(own, source)))
                throw new RuleBoundaryException("foreign_state", "Source is not a mutable instance in this world");
            execution.Value = new(lease, source?.InstanceId ?? "external");
            cancellationToken.ThrowIfCancellationRequested(); await operation(); cancellationToken.ThrowIfCancellationRequested();
        }
        catch { IsFaulted = true; throw; }
        finally { lease.Active = false; execution.Value = null; gate.Release(); }
    }
    public void RequireExactScope(Creature creature)
    { RequireExecution(); RequireOwn(creature); }
    internal void RequireOwn(Creature creature)
    {
        if (creature.AttachedWorld != this || !creatures.TryGetValue(creature.InstanceId, out var own) || !ReferenceEquals(creature, own))
            throw new RuleBoundaryException("foreign_state", "Command target is not part of this world");
    }
    internal void RequireExecution()
    {
        var frame = execution.Value;
        if (frame?.Lease.Active != true) throw new RuleBoundaryException("foreign_state", "No active transition context for this world");
        frame.Lease.CancellationToken.ThrowIfCancellationRequested();
    }
    private IDisposable EnterHydration()
    {
        if (!gate.Wait(0)) throw new RuleBoundaryException("world_busy", "Cannot hydrate/clone during an active transition");
        if (IsFaulted) { gate.Release(); throw new RuleBoundaryException("world_faulted", "Failed/cancelled branch cannot be reused"); }
        return new Scope(() => gate.Release());
    }
    internal IDisposable EnterModel(AbstractModel model)
    {
        RequireExecution(); if (model.World != this) throw new RuleBoundaryException("foreign_state", "Foreign listener");
        var previous = execution.Value!; execution.Value = new(previous.Lease, model.InstanceId ?? throw new RuleBoundaryException("invalid_state", "Canonical listener"));
        return new Scope(() => execution.Value = previous);
    }
    internal void Record(string operation, Creature target, decimal requested, int hp, int maximum, int block) =>
        trace.Add(new(trace.Count + 1, operation, execution.Value?.Source ?? throw new InvalidOperationException("No execution context"),
            target.InstanceId, requested, hp, target.CurrentHp, maximum, target.MaxHp, block, target.Block));

    public SimulationWorld Clone()
    {
        if (!gate.Wait(0)) throw new RuleBoundaryException("world_busy", "Cannot clone an active transition");
        try
        {
            if (IsFaulted) throw new RuleBoundaryException("world_faulted", "Cannot clone a failed/cancelled branch as exact state");
            var copy = new SimulationWorld(Parameters, Registry) { nextModelInstance = nextModelInstance }; var context = new WorldCloneContext(this, copy);
            context.Add(LocalContext, copy.LocalContext);
            context.Add(RunState, copy.RunState);
            foreach (var creature in creatures.Values) { var cloned = creature.CreateCloneShell(); copy.Add(cloned); context.Add(creature, cloned); }
            foreach (var player in RunState.Players)
            { var cloned = copy.RestorePlayer(player.NetId, player.Creature.InstanceId, player.IsActiveForHooks); context.Add(player, cloned); }
            copy.RunState.CurrentRoom = RunState.CurrentRoom?.Clone(); copy.RunState.TotalFloor = RunState.TotalFloor;
            if (RunState.CurrentRoom != null) context.Add(RunState.CurrentRoom, copy.RunState.CurrentRoom!);
            copy.RunState.HistoryStatus = RunState.HistoryStatus; copy.RunState.CurrentMapPointHistoryEntry = RunState.CurrentMapPointHistoryEntry?.Clone();
            if (RunState.CurrentMapPointHistoryEntry != null)
            {
                context.Add(RunState.CurrentMapPointHistoryEntry, copy.RunState.CurrentMapPointHistoryEntry!);
                foreach (var stats in RunState.CurrentMapPointHistoryEntry.PlayerStats)
                    context.Add(stats, copy.RunState.CurrentMapPointHistoryEntry!.GetEntry(stats.PlayerId));
            }
            if (CombatState != null)
            {
                var combat = copy.RestoreCombat(CombatState.InstanceId, CombatState.Phase, CombatState.Creatures.Select(c => c.InstanceId).ToArray());
                context.Add(CombatState, combat); combat.ObservedHookOrder = CombatState.ObservedHookOrder?.ToArray();
            }
            var canonical = Models.CanonicalInstances.ToArray();
            foreach (var model in canonical) _ = context.AllocateModel(model);
            foreach (var entry in instances) copy.instances.Add(entry.Key, context.AllocateModel(entry.Value));
            context.FillAllocatedState();
            copy.trace.AddRange(trace);
            WorldCloneContext.AssertDisjoint(StateRoots(), copy.StateRoots(), this, copy);
            return copy;
        }
        finally { gate.Release(); }
    }
    private object[] StateRoots() => [LocalContext, RunState, .. creatures.Values, .. instances.Values, .. Models.CanonicalInstances];
    internal T Inspect<T>(Func<T> read)
    {
        using var scope = EnterHydration(); return read();
    }
    internal void RestoreTrace(IEnumerable<CommandTrace> entries)
    {
        using var scope = EnterHydration(); var values = entries.ToArray();
        if (values.Where((entry, index) => entry.Sequence != index + 1).Any())
            throw new RuleBoundaryException("invalid_snapshot", "Trace sequence is not contiguous");
        trace.AddRange(values);
    }
    private sealed class ExecutionLease(CancellationToken cancellationToken)
    { public bool Active = true; public CancellationToken CancellationToken { get; } = cancellationToken; }
    private sealed record ExecutionFrame(ExecutionLease Lease, string Source);
    private sealed class Scope(Action dispose) : IDisposable { public void Dispose() => dispose(); }
}

public sealed record RelicObservation(string InstanceId, ModelId ModelId, bool IsWax, bool IsMelted,
    int FloorAddedToDeck, int StackCount, RelicStatus Status);
public sealed record PowerObservation(string InstanceId, ModelId ModelId, int Amount, int AmountOnTurnStart,
    bool SkipNextDurationTick, string? ApplierId, string? TargetId);
