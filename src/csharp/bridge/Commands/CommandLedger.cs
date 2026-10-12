using System.Collections.Concurrent;

namespace AutoSlayBridge;

public sealed record ActionRequest(string RequestId, string RunId, string SessionEpoch,
    long ExpectedStateVersion, string ActionId, string? TargetId = null);

public sealed record ActionReceipt(string RequestId, string Status, bool? Dispatched,
    string? Error = null, string? Message = null, string? SnapshotJson = null);

// Thread-safe receipts survive retries within one bridge process. Never evict IDs
// silently: at capacity reject new requests instead of risking replay.
public sealed class CommandLedger
{
    private readonly object gate = new();
    private readonly Dictionary<string, (ActionRequest Request, ActionReceipt Receipt)> records = new();
    private readonly ConcurrentQueue<ActionRequest> queue = new();
    private readonly Dictionary<string, DateTimeOffset> acceptedAt = new();
    private string? active;
    private bool paused = true;
    public bool Paused { get { lock (gate) return paused; } }
    public bool Busy { get { lock (gate) return active != null; } }
    public void SetPaused(bool value) { lock (gate) paused = value; }

    public ActionReceipt Submit(ActionRequest request)
    {
        lock (gate)
        {
            if (records.TryGetValue(request.RequestId, out var previous))
                return previous.Request == request ? previous.Receipt : new(request.RequestId, "rejected", null, "REQUEST_CONFLICT", "This ID already belongs to a different request; query its existing result.");
            string? error = records.Count >= 4096 ? "LEDGER_FULL" : paused ? "CONTROL_PAUSED" : active != null ? "BUSY" : null;
            var receipt = new ActionReceipt(request.RequestId, error == null ? "received" : "rejected", false, error);
            if (error == "LEDGER_FULL") return receipt;
            records.Add(request.RequestId, (request, receipt));
            if (error == null) { acceptedAt[request.RequestId] = DateTimeOffset.UtcNow; active = request.RequestId; queue.Enqueue(request); }
            return receipt;
        }
    }

    public bool TryDequeue(out ActionRequest? request) => queue.TryDequeue(out request);
    public bool IsExpired(string id) { lock (gate) return DateTimeOffset.UtcNow - acceptedAt[id] > TimeSpan.FromSeconds(10); }
    public bool TryBeginDispatch(string id)
    {
        lock (gate)
        {
            if (paused || active != id) return false;
            var record = records[id];
            records[id] = (record.Request, new(id, "executing", true));
            return true;
        }
    }
    public ActionReceipt Get(string id)
    {
        lock (gate) return records.TryGetValue(id, out var value) ? value.Receipt : new(id, "unknown", null, "RESULT_UNKNOWN");
    }
    public void Update(ActionReceipt receipt, bool release = false)
    {
        lock (gate)
        {
            if (!records.TryGetValue(receipt.RequestId, out var record)) throw new InvalidOperationException("Unknown request");
            records[receipt.RequestId] = (record.Request, receipt);
            if (release && active == receipt.RequestId) active = null;
        }
    }
}
