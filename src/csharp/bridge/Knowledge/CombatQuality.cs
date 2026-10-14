using HarmonyLib;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Combat.History;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;

namespace AutoSlayBridge;

public static partial class Bridge
{
    private sealed class QualityCounters
    {
        public required string CombatId;
        public required string RunId;
        public int EnemyLoss, SelfLoss, UnknownLoss, Healing;
        public bool StartedFromBeginning;
        public string? Result;
        public bool? Survived;
        public int? Turns;
    }
    private static QualityCounters? quality;
    private static bool observedHistoryReset;

    private static QualityCounters? QualityFor(ICombatState? state)
    {
        var live = CombatManager.Instance.DebugOnlyGetState();
        if (state == null || !ReferenceEquals(state, live) || !RunManager.Instance.IsInProgress) return null;
        var run = RunManager.Instance.DebugOnlyGetState();
        if (run == null) return null;
        var id = Id(state);
        if (quality?.CombatId != id)
            quality = new() { CombatId = id, RunId = Id(run), StartedFromBeginning = observedHistoryReset && live!.RoundNumber <= 1 };
        return quality;
    }

    private static object? CombatQuality(IRunState? run, CombatState? combat)
    {
        if (combat != null) QualityFor(combat);
        if (run == null || quality?.RunId != Id(run)) return null;
        return new { combat_instance_id = quality.CombatId, enemy_hp_loss = quality.EnemyLoss,
            self_hp_loss = quality.SelfLoss, unattributed_hp_loss = quality.UnknownLoss, healing = quality.Healing,
            result = quality.Result, survived = quality.Survived, turns_taken = quality.Turns,
            complete = quality.StartedFromBeginning, basis = "observed game damage/heal callbacks; no net-HP attribution" };
    }

    private static void ObserveCombatResult(bool won)
    {
        var state = CombatManager.Instance.DebugOnlyGetState();
        var row = QualityFor(state);
        if (row == null || state?.Players.Count != 1) return;
        var player = state.Players[0];
        row.Survived = player.Creature.CurrentHp > 0;
        row.Turns = player.PlayerCombatState?.TurnNumber;
        if (won) row.Result = "win";
        else if (row.Survived == false) row.Result = "loss";
    }

    [HarmonyPatch(typeof(CombatHistory), nameof(CombatHistory.Clear))]
    private static class ObserveHistoryReset
    {
        private static void Postfix() => observedHistoryReset = true;
    }

    [HarmonyPatch(typeof(CombatHistory), nameof(CombatHistory.DamageReceived))]
    private static class ObserveDamage
    {
        private static void Postfix(ICombatState combatState, Creature receiver, Creature? dealer, DamageResult result, CardModel? cardSource)
        {
            if (!receiver.IsPlayer) return;
            var row = QualityFor(combatState);
            if (row == null) return;
            if (dealer?.IsEnemy == true) row.EnemyLoss += result.UnblockedDamage;
            else if (dealer?.Side == receiver.Side || cardSource?.Owner == receiver.Player) row.SelfLoss += result.UnblockedDamage;
            else row.UnknownLoss += result.UnblockedDamage;
        }
    }

    [HarmonyPatch(typeof(Creature), nameof(MegaCrit.Sts2.Core.Entities.Creatures.Creature.HealInternal))]
    private static class ObserveHealing
    {
        private static void Prefix(Creature __instance, out int __state) => __state = __instance.CurrentHp;
        private static void Postfix(Creature __instance, int __state)
        {
            if (__instance.IsPlayer && QualityFor(__instance.CombatState) is { } row)
                row.Healing += Math.Max(0, __instance.CurrentHp - __state);
        }
    }
}
