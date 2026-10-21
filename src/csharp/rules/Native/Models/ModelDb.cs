using System.Collections.Frozen;
using System.Reflection;
using AutoSlay.Rules.Runtime;
using AutoSlay.Rules.State;
using AutoSlay.Rules.Parameters;
using System.Runtime.ExceptionServices;

namespace AutoSlay.Rules.Native.Models;

/// <summary>Immutable compiled type inventory; not a hand-maintained entity/effect registration table.</summary>
public sealed class ModelRegistry
{
    private readonly FrozenDictionary<ModelId, Type> types;
    public ModelRegistry(params Assembly[] assemblies)
    {
        var discovered = assemblies.Distinct().SelectMany(a => a.GetTypes())
            .Where(t => !t.IsAbstract && !t.ContainsGenericParameters && typeof(AbstractModel).IsAssignableFrom(t)).ToArray();
        var entries = new Dictionary<ModelId, Type>();
        foreach (var type in discovered)
        {
            if (type.GetConstructor(Type.EmptyTypes) == null && type.GetConstructor([typeof(RuleParameters)]) == null)
                throw new InvalidOperationException("Model needs a public default or RuleParameters constructor: " + type.FullName);
            var id = ModelDb.GetId(type);
            if (!entries.TryAdd(id, type)) throw new InvalidOperationException("Duplicate model ID: " + id);
        }
        types = entries.ToFrozenDictionary();
    }
    public IReadOnlyCollection<ModelId> Ids => types.Keys;
    internal Type Require(ModelId id) => types.TryGetValue(id, out var type) ? type
        : throw new RuleBoundaryException("model_not_migrated", "No compiled model: " + id);
    internal AbstractModel Create(ModelId id, RuleParameters? parameters)
    {
        var type = Require(id);
        // Numeric data enters before the native base constructor runs. No mutable process-global
        // world, ID-specific factory or post-construction correction of derived state is needed.
        try
        {
            return type.GetConstructor([typeof(RuleParameters)]) is { } constructor
                ? (AbstractModel)constructor.Invoke([parameters ?? throw new RuleBoundaryException("parameter_missing", "Model constructor requires numeric parameters: " + id)])
                : (AbstractModel)Activator.CreateInstance(type)!;
        }
        catch (TargetInvocationException error) when (error.InnerException != null)
        { ExceptionDispatchInfo.Capture(error.InnerException).Throw(); throw; }
    }
}

/// <summary>Canonical instances belong to one world; there is no process-global ModelDb singleton.</summary>
public sealed class ModelDb(SimulationWorld world, ModelRegistry registry)
{
    private readonly Dictionary<ModelId, AbstractModel> canonical = [];
    private readonly object sync = new();
    internal IEnumerable<AbstractModel> CanonicalInstances { get { lock (sync) return canonical.Values.ToArray(); } }
    internal void RestoreCanonicalSlot(ModelId id, AbstractModel model) { lock (sync) canonical.Add(id, model); }
    public T GetById<T>(ModelId id) where T : AbstractModel
    {
        lock (sync)
        {
            if (!canonical.TryGetValue(id, out var model))
            {
                model = registry.Create(id, world.Parameters);
                model.InitializeCanonical(world); canonical.Add(id, model);
            }
            return model as T ?? throw new RuleBoundaryException("model_type_mismatch", "Unexpected category for " + id);
        }
    }
    public T Relic<T>() where T : RelicModel => GetById<T>(GetId(typeof(T)));
    public T Power<T>() where T : PowerModel => GetById<T>(GetId(typeof(T)));
    public T Card<T>() where T : CardModel => GetById<T>(GetId(typeof(T)));
    public T Singleton<T>() where T : SingletonModel => GetById<T>(GetId(typeof(T)));
    public static ModelId GetId(Type type)
    {
        if (!typeof(AbstractModel).IsAssignableFrom(type) || type == typeof(AbstractModel)) throw new ArgumentException("Not an entity model type");
        var category = type;
        while (category.BaseType != typeof(AbstractModel)) category = category.BaseType ?? throw new ArgumentException("Missing model category");
        return new(ModelId.SlugifyCategory(category.Name), ModelId.Slugify(type.Name));
    }
}
