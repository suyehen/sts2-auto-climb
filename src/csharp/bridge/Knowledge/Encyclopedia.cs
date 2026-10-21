using System.Text.Json;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Localization;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;

namespace AutoSlayBridge;

public static partial class Bridge
{
    private static string encyclopedia = "{\"schema_version\":\"1.0\",\"status\":\"not_requested\"}";
    private static int encyclopediaRequested;
    private static Queue<AbstractModel>? encyclopediaQueue;
    private static readonly List<object> encyclopediaEntries = [];
    private static readonly List<object> encyclopediaErrors = [];
    private static string encyclopediaLanguage = "";
    private static int encyclopediaGeneration;
    private static int encyclopediaRefresh;
    private static int encyclopediaExportGeneration;

    private static string RefreshEncyclopedia()
    {
        var generation = Interlocked.Increment(ref encyclopediaGeneration);
        Interlocked.Exchange(ref encyclopediaRefresh, 1);
        Interlocked.Exchange(ref encyclopediaRequested, 1);
        return JsonSerializer.Serialize(new { schema_version = "1.0", status = "refresh_requested", generation });
    }

    // HTTP reads immutable JSON and requests work; model access stays on Godot's main thread.
    private static string ReadEncyclopedia()
    {
        Interlocked.Exchange(ref encyclopediaRequested, 1);
        return Volatile.Read(ref encyclopedia);
    }

    private static object ReferenceCard(CardModel card) => new {
        upgrade_level = card.CurrentUpgradeLevel, name = card.Title,
        description = card.GetDescriptionForPile(PileType.None),
        energy_cost = card.EnergyCost.GetWithModifiers(CostModifiers.All), costs_x = card.EnergyCost.CostsX,
        star_cost = card.BaseStarCost, costs_star_x = card.HasStarCostX, type = card.Type.ToString(), target_type = card.TargetType.ToString(),
        keywords = card.Keywords.Select(k => k.ToString()).ToArray(),
        dynamic_variables = card.DynamicVars.Select(kv => ReferenceVariable(kv.Key, kv.Value)).ToArray(),
        hover_tips = card.HoverTips.Select(EventTip).ToArray()
    };

    private static object ReferenceEntry(AbstractModel model)
    {
        if (model is CardModel canonical)
        {
            var card = canonical.ToMutable();
            var variants = new List<object> { ReferenceCard(card) };
            // Mirror the encyclopedia's single upgrade preview; don't enumerate infinite upgrades.
            if (card.IsUpgradable)
            {
                card.UpgradeInternal();
                card.FinalizeUpgradeInternal();
                variants.Add(ReferenceCard(card));
            }
            return new { entity_id = canonical.Id.ToString(), category = "card", name = canonical.Title,
                model_type = canonical.GetType().FullName,
                rarity = canonical.Rarity.ToString(), pool = canonical.Pool.Id.ToString(),
                max_upgrade_level = canonical.MaxUpgradeLevel, variants };
        }
        if (model is PotionModel potion)
            return new { entity_id = potion.Id.ToString(), category = "potion", name = potion.Title.GetFormattedText(),
                model_type = potion.GetType().FullName,
                description = potion.DynamicDescription.GetFormattedText(), rarity = potion.Rarity.ToString(),
                dynamic_variables = potion.DynamicVars.Select(kv => ReferenceVariable(kv.Key, kv.Value)).ToArray(),
                pool = potion.Pool.Id.ToString(), usage = potion.Usage.ToString(), target_type = potion.TargetType.ToString(),
                hover_tips = potion.HoverTips.Select(EventTip).ToArray() };
        if (model is RelicModel relic)
            return new { entity_id = relic.Id.ToString(), category = "relic", name = relic.Title.GetFormattedText(),
                model_type = relic.GetType().FullName,
                description = relic.DynamicDescription.GetFormattedText(), rarity = relic.Rarity.ToString(),
                pool = ModelDb.AllRelicPools.FirstOrDefault(p => p.AllRelicIds.Contains(relic.Id))?.Id.ToString(),
                pools = ModelDb.AllRelicPools.Where(p => p.AllRelicIds.Contains(relic.Id)).Select(p => p.Id.ToString()).ToArray(),
                dynamic_variables = relic.DynamicVars.Select(kv => ReferenceVariable(kv.Key, kv.Value)).ToArray(),
                hover_tips = relic.HoverTipsExcludingRelic.Select(EventTip).ToArray() };
        throw new InvalidOperationException("Unsupported reference model");
    }

