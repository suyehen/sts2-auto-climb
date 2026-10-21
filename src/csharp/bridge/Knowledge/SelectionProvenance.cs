using System.Reflection;
using System.Runtime.CompilerServices;
using HarmonyLib;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;

namespace AutoSlayBridge;
public static partial class Bridge
{
    private static readonly SelectionObservation SelectionObservations = new();
    private static readonly ConditionalWeakTable<EventOption, EventModel> OptionSources = new();

    private static SelectionSource ModelSelectionSource(AbstractModel model, string callback, string? key = null) =>
        new(model.Id.ToString(), model.GetType().FullName!, Id(model), callback, key, model);

    private static string? SelectionOperation(string api) => api switch {
        nameof(CardSelectCmd.FromDeckForUpgrade) or nameof(CardSelectCmd.FromHandForUpgrade) => "upgrade",
        nameof(CardSelectCmd.FromDeckForRemoval) => "remove",
        nameof(CardSelectCmd.FromDeckForTransformation) => "transform",
        nameof(CardSelectCmd.FromDeckForEnchantment) => "enchant",
        nameof(CardSelectCmd.FromHandForDiscard) => "discard",
        _ => null
    };

    private static object? SelectionSemantics(object token)
    {
        var call = SelectionObservations.Read(token);
        if (call == null) return null;
        var source = call.Source;
        return new { schema_version = "observed-selection/1", operation = call.Operation,
            api_path = call.ApiPath, source_model_id = source?.ModelId, source_model_type = source?.ModelType,
            source_instance_id = source?.InstanceId, source_callback = source?.Callback,
            source_option_key = source?.OptionKey,
            source_dynamic_variables = source?.Model is EventModel model ? model.DynamicVars.Select(kv =>
                (object)new { name = kv.Key, value = kv.Value.BaseValue,
                    int_value = kv.Value.IntValue,
                    decimal_value = kv.Value.BaseValue.ToString(System.Globalization.CultureInfo.InvariantCulture) }).ToArray() : null,
            source_runtime_state = source?.Model is EventModel eventModel ? RuntimeFields.Read(eventModel) : null,
            observation_scope = "actual selection API; caller continuation is not inferred",
            continuation_observed = false };
    }

    [HarmonyPatch]
    private static class ObserveSelectionApi
    {
        private static IEnumerable<MethodBase> TargetMethods() => typeof(CardSelectCmd).GetMethods(BindingFlags.Public | BindingFlags.Static)
            .Where(m => m.Name.StartsWith("From", StringComparison.Ordinal));
        private static void Prefix(MethodBase __originalMethod, object[] __args, out IDisposable __state)
        {
            var parameters = __originalMethod.GetParameters();
            var source = parameters.Select((p, i) => (p, i)).FirstOrDefault(v => v.p.Name == "source");
            var model = source.p == null ? null : __args[source.i] as AbstractModel;
            __state = SelectionObservations.EnterCommand("CardSelectCmd." + __originalMethod.Name,
                SelectionOperation(__originalMethod.Name), model == null ? null : ModelSelectionSource(model, __originalMethod.Name));
        }
        private static Exception? Finalizer(Exception? __exception, IDisposable? __state)
        { __state?.Dispose(); return __exception; }
    }

    [HarmonyPatch]
    private static class ObserveSelectionScreen
    {
        private static IEnumerable<MethodBase> TargetMethods() => new[] {
            typeof(NDeckCardSelectScreen), typeof(NDeckUpgradeSelectScreen), typeof(NDeckEnchantSelectScreen),
            typeof(NDeckTransformSelectScreen), typeof(NSimpleCardSelectScreen), typeof(NCombatPileCardSelectScreen),
            typeof(NChooseACardSelectionScreen)
        }.SelectMany(t => t.GetMethods(BindingFlags.Public | BindingFlags.Static | BindingFlags.DeclaredOnly)
            .Where(m => (m.Name is "Create" or "ShowScreen") && t.IsAssignableFrom(m.ReturnType)));
        private static void Postfix(object __result)
        {
            if (__result == null) return;
            var token = ReadUiField<TaskCompletionSource<IEnumerable<CardModel>>>(__result, "_completionSource");
            SelectionObservations.Capture(token);
        }
    }

