using System.Reflection;
using Godot;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Runs;

namespace AutoSlayBridge;
public static partial class Bridge
{
    private sealed record ShopView(string? ScreenId, bool Open, bool DecisionReady, object[] Items, object[] Controls);
    private sealed record ShopBinding(Node Screen, string Kind, string RunId, Func<Task?> Dispatch, MerchantEntry? Entry = null);
    private static readonly Dictionary<string, ShopBinding> ShopBindings = new();
    private static ShopBinding? activeShop;
    private static Task? shopTask;
    private static bool shopPurchased;
    private static Action<PurchaseStatus, MerchantEntry>? shopSuccessHandler;
    private static Action<PurchaseStatus>? shopFailureHandler;
    private static bool ShopIdle(RunState? run, Player? player) => UiIdle(run, player)
        && NGame.Instance?.Transition?.InTransition != true;
    private static bool InventoryReady(NMerchantInventory inventory) => inventory.GetType() == typeof(NMerchantInventory)
        && inventory.IsOpen && IsActive(inventory) && inventory.IsNodeReady()
        && !ReadUiField<bool>(inventory, "_isInputBlocked")
        && ReadUiField<Tween?>(inventory, "_inventoryTween")?.IsRunning() != true;
    private static object ShopItem(MerchantEntry entry) => entry switch {
        MerchantCardEntry card when card.IsStocked && card.CreationResult != null => new { card = Card(card.CreationResult.Card) },
        MerchantRelicEntry relic when relic.IsStocked && relic.Model != null => new { entity_id = relic.Model.Id.ToString(),
            name = relic.Model.Title.GetFormattedText(), description = relic.Model.DynamicDescription.GetFormattedText(),
            dynamic_variables = relic.Model.DynamicVars.Select(kv => ReferenceVariable(kv.Key, kv.Value)).ToArray(),
            hover_tips = relic.Model.HoverTipsExcludingRelic.Select(EventTip).ToArray() },
        MerchantPotionEntry potion when potion.IsStocked && potion.Model != null => new { entity_id = potion.Model.Id.ToString(),
            name = potion.Model.Title.GetFormattedText(), description = potion.Model.DynamicDescription.GetFormattedText(),
            dynamic_variables = potion.Model.DynamicVars.Select(kv => ReferenceVariable(kv.Key, kv.Value)).ToArray() },
        MerchantCardRemovalEntry => new { description = "Choose one removable deck card; payment applies only after confirmation." },
        _ => new { unavailable = true }
    };
    private static Task StartShopPurchase(NMerchantSlot slot) => (Task)(typeof(NMerchantSlot)
        .GetMethod("OnSelected", BindingFlags.Instance | BindingFlags.NonPublic)
        ?? throw new MissingMethodException("NMerchantSlot.OnSelected")).Invoke(slot, null)!;
    private static ShopView ReadShop(RunState? run, Player? player, bool bind)
    {
        ShopBindings.Clear();
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        var room = NMerchantRoom.Instance;
        var items = new List<object>();
        var controls = new List<object>();
        var ready = false;
        var idle = ShopIdle(run, player);
        void Control(Node screen, NButton button, string kind)
        {
            var enabled = idle && IsActive(screen) && SelectionButtonReady(button);
            var actionId = kind + "_" + Id(screen);
            controls.Add(new { type = kind, available = enabled, action_id = enabled ? actionId : null });
            if (enabled)
            {
                ready = true;
                if (bind) ShopBindings[actionId] = new(screen, kind, Id(run!), () => { button.ForceClick(); return null; });
            }
        }
        if (room != null && current == room && room.IsNodeReady())
        {
            Control(room, room.MerchantButton, "open_shop");
            Control(room, room.ProceedButton, "proceed_shop");
        }
        if (current is NMerchantInventory inventory && inventory.GetType() == typeof(NMerchantInventory) && inventory.IsNodeReady())
        {
            idle &= InventoryReady(inventory);
            foreach (var slot in inventory.GetAllSlots().Where(s => s.IsNodeReady() && !s.IsQueuedForDeletion()))
            {
                var entry = slot.Entry;
                var supported = entry is MerchantCardEntry or MerchantRelicEntry or MerchantPotionEntry or MerchantCardRemovalEntry;
                var capacity = entry is not MerchantPotionEntry || player != null && player.HasOpenPotionSlots;
                var enabled = idle && supported && slot.IsVisibleInTree() && entry.IsStocked && entry.EnoughGold && capacity;
                var actionId = "buy_" + Id(inventory) + "_" + Id(entry);
                items.Add(new { item_id = Id(entry), type = entry.GetType().Name, cost = entry.Cost,
                    base_cost = ReadUiField<int>(entry, "_cost"),
                    stocked = entry.IsStocked, affordable = entry.EnoughGold, capacity_ok = capacity, supported,
                    details = ShopItem(entry), available = enabled, action_id = enabled ? actionId : null });
                if (enabled)
                {
                    ready = true;
                    if (bind) ShopBindings[actionId] = new(inventory, "buy_shop_item", Id(run!), () => StartShopPurchase(slot), entry);
                }
            }
            Control(inventory, ReadUiField<NBackButton>(inventory, "_backButton"), "close_shop");
        }
        return new(current is NMerchantRoom or NMerchantInventory ? Id(current) : null,
            current is NMerchantInventory { IsOpen: true }, ready, items.ToArray(), controls.ToArray());
    }
    private static LegalAction[] ShopActions() => ShopBindings.Select(p => new LegalAction(p.Key, p.Value.Kind, null, [], true,
        new(ActionCatalog.Require(p.Value.Kind).SourceKinds[0], null, Id((object?)p.Value.Entry ?? p.Value.Screen)))).ToArray();
    private static void DispatchShop(ShopBinding binding)
    {
        activeShop = binding; shopPurchased = binding.Entry == null;
        if (binding.Entry != null)
        {
            shopSuccessHandler = (_, _) => shopPurchased = true;
            shopFailureHandler = _ => shopPurchased = false;
            binding.Entry.PurchaseCompleted += shopSuccessHandler;
            binding.Entry.PurchaseFailed += shopFailureHandler;
        }
        shopTask = binding.Dispatch();
    }
    private static void ObserveShopCompletion()
    {
        if (activeRequest == null || activeShop == null || tick <= submittedTick) return;
        var binding = activeShop;
        var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
        var player = run?.Players.Count == 1 ? run.Players[0] : null;
        var current = ActiveScreenContext.Instance.GetCurrentScreen();
        var childReady = current is NCardGridSelectionScreen child && SelectionIdle(run, player, child);
        var nextReady = binding.Kind switch {
            "open_shop" => current is NMerchantInventory opened && InventoryReady(opened),
            "close_shop" => current is NMerchantRoom room && IsActive(room)
                && ReadUiField<Tween?>(room.Inventory, "_inventoryTween")?.IsRunning() != true,
            "proceed_shop" => current is NMapScreen map && IsActive(map) && map.IsTravelEnabled,
            _ => current is NMerchantInventory inventory && InventoryReady(inventory)
        };
        var outcome = ShopRules.Evaluate(new(run != null && Id(run) == binding.RunId,
            shopTask?.IsFaulted == true || shopTask?.IsCanceled == true, shopTask == null || shopTask.IsCompletedSuccessfully,
            shopPurchased, childReady, nextReady, ShopIdle(run, player), NModalContainer.Instance?.OpenModal != null,
            tick - submittedTick > 180));
        if (outcome == ShopOutcome.SelectionOpened)
        {
            pendingSelectionEffects = shopTask == null ? [] : [shopTask];
            FinishShop("completed", message: "Shop opened a card selector; payment/effects await selection confirmation.");
        }
        else if (outcome == ShopOutcome.Completed) FinishShop("completed");
        else if (outcome == ShopOutcome.Failed) FinishShop("failed", "SHOP_ACTION_FAILED");
        else if (outcome == ShopOutcome.NeedsUserInput)
            Ledger.Update(new(activeRequest.RequestId, "needs_user_input", true, "UNSUPPORTED_SHOP_FLOW",
                "Resolve the shop sub-selection or modal, then query this request ID.", snapshot));
    }
    private static void FinishShop(string status, string? error = null, string? message = null)
    {
        var id = activeRequest!.RequestId;
        if (activeShop?.Entry is MerchantEntry entry)
        {
            entry.PurchaseCompleted -= shopSuccessHandler; entry.PurchaseFailed -= shopFailureHandler;
        }
        Ledger.Update(new(id, status, true, error, message), release: true);
        activeRequest = null; activeShop = null; shopTask = null; shopSuccessHandler = null; shopFailureHandler = null;
        Capture();
        Ledger.Update(new(id, status, true, error, message, snapshot));
    }
}
