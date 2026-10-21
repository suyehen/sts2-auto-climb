using System.Reflection;
using System.Runtime.CompilerServices;
using AutoSlay.Rules.Native.Combat;
using AutoSlay.Rules.Native.Entities.Creatures;
using AutoSlay.Rules.Native.Entities.Players;
using AutoSlay.Rules.Native.Models;
using AutoSlay.Rules.Native.Runs;
using AutoSlay.Rules.Parameters;
using AutoSlay.Rules.Runtime;

namespace AutoSlay.Rules.State;

/// <summary>
/// A world fork copies existing state, not native entity-creation lifecycle. It never invokes model
/// constructors, MutableClone, DeepCloneFields, AfterCloned, property setters or custom enumerators.
/// Unsupported state layouts fail rather than sharing mutable objects across candidates.
/// </summary>
internal sealed class OwnedStateCopier(WorldCloneContext context, SimulationWorld source)
{
    private readonly HashSet<object> filled = new(ReferenceEqualityComparer.Instance);
    private readonly Dictionary<Type, FieldInfo[]> fields = [];
    private readonly Queue<(object Source, object Copy)> deferredTargets = new();
    internal void Finish()
    {
        while (deferredTargets.TryDequeue(out var pair))
        {
            if (pair.Source is Array array) CopyArrayElements(array, (Array)pair.Copy);
            else { CopyFields(pair.Source, pair.Copy); RehashIdentityCollection(pair.Source, pair.Copy); }
        }
    }
    private FieldInfo[] Fields(Type type)
    {
        if (fields.TryGetValue(type, out var found)) return found;
        var all = new List<FieldInfo>();
        for (var current = type; current != null && current != typeof(object); current = current.BaseType)
            all.AddRange(current.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly));
        return fields[type] = all.ToArray();
    }
    internal AbstractModel AllocateModel(AbstractModel model)
    {
        if (context.TryGet(model, out var existing)) return (AbstractModel)existing;
        if (model.World != source || source.Registry.Require(model.Id) != model.GetType())
            throw new RuleBoundaryException("foreign_state", "Model state does not belong to the source registry/world");
        var copy = (AbstractModel)RuntimeHelpers.GetUninitializedObject(model.GetType());
        context.Add(model, copy);
        if (model.IsCanonical) context.Destination.Models.RestoreCanonicalSlot(model.Id, copy);
        return copy;
    }
    internal void CopyFields(object original, object copy)
    {
        if (!filled.Add(original)) return;
        foreach (var field in Fields(original.GetType()))
        {
            var value = field.GetValue(original);
            var isEvent = field.DeclaringType!.GetEvent(field.Name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly) != null;
            var cloned = value is Delegate callback ? CopyDelegate(callback, isEvent) : Copy(value);
            field.SetValue(copy, cloned);
        }
    }
    private object? Copy(object? value, bool deferFields = false)
    {
        if (value == null || IsImmutable(value)) return value;
        if (value is Delegate callback) return CopyDelegate(callback, false);
        if (context.TryGet(value, out var mapped)) return mapped;
        var type = value.GetType();
        if (value is SimulationWorld or RunState or Player or Creature or CombatState or ModelDb)
            throw new RuleBoundaryException("foreign_state", "Unmapped world entity reference: " + type.FullName);
        if (value is AbstractModel model)
        {
            var cloned = AllocateModel(model);
            if (deferFields) deferredTargets.Enqueue((model, cloned)); else CopyFields(model, cloned);
            return cloned;
        }
        if (value is Array array)
        {
            var cloned = (Array)array.Clone(); context.Add(array, cloned);
            if (deferFields) deferredTargets.Enqueue((array, cloned)); else CopyArrayElements(array, cloned);
            return cloned;
        }
        ValidateType(type);
        var copy = RuntimeHelpers.GetUninitializedObject(type); context.Add(value, copy);
        if (deferFields) { deferredTargets.Enqueue((value, copy)); return copy; }
        CopyFields(value, copy);
        RehashIdentityCollection(value, copy); return copy;
    }
    private void CopyArrayElements(Array array, Array cloned)
    {
        var indices = Enumerable.Range(0, array.Rank).Select(array.GetLowerBound).ToArray();
        void Dimension(int dimension)
        {
            for (var index = array.GetLowerBound(dimension); index <= array.GetUpperBound(dimension); index++)
            {
                indices[dimension] = index;
                if (dimension + 1 < array.Rank) Dimension(dimension + 1);
                else cloned.SetValue(Copy(array.GetValue(indices)), indices);
            }
        }
        if (array.Length != 0) Dimension(0);
    }
    private Delegate? CopyDelegate(Delegate callback, bool observerEvent)
    {
        // Allocate closure targets without traversing their fields first. A closure can hold the
        // very same delegate in a captured local, so the delegate must enter the identity map before
        // filling that target; otherwise reference aliases are broken despite equivalent invocation.
        var invocations = callback.GetInvocationList();
        var retained = invocations.Where(function => !observerEvent || function.Method.DeclaringType is { } origin && IsRuleType(origin)).ToArray();
        foreach (var function in retained)
            if (function.Method.DeclaringType is not { } origin || !IsRuleType(origin) && origin.Assembly != typeof(object).Assembly)
                throw new RuleBoundaryException("clone_contract_missing", "Unowned callback code cannot be copied into a rule world");
        if (retained.Length == invocations.Length && context.TryGet(callback, out var mapped)) return (Delegate)mapped;
        Delegate? combined = null;
        foreach (var function in retained)
        {
            Delegate rebound;
            if (!ReferenceEquals(function, callback)) rebound = CopyDelegate(function, false)!;
            else
            {
                var target = function.Target == null ? null : Copy(function.Target, deferFields: true);
                rebound = target == null ? function : function.Method.CreateDelegate(function.GetType(), target);
            }
            combined = Delegate.Combine(combined, rebound);
        }
        if (combined != null && retained.Length == invocations.Length) context.Add(callback, combined);
        return combined;
    }
    internal static bool IsRuleType(Type type)
    {
        for (var current = type; current != null; current = current.DeclaringType)
        {
            if (current == typeof(SimulationWorld)) return true;
            if (typeof(AbstractModel).IsAssignableFrom(current)) return true;
            if (current.Assembly == typeof(AbstractModel).Assembly && current.Namespace?.StartsWith("AutoSlay.Rules.Native.", StringComparison.Ordinal) == true) return true;
        }
        return false;
    }
    private void ValidateType(Type type)
    {
        if (type.IsPointer || type.IsByRefLike || type == typeof(IntPtr) || type == typeof(UIntPtr))
            throw new RuleBoundaryException("clone_contract_missing", "Native handle state cannot be copied");
        if (type.Assembly == typeof(List<>).Assembly)
        {
            if (type.IsGenericType)
            {
                var definition = type.GetGenericTypeDefinition();
                if (definition == typeof(List<>) || definition == typeof(Stack<>) || definition == typeof(Queue<>)) return;
                if (definition == typeof(Dictionary<,>.KeyCollection) || definition == typeof(Dictionary<,>.ValueCollection))
                {
                    RequireKeyLayout(type.GetGenericArguments()[0]);
                    return;
                }
                if (definition == typeof(System.Collections.ObjectModel.ReadOnlyCollection<>)) return;
                if (definition == typeof(Dictionary<,>) || definition == typeof(HashSet<>))
                {
                    RequireKeyLayout(type.GetGenericArguments()[0]);
                    return;
                }
            }
            if (type == typeof(object) || type.IsValueType) return;
            throw new RuleBoundaryException("clone_contract_missing", "Unmigrated framework state: " + type.FullName);
        }
        if (IsRuleType(type)) return;
        throw new RuleBoundaryException("clone_contract_missing", "Unmigrated owned state layout: " + type.FullName);
    }
    internal static bool IsImmutable(object value)
    {
        if (value is string or Type or ModelId or RuleParameters or ModelRegistry or decimal or Guid or DateTime or TimeSpan) return true;
        var type = value.GetType();
        if (type.IsPrimitive) return type != typeof(IntPtr) && type != typeof(UIntPtr);
        if (type.IsEnum) return true;
        if (value is Array { Length: 0 }) return true;
        // BCL stateless/readonly comparers are code/constant data. Delegate-based or custom comparers
        // are not included; their captured mutable state needs a separate collection contract.
        if (type.Assembly == typeof(List<>).Assembly && type.GetInterfaces().Any(t => t.IsGenericType && t.GetGenericTypeDefinition() == typeof(IEqualityComparer<>)))
        {
            for (var current = type; current != null && current != typeof(object); current = current.BaseType)
                foreach (var field in current.GetFields(BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly))
                    if (!field.IsInitOnly || field.GetValue(value) is { } child && !IsImmutable(child)) return false;
            return true;
        }
        return false;
    }
    private static bool ImmutableKeyType(Type type) => type.IsPrimitive && type != typeof(IntPtr) && type != typeof(UIntPtr)
        || type.IsEnum || type == typeof(string) || type == typeof(ModelId) || type == typeof(Guid) || type == typeof(decimal) || type == typeof(Type);

    private static void RequireKeyLayout(Type keyType)
    {
        if (!ImmutableKeyType(keyType) && keyType.IsValueType)
            throw new RuleBoundaryException("clone_contract_missing", "Value-key collection equality layout is not migrated");
    }
    // Dictionary/HashSet bucket hashes cannot be copied for reference-identity keys: the fork owns
    // different objects. Rebuild only their hashes/links, retaining slots, free list, version and order.
    // No custom comparer, key GetHashCode/Equals, iterator, constructor or rule callback is invoked.
    private static void RehashIdentityCollection(object original, object copy)
    {
        var type = original.GetType();
        if (!type.IsGenericType) return;
        var definition = type.GetGenericTypeDefinition();
        var isDictionary = definition == typeof(Dictionary<,>);
        if (!isDictionary && definition != typeof(HashSet<>)) return;
        if (ImmutableKeyType(type.GetGenericArguments()[0])) return;
        var comparer = Field(type, "_comparer").GetValue(original);
        var referenceComparer = comparer is ReferenceEqualityComparer;
        if (!referenceComparer && comparer != null && (comparer.GetType().Assembly != typeof(object).Assembly
            || !comparer.GetType().IsGenericType
            || comparer.GetType().GetGenericTypeDefinition() != typeof(EqualityComparer<>).Assembly.GetType("System.Collections.Generic.ObjectEqualityComparer`1")))
            throw new RuleBoundaryException("clone_contract_missing", "Only default object identity or BCL reference equality keys can be rehashed");
        var originalEntries = (Array?)Field(type, "_entries").GetValue(original);
        if (originalEntries == null) return;
        var entries = (Array)Field(type, "_entries").GetValue(copy)!;
        var buckets = (int[])Field(type, "_buckets").GetValue(copy)!;
        var count = (int)Field(type, "_count").GetValue(original)!;
        var entryType = originalEntries.GetType().GetElementType()!;
        var nextField = Field(entryType, isDictionary ? "next" : "Next");
        var hashField = Field(entryType, isDictionary ? "hashCode" : "HashCode");
        var keyField = Field(entryType, isDictionary ? "key" : "Value");
        if (nextField.FieldType != typeof(int) || hashField.FieldType != (isDictionary ? typeof(uint) : typeof(int))
            || count < 0 || count > entries.Length || buckets.Length == 0)
            throw new RuleBoundaryException("clone_contract_missing", "Unsupported BCL hash collection layout");
        Array.Clear(buckets);
        for (var index = 0; index < count; index++)
        {
            var oldEntry = originalEntries.GetValue(index)!;
            if ((int)nextField.GetValue(oldEntry)! < -1) continue; // Preserve removed/free slots.
            var entry = entries.GetValue(index)!;
            var oldKey = keyField.GetValue(oldEntry); var key = keyField.GetValue(entry);
            uint hash;
            if (key == null) hash = 0;
            else if (!referenceComparer && oldKey != null && IsImmutable(oldKey))
                hash = isDictionary ? (uint)hashField.GetValue(oldEntry)! : unchecked((uint)(int)hashField.GetValue(oldEntry)!);
            else
            {
                var keyType = oldKey!.GetType();
                if (!referenceComparer && (keyType.IsValueType
                    || keyType.GetMethod(nameof(GetHashCode), Type.EmptyTypes)?.DeclaringType != typeof(object)
                    || keyType.GetMethod(nameof(Equals), [typeof(object)])?.DeclaringType != typeof(object)
                    || keyType.GetInterfaces().Any(i => i.IsGenericType && i.GetGenericTypeDefinition() == typeof(IEquatable<>))))
                    throw new RuleBoundaryException("clone_contract_missing", "Key equality code requires migration before cloning a collection");
                hash = unchecked((uint)RuntimeHelpers.GetHashCode(key));
            }
            var bucket = (int)(hash % (uint)buckets.Length);
            hashField.SetValue(entry, isDictionary ? (object)hash : unchecked((int)hash));
            nextField.SetValue(entry, buckets[bucket] - 1); buckets[bucket] = index + 1;
            entries.SetValue(entry, index);
        }
    }
    private static FieldInfo Field(Type type, string name) => type.GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic)
        ?? throw new RuleBoundaryException("clone_contract_missing", "Unsupported BCL hash collection layout: " + type.FullName + "." + name);
}