    private static void CaptureEncyclopedia()
    {
        if (Volatile.Read(ref encyclopediaRequested) == 0) return;
        if (Volatile.Read(ref encyclopediaRefresh) != 0)
        {
            if (RunManager.Instance.IsInProgress || ReadMenu(false).Phase != "main_menu") return;
            if (Interlocked.Exchange(ref encyclopediaRefresh, 0) != 0)
            {
                encyclopediaQueue = null;
                encyclopediaEntries.Clear();
                encyclopediaErrors.Clear();
            }
        }
        if (encyclopediaQueue is { Count: 0 }) return;
        // Exporting needs initialized localization/save data, not a loaded run.
        if (encyclopediaQueue == null && (RunManager.Instance.IsInProgress || ReadMenu(false).Phase != "main_menu")) return;
        try
        {
            if (encyclopediaQueue == null)
            {
                encyclopediaExportGeneration = Volatile.Read(ref encyclopediaGeneration);
                encyclopediaLanguage = LocManager.Instance.Language;
                encyclopediaQueue = new Queue<AbstractModel>(ModelDb.AllCards.Cast<AbstractModel>()
                    .Concat(ModelDb.AllPotions).Concat(ModelDb.AllRelics).DistinctBy(m => m.Id.ToString())
                    .OrderBy(m => m.Id.ToString()));
                Volatile.Write(ref encyclopedia, JsonSerializer.Serialize(new {
                    schema_version = "1.0", status = "exporting", game_version = "0.111.0"
                }));
            }
            for (int i = 0; i < 12 && encyclopediaQueue.Count > 0; i++)
            {
                var model = encyclopediaQueue.Dequeue();
                try { encyclopediaEntries.Add(ReferenceEntry(model)); }
                catch (Exception e) { encyclopediaErrors.Add(new { entity_id = model.Id.ToString(), message = e.Message }); }
            }
            if (encyclopediaQueue.Count == 0)
                Volatile.Write(ref encyclopedia, JsonSerializer.Serialize(new {
                    schema_version = "1.0", catalog_version = "3.0.0", bridge_version = "0.29.0",
                    game_version = "0.111.0", language = encyclopediaLanguage, exported_at = DateTimeOffset.UtcNow,
                    game_assembly_sha256 = gameAssemblySha256,
                    status = "complete", generation = encyclopediaExportGeneration, scope = "all_modeldb_reference_models_including_locked_and_tokens", session_epoch = Epoch,
                    entries = encyclopediaEntries, errors = encyclopediaErrors,
                    model_inventory = ModelDb.AllAbstractModelSubtypes.Where(t => !t.IsAbstract && !t.ContainsGenericParameters)
                        .OrderBy(t => t.FullName).Select(t => new {
                            entity_id = ModelDb.GetId(t).ToString(), model_type = t.FullName,
                            category = ModelDb.GetCategory(t), base_type = t.BaseType?.FullName
                        }).ToArray(),
                    limitations = new[] { "reference descriptions are not executable simulation rules", "base and first upgrade preview; repeated/dynamic upgrades require executable rules", "includes locked/deprecated reference models; not a reward eligibility list", "refresh in main menu" }
                }));
        }
        catch (Exception e)
        {
            encyclopediaQueue = new Queue<AbstractModel>();
            Volatile.Write(ref encyclopedia, JsonSerializer.Serialize(new {
                schema_version = "1.0", status = "failed", error = "ENCYCLOPEDIA_EXPORT_FAILED", message = e.Message
            }));
        }
    }
}