    [HarmonyPatch(typeof(NPlayerHand), nameof(NPlayerHand.SelectCards))]
    private static class ObserveHandSelection
    {
        private static void Postfix(NPlayerHand __instance) => SelectionObservations.Capture(
            ReadUiField<TaskCompletionSource<IEnumerable<CardModel>>>(__instance, "_selectionCompletionSource"));
    }

    [HarmonyPatch]
    private static class ObserveEventOptionSource
    {
        private static IEnumerable<MethodBase> TargetMethods() => typeof(EventOption).GetConstructors();
        private static void Postfix(EventOption __instance, object[] __args)
        {
            var model = __args.OfType<EventModel>().FirstOrDefault();
            if (model == null && __args.OfType<EventOption>().FirstOrDefault() is { } copy)
                OptionSources.TryGetValue(copy, out model);
            if (model != null) OptionSources.AddOrUpdate(__instance, model);
        }
    }

    [HarmonyPatch(typeof(EventOption), nameof(EventOption.Chosen))]
    private static class ObserveEventSelectionSource
    {
        private static void Prefix(EventOption __instance, out IDisposable? __state)
        {
            __state = null;
            if (!OptionSources.TryGetValue(__instance, out var model)) return;
            __state = SelectionObservations.EnterSource(ModelSelectionSource(model, "EventOption.Chosen", __instance.TextKey));
        }
        private static Exception? Finalizer(Exception? __exception, IDisposable? __state)
        { __state?.Dispose(); return __exception; }
    }

    // BeforeChosen may open its own selector. Observe the actual callback entry
    // instead of attributing every selector inside Chosen to OnChosen's delegate.
    [HarmonyPatch]
    private static class ObserveEventCallbackEntry
    {
        private static IEnumerable<MethodBase> TargetMethods() => typeof(EventModel).Assembly.GetTypes()
            .Where(t => !t.IsAbstract && typeof(EventModel).IsAssignableFrom(t))
            .SelectMany(t => t.GetMethods(BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.DeclaredOnly))
            .Where(m => !m.IsAbstract && !m.ContainsGenericParameters && typeof(Task).IsAssignableFrom(m.ReturnType));
        private static void Prefix(EventModel __instance, MethodBase __originalMethod, out IDisposable __state) =>
            __state = SelectionObservations.EnterCallback(ModelSelectionSource(__instance, __originalMethod.Name));
        private static Exception? Finalizer(Exception? __exception, IDisposable? __state)
        { __state?.Dispose(); return __exception; }
    }

    [HarmonyPatch]
    private static class ObserveModelSelectionSource
    {
        private static IEnumerable<MethodBase> TargetMethods() => new[] {
            AccessTools.Method(typeof(CardModel), nameof(CardModel.OnPlayWrapper)),
            AccessTools.Method(typeof(PotionModel), nameof(PotionModel.OnUseWrapper)),
            AccessTools.Method(typeof(EventModel), "SelectCardsToAddToDeckFromGrid")
        };
        private static void Prefix(AbstractModel __instance, MethodBase __originalMethod, out IDisposable __state) =>
            __state = __originalMethod.Name == "SelectCardsToAddToDeckFromGrid"
                ? SelectionObservations.EnterCommand("EventModel.SelectCardsToAddToDeckFromGrid", "add",
                    ModelSelectionSource(__instance, __originalMethod.Name))
                : SelectionObservations.EnterSource(ModelSelectionSource(__instance, __originalMethod.Name));
        private static Exception? Finalizer(Exception? __exception, IDisposable? __state)
        { __state?.Dispose(); return __exception; }
    }

    [HarmonyPatch]
    private static class ObserveRestSelectionSource
    {
        private static IEnumerable<MethodBase> TargetMethods() => typeof(RestSiteOption).Assembly.GetTypes()
            .Where(t => !t.IsAbstract && typeof(RestSiteOption).IsAssignableFrom(t))
            .Select(t => t.GetMethod("OnSelect", BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly))
            .Where(m => m != null).Cast<MethodBase>();
        private static void Prefix(RestSiteOption __instance, out IDisposable __state) =>
            __state = SelectionObservations.EnterSource(new(null, __instance.GetType().FullName!, Id(__instance), "OnSelect"));
        private static Exception? Finalizer(Exception? __exception, IDisposable? __state)
        { __state?.Dispose(); return __exception; }
    }
}
