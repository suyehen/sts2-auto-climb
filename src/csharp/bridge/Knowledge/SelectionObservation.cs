using System.Runtime.CompilerServices;

namespace AutoSlayBridge;

// Selection APIs return cards. They do not imply that the caller has already
// removed, upgraded or otherwise changed those cards.
public sealed record SelectionSource(string? ModelId, string ModelType, string InstanceId,
    string Callback, string? OptionKey = null, object? Model = null);
public sealed record SelectionCall(SelectionSource? Source, string? Operation, string[] ApiPath);

public sealed class SelectionObservation
{
    private readonly AsyncLocal<SelectionCall?> current = new();
    private readonly ConditionalWeakTable<object, SelectionCall> observed = new();
    public SelectionSource? CurrentSource => current.Value?.Source;
    private static SelectionCall Copy(SelectionCall call) => call with { ApiPath = call.ApiPath.ToArray() };

    private sealed class Scope(Action restore) : IDisposable
    {
        private bool disposed;
        public void Dispose() { if (!disposed) { disposed = true; restore(); } }
    }

    public IDisposable EnterSource(SelectionSource source)
    {
        var previous = current.Value;
        current.Value = new(source, null, []);
        return new Scope(() => current.Value = previous);
    }

    public IDisposable EnterCommand(string api, string? operation, SelectionSource? source = null)
    {
        var previous = current.Value;
        var origin = source?.InstanceId == previous?.Source?.InstanceId ? previous?.Source : source ?? previous?.Source;
        current.Value = new(origin, operation ?? previous?.Operation,
            (previous?.ApiPath ?? []).Append(api).TakeLast(24).ToArray());
        return new Scope(() => current.Value = previous);
    }

    public IDisposable EnterCallback(SelectionSource source)
    {
        var previous = current.Value;
        var same = previous?.Source?.InstanceId == source.InstanceId;
        var origin = same && previous!.Source!.Callback != "EventOption.Chosen"
            ? previous.Source : source with { OptionKey = same ? previous!.Source!.OptionKey : source.OptionKey };
        current.Value = new(origin, same ? previous?.Operation : null, same ? previous!.ApiPath.ToArray() : []);
        return new Scope(() => current.Value = previous);
    }

    // Key reusable UI surfaces by their fresh completion token, never by screen
    // name or prompt. An unobserved token cannot inherit a previous selection.
    public void Capture(object token)
    {
        if (current.Value is { } call) observed.AddOrUpdate(token, Copy(call));
        else observed.Remove(token);
    }

    public SelectionCall? Read(object token) => observed.TryGetValue(token, out var call) ? Copy(call) : null;
}
