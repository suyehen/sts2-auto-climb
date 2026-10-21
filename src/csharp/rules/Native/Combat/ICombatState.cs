using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Runs;

namespace AutoSlay.Rules.Native.Combat;

/// <summary>Migrated native interface members only. Missing APIs remain compile errors.</summary>
public interface ICombatState
{
    RunState RunState { get; }
    EncounterModel? Encounter { get; }
    IReadOnlyList<Player> Players { get; }
    int RoundNumber { get; }
    CombatSide CurrentSide { get; }
    Player? GetPlayer(ulong netId);
    bool ContainsCreature(Creature creature);
    IEnumerable<AbstractModel> IterateHookListeners();
}
