using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Actions;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Potions;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Runs;

namespace AutoSlayBridge;
public static partial class Bridge
{
    private sealed record PotionBinding(PotionModel Potion, NPotionHolder Holder, bool Discard,
        Creature?[] Targets, string RunId, int Slot);
    private static readonly Dictionary<string, PotionBinding> PotionBindings = new();
    private static PotionBinding? activePotion;
    private static bool PotionIdle(RunState? run, CombatState? combat, Player? player)
    {
        if (combat != null) return CanDecide(run, combat, player);
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        if (!UiIdle(run, player) || NGame.Instance?.Transition?.InTransition == true
            || current is not Node screen || !IsActive(screen)) return false;
        return current switch {
            NMapScreen map => map.IsOpen && map.IsTravelEnabled && !map.IsTraveling
                && !ReadUiField<bool>(map, "_isInputDisabled") && ReadUiField<Tween?>(map, "_actAnimTween")?.IsRunning() != true,
            NEventRoom room => EventIdle(run, player, room),
            NRestSiteRoom room => RestIdle(run, player, room),
            NMerchantInventory inventory => InventoryReady(inventory),
            NMerchantRoom => ShopIdle(run, player),
            NRewardsScreen => pendingRewardTask == null || pendingRewardTask.IsCompletedSuccessfully,
            _ => false
        };
    }
    private static bool PotionHolderReady(NPotionHolder holder, PotionModel potion) => GodotObject.IsInstanceValid(holder)
        && !holder.IsQueuedForDeletion() && holder.IsNodeReady() && holder.IsVisibleInTree()
        && holder.HasPotion && ReferenceEquals(holder.Potion?.Model, potion)
        && ReadUiField<bool>(holder, "_isUsable")
        && !ReadUiField<bool>(holder, "_disabledUntilPotionRemoved") && !ReadUiField<bool>(holder, "_potionTargeting");
    private static bool SupportedPotionTarget(PotionModel potion) => potion.TargetType is TargetType.Self
        or TargetType.AnyEnemy or TargetType.AllEnemies or TargetType.AnyPlayer or TargetType.None;
    private static Creature?[] PotionTargets(PotionModel potion, CombatState? combat, Player player)
    {
        // Potions treat Self differently from cards; a null request is normalized by EnqueueManualUse.
        var targets = (combat?.Creatures ?? [player.Creature]).Where(potion.IsValidTarget).Cast<Creature?>().ToList();
        if (potion.IsValidTarget(null) || potion.TargetType == TargetType.Self && potion.IsValidTarget(player.Creature)) targets.Add(null);
        return targets.ToArray();
    }
    private static object[] ReadPotions(RunState? run, CombatState? combat, Player? player, bool bind)
    {
        PotionBindings.Clear();
        if (player == null) return [];
        var container = NRun.Instance?.GlobalUi.TopBar.PotionContainer;
        var holders = container != null && container.IsNodeReady()
            ? ReadUiField<List<NPotionHolder>>(container, "_holders") : [];
        var idle = PotionIdle(run, combat, player);
        return player.Potions.Select(potion => {
            var slot = player.GetPotionSlotIndex(potion);
            var holder = holders.FirstOrDefault(h => GodotObject.IsInstanceValid(h) && h.HasPotion && ReferenceEquals(h.Potion?.Model, potion));
            var holderReady = holder != null && PotionHolderReady(holder, potion);
            var use = PotionRules.CanUse(potion.Usage.ToString(), combat != null, idle && holderReady,
                slot >= 0, potion.IsQueued, player.CanUseOrRemovePotions, potion.PassesCustomUsabilityCheck, SupportedPotionTarget(potion));
            var targets = use ? PotionTargets(potion, combat, player) : [];
            use &= targets.Length > 0;
            var discard = PotionRules.CanDiscard(idle && holderReady, slot >= 0, potion.IsQueued, player.CanUseOrRemovePotions);
            var useId = use ? "use_potion_" + Id(potion) : null;
            var discardId = discard ? "discard_potion_" + Id(potion) : null;
            if (bind && use) PotionBindings[useId!] = new(potion, holder!, false, targets, Id(run!), slot);
            if (bind && discard) PotionBindings[discardId!] = new(potion, holder!, true, [null], Id(run!), slot);
            return (object)new { instance_id = Id(potion), entity_id = potion.Id.ToString(), slot,
                name = potion.Title.GetFormattedText(), description = potion.DynamicDescription.GetFormattedText(),
                hover_tips = potion.HoverTips.Select(EventTip).ToArray(), usage = potion.Usage.ToString(), target_type = potion.TargetType.ToString(),
                queued = potion.IsQueued, target_supported = SupportedPotionTarget(potion), usable = use, discardable = discard,
                use_action_id = useId, discard_action_id = discardId };
        }).ToArray();
    }
    private static LegalAction[] PotionActions() => PotionBindings.Select(p => new LegalAction(p.Key,
        p.Value.Discard ? "discard_potion" : "use_potion", null,
        p.Value.Targets.Where(t => t != null).Select(t => Id(t!)).ToArray(), p.Value.Targets.Contains(null),
        new("potion", p.Value.Potion.Id.ToString(), Id(p.Value.Potion)))).ToArray();
    private static void DispatchPotion(PotionBinding binding, Creature? target)
    {
        activePotion = binding;
        binding.Holder.DisableUntilPotionRemoved();
        if (binding.Discard)
        {
            executing = new DiscardPotionGameAction(binding.Potion.Owner, (uint)binding.Slot, CombatManager.Instance.IsInProgress);
            RunManager.Instance.ActionQueueSynchronizer.RequestEnqueue(executing);
        }
        else
        {
            // Observe the action created by the real manual-use entry point; do not reconstruct its effects.
            var queue = RunManager.Instance.ActionQueueSet;
            void Enqueued(GameAction action)
            {
                if (action is UsePotionAction use && use.Player == binding.Potion.Owner && use.PotionIndex == binding.Slot) executing = action;
            }
            queue.ActionEnqueued += Enqueued;
            try { binding.Potion.EnqueueManualUse(target); }
            finally { queue.ActionEnqueued -= Enqueued; }
        }
    }
    private static void ObservePotionCompletion()
    {
        if (activeRequest == null || activePotion == null || tick <= submittedTick) return;
        if (YieldCombatChoice()) return;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var combat = CombatManager.Instance.IsInProgress ? CombatManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        var nextReady = PotionIdle(run, combat, player);
        var outcome = PotionRules.Evaluate(new(run != null && Id(run) == activePotion.RunId,
            executing?.Exception != null || executing?.State == GameActionState.Canceled,
            executing?.State == GameActionState.GatheringPlayerChoice,
            executing?.State == GameActionState.Finished,
            player != null && !player.Potions.Contains(activePotion.Potion),
            !RunManager.Instance.ActionExecutor.IsRunning && RunManager.Instance.ActionQueueSet.IsEmpty,
            nextReady || run?.IsGameOver == true, MegaCrit.Sts2.Core.Nodes.CommonUi.NModalContainer.Instance?.OpenModal != null,
            tick - submittedTick > 180));
        if (outcome is PotionOutcome.Completed or PotionOutcome.Failed)
        {
            var id = activeRequest.RequestId;
            var status = outcome == PotionOutcome.Completed ? "completed" : "failed";
            var error = outcome == PotionOutcome.Failed ? "POTION_ACTION_FAILED" : null;
            Ledger.Update(new(id, status, true, error), release: true);
            activeRequest = null; activePotion = null; executing = null;
            Capture(); Ledger.Update(new(id, status, true, error, SnapshotJson: snapshot));
        }
        else if (outcome == PotionOutcome.NeedsUserInput)
            Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_POTION_FLOW",
                "Resolve this potion selection or modal, then query the existing request ID.", snapshot));
    }
}
