using System.Net;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using Godot;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Combat.History.Entries;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Modding;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;

namespace AutoSlayBridge;

[ModInitializer(nameof(Initialize))]
public static partial class Bridge
{
    private static readonly string Epoch = Guid.NewGuid().ToString("N");
    private static readonly ConditionalWeakTable<object, Identity> Ids = new();
    private sealed class Identity { public string Id { get; } = Guid.NewGuid().ToString("N"); }
    private static string Id(object value) => Ids.GetValue(value, _ => new Identity()).Id;
    private static string snapshot = "{\"schema_version\":\"1.0\",\"ready\":false,\"phase\":\"initializing\"}";
    private static string lastPayload = "";
    private static long version;
    private static Godot.Timer? timer;
    private static string token = "";
    private static string gameAssemblySha256 = "";

    public static void Initialize()
    {
        try
        {
            var dir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location)!;
            using var release = JsonDocument.Parse(File.ReadAllText(Path.Combine(dir, "..", "..", "release_info.json")));
            var actualVersion = release.RootElement.GetProperty("version").GetString();
            if (actualVersion != "v0.111.0") throw new InvalidOperationException("Unsupported game version: " + actualVersion);
            gameAssemblySha256 = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(
                File.ReadAllBytes(typeof(ModelDb).Assembly.Location))).ToLowerInvariant();
            new HarmonyLib.Harmony("autoslay.observed-combat-quality").PatchAll(typeof(Bridge).Assembly);
            CombatManager.Instance.CombatWon += _ => ObserveCombatResult(true);
            CombatManager.Instance.CombatEnded += _ => ObserveCombatResult(false);
            token = File.ReadAllText(Path.Combine(dir, "bridge-token.txt")).Trim();
            if (token.Length < 32) throw new InvalidOperationException("Invalid bridge token");
            var tree = (SceneTree)Engine.GetMainLoop();
            timer = new Godot.Timer { WaitTime = 0.25, Autostart = true };
            timer.Timeout += Tick;
            tree.Root.CallDeferred(Node.MethodName.AddChild, timer);
            _ = Task.Run(() => BridgeHttpServer.Run(token, () => Volatile.Read(ref snapshot), Ledger, ReadEncyclopedia, RefreshEncyclopedia));
            GD.Print("AutoSlayBridge: combat/reward bridge initialized (control paused) on 127.0.0.1:18765");
        }
        catch (Exception e) { GD.PrintErr("AutoSlayBridge initialization failed: " + e); }
    }

    private static object Entity(AbstractModel model) => new { entity_id = model.Id.ToString() };
    // Public multiset identity only. Never exports draw order or RNG.
    private static string CardVariantKey(CardModel card) => string.Join("|", card.Id.ToString(),
        card.CurrentUpgradeLevel, card.Enchantment?.Id.ToString() ?? "", card.Affliction?.Id.ToString() ?? "",
        card.EnergyCost.GetWithModifiers(CostModifiers.All), card.GetStarCostWithModifiers(),
        card.EnergyCost.GetWithModifiers(CostModifiers.None), card.EnergyCost.HasLocalModifiers,
        card.Enchantment?.Amount);
    private static object Card(CardModel card) => new {
        instance_id = Id(card), entity_id = card.Id.ToString(), name = card.Title,
        variant_key = CardVariantKey(card),
        upgrade_level = card.CurrentUpgradeLevel, type = card.Type.ToString(), target_type = card.TargetType.ToString(),
        energy_cost = card.EnergyCost.GetWithModifiers(CostModifiers.All), costs_x = card.EnergyCost.CostsX,
        base_energy_cost = card.EnergyCost.GetWithModifiers(CostModifiers.None), has_local_cost_modifiers = card.EnergyCost.HasLocalModifiers,
        star_cost = card.GetStarCostWithModifiers(), costs_star_x = card.HasStarCostX,
        keywords = card.Keywords.Select(k => k.ToString()).ToArray(),
        dynamic_variables = card.DynamicVars.Select(kv => ReferenceVariable(kv.Key, kv.Value)).ToArray(),
        modifiers_complete = true,
        enchantment_id = card.Enchantment?.Id.ToString(), affliction_id = card.Affliction?.Id.ToString(),
        enchantment_amount = card.Enchantment?.Amount,
        description = card.GetDescriptionForPile(card.Pile?.Type ?? PileType.Deck, null)
    };
    private static CardPresentation TooltipCard(CardModel card) => new(card.Id.ToString(), card.Title,
        card.CurrentUpgradeLevel, card.Type.ToString(), card.TargetType.ToString(),
        card.EnergyCost.GetWithModifiers(CostModifiers.All), card.EnergyCost.CostsX,
        card.GetDescriptionForPile(card.Pile?.Type ?? PileType.Deck, null));
    private static object Creature(Creature creature, CombatState? combat) => new {
        instance_id = Id(creature), entity_id = creature.ModelId.ToString(), name = creature.Name,
        hp = creature.CurrentHp, max_hp = creature.MaxHp, block = creature.Block,
        runtime_state = creature.Monster == null ? null : RuntimeFields.Read(creature.Monster),
        current_move_id = creature.Monster?.NextMove?.StateId,
        hidden_power_ids = creature.Powers.Where(p => !p.IsVisible).Select(p => p.Id.ToString()).ToArray(),
        powers = creature.Powers.Where(p => p.IsVisible).Select(p => new { entity_id = p.Id.ToString(), amount = p.Amount,
            name = p.Title.GetFormattedText(), skip_next_duration_tick = p.SkipNextDurationTick,
            hover_tips = p.HoverTips.Select(EventTip).ToArray() }).ToArray(),
        intents = creature.Monster?.NextMove == null || combat == null ? Array.Empty<object>() : creature.Monster.NextMove.Intents.Select(i => (object)new {
            type = i.IntentType.ToString(), label = i.GetIntentLabel(combat.PlayerCreatures, creature).GetFormattedText(),
            hover_tip = i.HasIntentTip ? EventTip(i.GetHoverTip(combat.PlayerCreatures, creature)) : null,
            attack_damage = i is MegaCrit.Sts2.Core.MonsterMoves.Intents.AttackIntent attack ? (int?)attack.GetSingleDamage(combat.PlayerCreatures, creature) : null,
            attack_repeats = i is MegaCrit.Sts2.Core.MonsterMoves.Intents.AttackIntent repeated ? (int?)repeated.Repeats : null
        }).ToArray()
    };

    private static object? CombatCounters(ICombatState? combat, Player? player)
    {
        if (combat == null || player == null) return null;
        var history = CombatManager.Instance.History;
        var plays = history.CardPlaysFinished.Where(e => e.CardPlay.Player == player).ToArray();
        var turnPlays = plays.Where(e => e.HappenedThisTurn(combat)).ToArray();
        var exhausted = history.Entries.OfType<CardExhaustedEntry>().Where(e => e.Actor == player.Creature).ToArray();
        return new {
            cards_played = turnPlays.Length,
            attacks_played = turnPlays.Count(e => e.CardPlay.Card.Type == CardType.Attack),
            skills_played = turnPlays.Count(e => e.CardPlay.Card.Type == CardType.Skill),
            combat_cards_played = plays.Length,
            cards_exhausted = exhausted.Length,
            turn_cards_exhausted = exhausted.Count(e => e.HappenedThisTurn(combat)),
            turn_cards_drawn = history.Entries.OfType<CardDrawnEntry>().Count(e => e.Actor == player.Creature && e.HappenedThisTurn(combat) && !e.FromHandDraw)
        };
    }

    // Runs only on Godot's main thread. The HTTP thread serves immutable JSON.
    private static void Capture()
    {
        try
        {
            var run = RunManager.Instance.IsInProgress ? RunManager.Instance.DebugOnlyGetState() : null;
            var manager = CombatManager.Instance;
            var combat = manager.IsInProgress ? manager.DebugOnlyGetState() : null;
            var player = run?.Players.Count == 1 ? run.Players[0] : null;
            var pcs = player?.PlayerCombatState;
            var combatReady = CanDecide(run, combat, player);
            var rewardView = ReadRewards(run, player, !Ledger.Paused);
            var menuView = ReadMenu(!Ledger.Paused);
            var mapView = ReadMap(run, !Ledger.Paused);
            var eventView = ReadEvent(run, player, !Ledger.Paused);
            var combatChoiceView = ReadCombatChoice(run, player, !Ledger.Paused);
            var selectionView = combatChoiceView ?? ReadSelection(run, player, !Ledger.Paused);
            if (combatChoiceView != null) SelectionBindings.Clear();
            var shopView = ReadShop(run, player, !Ledger.Paused);
            var restView = ReadRest(run, player, !Ledger.Paused);
            var treasureView = ReadTreasure(run, player, !Ledger.Paused);
            var potionView = ReadPotions(run, combat, player, !Ledger.Paused);
            var lifecycleActions = BuildLifecycleActions(run, combat, player, !Ledger.Paused);
            var decisionReady = combatReady || rewardView.DecisionReady || menuView.DecisionReady || mapView.DecisionReady || eventView.DecisionReady || selectionView.DecisionReady || shopView.DecisionReady || restView.DecisionReady || treasureView.DecisionReady || lifecycleActions.Length > 0;
            var actions = BuildActions(combat, player, combatReady && !Ledger.Paused)
                .Concat(RewardActions()).Concat(MenuActions()).Concat(MapActions()).Concat(EventActions()).Concat(SelectionActions()).Concat(ShopActions()).Concat(RestActions()).Concat(TreasureActions()).Concat(PotionActions()).Concat(lifecycleActions).ToArray();
            if (suspendedCombatRequest != null)
            {
                decisionReady = combatChoiceView?.DecisionReady == true;
                actions = CombatChoiceActions();
            }
            if (suspendedEventRequest != null)
            {
                actions = actions.Where(a => EventRewardChildAction(a.ActionId)).ToArray();
                decisionReady = actions.Length > 0;
            }
            foreach (var action in actions)
                ActionCatalog.ValidateBinding(action.Type, action.AllowedTargetIds, action.AllowsNoTarget);
            var payload = JsonSerializer.Serialize(new {
                action_catalog = new { version = ActionCatalog.Version, sha256 = ActionCatalog.Sha256 },
                phase = menuView.Phase ?? (MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext.ActiveScreenContext.Instance.GetCurrentScreen() is MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen.NGameOverScreen ? "game_over" : null) ?? (run == null ? "no_run" : (treasureView.ScreenId != null ? "treasure" : null) ?? (restView.ScreenId != null ? "rest_site" : null) ?? (shopView.ScreenId != null ? "shop" : null) ?? (selectionView.ScreenId != null ? "card_selection" : null) ?? rewardView.Phase ?? (mapView.Active ? "map" : null) ?? (eventView.Supported && IsActive(MegaCrit.Sts2.Core.Nodes.Rooms.NEventRoom.Instance!) ? "event" : null) ?? (combat != null ? "combat" : "outside_combat")),
                command_context_id = run == null ? menuView.ContextId : Id(run),
                combat_instance_id = combat == null ? null : Id(combat),
                combat_quality = CombatQuality(run, combat),
                run_id = run == null ? null : Id(run), room_type = run?.CurrentRoom?.GetType().Name,
                // Saved start time + seed distinguish runs and survive loading the same save.
                // Export only a hash, never RNG state or draw order.
                run_history_id = run == null ? null : Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(
                    JsonSerializer.Serialize(new { seed = run.Rng.StringSeed,
                        started = ReadUiField<long>(RunManager.Instance, "_startTime"),
                        characters = run.Players.Select(p => p.Character.Id.ToString()).ToArray(),
                        ascension = run.AscensionLevel })))).ToLowerInvariant(),
                act_count = run?.Acts.Count,
                floor = run?.TotalFloor, ascension = run?.AscensionLevel,
                player = player == null ? null : new {
                    character = player.Character.Id.ToString(), creature = Creature(player.Creature, null), gold = player.Gold,
                    energy = pcs?.Energy, stars = pcs?.Stars, turn = pcs?.TurnNumber, turn_phase = pcs?.Phase.ToString(),
                    combat_counters = CombatCounters(combat, player),
                    pets = pcs?.Pets.Select(c => Creature(c, combat)).ToArray(),
                    hand_limit = CardPile.MaxCardsInHand,
                    deck = player.Deck.Cards.Select(Card).ToArray(), relics = player.Relics.Select(r => new { entity_id = r.Id.ToString(),
                        name = r.Title.GetFormattedText(), description = r.DynamicDescription.GetFormattedText(),
                        status = r.Status.ToString(), counter = r.ShowCounter ? (int?)r.DisplayAmount : null,
                        runtime_state = RuntimeFields.ReadRelicCounters(r),
                        dynamic_variables = r.DynamicVars.Select(kv => ReferenceVariable(kv.Key, kv.Value)).ToArray(),
                        hover_tips = r.HoverTipsExcludingRelic.Select(EventTip).ToArray() }).ToArray(),
                    potions = potionView, potion_slots = player.MaxPotionCount, hand = pcs?.Hand.Cards.Select(Card).ToArray(),
                    discard = pcs?.DiscardPile.Cards.Select(Card).ToArray(), exhaust = pcs?.ExhaustPile.Cards.Select(Card).ToArray(),
                    // Expose composition only: never reveal unknown draw order or RNG.
                    draw_variants = pcs?.DrawPile.Cards.GroupBy(CardVariantKey).OrderBy(g => g.Key)
                        .Select(g => new { variant_key = g.Key, entity_id = g.First().Id.ToString(), count = g.Count(),
                            card = Card(g.First()) }).ToArray(),
                    draw_composition = pcs?.DrawPile.Cards.GroupBy(c => c.Id.ToString()).OrderBy(g => g.Key)
                        .Select(g => new { entity_id = g.Key, count = g.Count() }).ToArray()
                },
                enemies = combat?.Enemies.Select(c => Creature(c, combat)).ToArray(),
                reward_screen = rewardView,
                menu = menuView,
                map = mapView,
                event_screen = eventView,
                pending_event_action = suspendedEventRequest == null ? null : new {
                    request_id = suspendedEventRequest.RequestId, action_id = suspendedEventRequest.ActionId,
                    status = "awaiting_choice"
                },
                card_selection = selectionView,
                pending_combat_action = suspendedCombatRequest == null ? null : new {
                    request_id = suspendedCombatRequest.RequestId, action_id = suspendedCombatRequest.ActionId,
                    status = executing?.State.ToString()
                },
                shop = shopView,
                rest_site = restView,
                treasure = treasureView,
                player_actions_disabled = manager.PlayerActionsDisabled,
                decision_ready = decisionReady, control_mode = Ledger.Paused ? "paused" : "auto",
                legal_actions = actions,
                limitations = new[] { "single-player combat and basic rewards", "combat card/potion choices support NPlayerHand SimpleSelect/UpgradeSelect, NSimpleCardSelectScreen, NCombatPileCardSelectScreen and NChooseACardSelectionScreen; other choices and linked rewards need additional backend adapters", "custom event layouts not supported", "intent descriptions contain only visible game tooltips; generic buff/debuff intents may omit exact effects" }
            }, BridgeHttpServer.Options);
            if (payload != lastPayload) { version++; lastPayload = payload; }
            using var doc = JsonDocument.Parse(payload);
            Volatile.Write(ref snapshot, JsonSerializer.Serialize(new {
                schema_version = "1.0", bridge_version = "0.30.0", game_version = "0.111.0",
                game_assembly_sha256 = gameAssemblySha256,
                session_epoch = Epoch, state_version = version, ready = decisionReady && !Ledger.Paused && !Ledger.Busy, observation_only = false,
                busy = Ledger.Busy,
                // Clocks are outside versioned state: elapsed time alone must not stale actions.
                run_clock = run == null ? null : new {
                    start_time_unix = ReadUiField<long>(RunManager.Instance, "_startTime"),
                    run_time_seconds = RunManager.Instance.RunTime,
                    result = RunManager.Instance.IsAbandoned ? "abandoned" :
                        MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext.ActiveScreenContext.Instance.GetCurrentScreen()
                            is MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen.NGameOverScreen && run.CurrentRoom?.IsVictoryRoom == true
                            ? "win" : run.IsGameOver ? "loss" : null,
                    result_source = "game", game_paused = RunManager.Instance.IsPaused
                },
                captured_at = DateTimeOffset.UtcNow, state = doc.RootElement.Clone()
            }));
        }
        catch (Exception e)
        {
            ActionBindings.Clear();
            UiBindings.Clear();
            MenuBindings.Clear();
            MapBindings.Clear();
            EventBindings.Clear();
            SelectionBindings.Clear();
            CombatChoiceBindings.Clear();
            currentCombatChoiceReady = false;
            ShopBindings.Clear();
            RestBindings.Clear();
            PotionBindings.Clear();
            LifecycleBindings.Clear();
            TreasureBindings.Clear();
            Volatile.Write(ref snapshot, JsonSerializer.Serialize(new { schema_version = "1.0", ready = false,
                error = "STATE_READ_FAILED", message = e.Message, session_epoch = Epoch }));
        }
    }

}
