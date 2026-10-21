using System.Reflection;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.State;

public sealed class WorldCloneContext
{
    private readonly Dictionary<object, object> clones = new(ReferenceEqualityComparer.Instance);
    private readonly OwnedStateCopier copier;
    public SimulationWorld Destination { get; }
    internal WorldCloneContext(SimulationWorld source, SimulationWorld destination)
    { Destination = destination; Add(source, destination); Add(source.Models, destination.Models); copier = new(this, source); }
    internal void Add(object source, object copy) => clones.Add(source, copy);
    internal bool TryGet(object source, out object copy) => clones.TryGetValue(source, out copy!);
    internal AbstractModel AllocateModel(AbstractModel source) => copier.AllocateModel(source);
    internal void CopyFields(object source, object copy) => copier.CopyFields(source, copy);
    internal void FillAllocatedState()
    {
        // Realm shells are allocated before traversal to preserve all backreferences. Copy every
        // shell's fields as well, including room/history nodes, without a per-node field whitelist.
        // ModelDb owns fresh synchronization/cache containers, and the world owns a fresh scheduler.
        foreach (var pair in clones.ToArray())
            if (pair.Key is not SimulationWorld and not ModelDb) copier.CopyFields(pair.Key, pair.Value);
        copier.Finish();
    }
    public T Get<T>(T source) where T : class => clones.TryGetValue(source, out var copy) ? (T)copy
        : throw new RuleBoundaryException("clone_contract_missing", "Unmapped state reference: " + source.GetType().FullName);

    internal static void AssertDisjoint(IEnumerable<object> sourceRoots, IEnumerable<object> copyRoots, SimulationWorld source, SimulationWorld copy)
    {
        var sourceObjects = Walk(sourceRoots, source, true);
        foreach (var state in Walk(copyRoots, copy, false))
            if (sourceObjects.Contains(state)) throw new RuleBoundaryException("clone_contract_missing", "World clone retained mutable state: " + state.GetType().FullName);
    }
    private static HashSet<object> Walk(IEnumerable<object> roots, SimulationWorld expectedWorld, bool source)
    {
        var visited = new HashSet<object>(ReferenceEqualityComparer.Instance); var pending = new Stack<object>(roots);
        while (pending.TryPop(out var value))
        {
            if (OwnedStateCopier.IsImmutable(value)) continue;
            var type = value.GetType();
            if (type.IsPrimitive || type.IsEnum || value is decimal or Guid or DateTime or TimeSpan or CancellationToken) continue;
            if (value is Array { Length: 0 }) continue;
            if (value is SimulationWorld world)
            {
                if (!ReferenceEquals(world, expectedWorld)) throw new RuleBoundaryException("foreign_state", "Cloned graph points to another world");
                continue;
            }
            if (value is Delegate function && function.GetInvocationList().All(d => d.Target == null)) continue;
            if (!visited.Add(value)) continue;
            if (value is Delegate callback)
            {
                foreach (var handler in callback.GetInvocationList())
                    if (handler.Target != null && handler.Method.DeclaringType is { } origin && OwnedStateCopier.IsRuleType(origin)) pending.Push(handler.Target);
                continue;
            }
            if (value is Array array)
            {
                foreach (var element in array) if (element != null) pending.Push(element);
                continue;
            }
            if (type == typeof(object)) continue;
            if (type.Assembly != typeof(AbstractModel).Assembly && type.Assembly != typeof(object).Assembly && !OwnedStateCopier.IsRuleType(type))
            {
                if (source) continue;
                throw new RuleBoundaryException("clone_contract_missing", "Unsupported mutable state object: " + type.FullName);
            }
            for (var current = type; current != null && current != typeof(object); current = current.BaseType)
                foreach (var field in current.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                    if (field.GetValue(value) is { } child) pending.Push(child);
        }
        return visited;
    }
}
