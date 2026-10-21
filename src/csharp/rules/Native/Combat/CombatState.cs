using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.Native.Combat.History;
using AutoSlay.Rules.Native.Models.Singleton;

namespace AutoSlay.Rules.Native.Combat;

public enum CombatPhase { NotStarted, InProgress, Ending, Over, Starting }

/// <summary>Initial identity/membership/phase graph; monster turns and terminal resolution are not migrated.</summary>
public sealed class CombatState : ICombatState
{
    private readonly List<Creature> creatures = [];
    public string InstanceId { get; }
    public RunState RunState { get; }
    public CombatPhase Phase { get; internal set; }
    // Native CombatManager.IsEnding is true while combat is still in progress.
    public bool IsInProgress => Phase is CombatPhase.InProgress or CombatPhase.Ending;
    public bool IsEnding => Phase == CombatPhase.Ending;
    public bool IsStarting => Phase == CombatPhase.Starting;
    public bool IsOverOrEnding => IsEnding || !IsInProgress;
    public CombatHistory History { get; } = new();
    public MultiplayerScalingModel? MultiplayerScalingModel { get; internal set; }
    internal ObservationStatus EncounterStatus { get; set; }
    public EncounterModel? Encounter => EncounterStatus == ObservationStatus.ObservedAbsent ? null
        : throw new RuleBoundaryException(EncounterStatus == ObservationStatus.Unobserved ? "state_incomplete" : "mechanism_not_implemented", "Combat encounter was not restored");
    internal CombatTimeline? Timeline { get; set; }
    internal CombatTimeline RequireTimeline() => Timeline ?? throw new RuleBoundaryException("state_incomplete", "Combat round/side/player turn numbers were not observed");
    public int RoundNumber => RequireTimeline().RoundNumber;
    public CombatSide CurrentSide => RequireTimeline().CurrentSide;
    public Player? GetPlayer(ulong netId) => Players.FirstOrDefault(p => p.NetId == netId);
    public IReadOnlyList<Creature> Creatures => creatures.AsReadOnly();
    internal string[]? ObservedHookOrder { get; set; }
    internal CombatHookInventory? HookInventory { get; set; }
    public IReadOnlyList<Player> Players => RunState.Players;
    public bool ContainsCreature(Creature creature) => creatures.Contains(creature);
    public IEnumerable<AbstractModel> IterateHookListeners() => HookInventory?.CombatListeners()
        ?? throw new RuleBoundaryException("state_incomplete", "Native combat hook domains were not restored");
    internal CombatState(string id, RunState runState, CombatPhase phase) { InstanceId = id; RunState = runState; Phase = phase; }
    internal void Add(Creature creature) { creatures.Add(creature); creature.CombatState = this; }
}
