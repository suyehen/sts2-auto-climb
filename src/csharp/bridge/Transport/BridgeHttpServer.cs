using System.Net;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace AutoSlayBridge;

public static class BridgeHttpServer
{
    public static readonly JsonSerializerOptions Options = new() {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow
    };
    private sealed record ControlRequest(string Mode);

    public static async Task Run(string token, Func<string> snapshot, CommandLedger ledger, Func<string>? encyclopedia = null, Func<string>? refreshEncyclopedia = null)
    {
        try
        {
            using var listener = new HttpListener();
            listener.Prefixes.Add("http://127.0.0.1:18765/");
            listener.Start();
            while (true)
            {
                var context = await listener.GetContextAsync();
                _ = Handle(context, token, snapshot, ledger, encyclopedia, refreshEncyclopedia);
            }
        }
        catch (Exception e) { Console.Error.WriteLine("AutoSlayBridge HTTP stopped: " + e.Message); }
    }

    private static object Receipt(ActionReceipt receipt) => new {
        schema_version = "1.0", request_id = receipt.RequestId, status = receipt.Status,
        dispatched = receipt.Dispatched, error = receipt.Error, message = receipt.Message,
        snapshot = receipt.SnapshotJson == null ? (JsonElement?)null : JsonSerializer.Deserialize<JsonElement>(receipt.SnapshotJson)
    };

    private static async Task Handle(HttpListenerContext context, string token, Func<string> snapshot, CommandLedger ledger, Func<string>? encyclopedia, Func<string>? refreshEncyclopedia)
    {
        try
        {
            var request = context.Request;
            if (request.Headers["Authorization"] != "Bearer " + token) { context.Response.StatusCode = 401; return; }
            object result;
            var path = request.Url?.AbsolutePath;
            if (request.HttpMethod == "GET" && path == "/state") result = JsonSerializer.Deserialize<JsonElement>(snapshot());
            else if (request.HttpMethod == "GET" && path == "/encyclopedia" && encyclopedia != null) result = JsonSerializer.Deserialize<JsonElement>(encyclopedia());
            else if (request.HttpMethod == "POST" && path == "/encyclopedia-refresh" && refreshEncyclopedia != null) result = JsonSerializer.Deserialize<JsonElement>(refreshEncyclopedia());
            else if (request.HttpMethod == "GET" && path == "/action-status") result = Receipt(ledger.Get(request.QueryString["request_id"] ?? ""));
            else if (request.HttpMethod == "POST" && path is "/act" or "/control")
            {
                if (request.ContentLength64 < 1 || request.ContentLength64 > 8192) { context.Response.StatusCode = 400; return; }
                using var reader = new StreamReader(request.InputStream, Encoding.UTF8);
                var body = await reader.ReadToEndAsync().WaitAsync(TimeSpan.FromSeconds(5));
                if (Encoding.UTF8.GetByteCount(body) > 8192) { context.Response.StatusCode = 400; return; }
                if (path == "/control")
                {
                    var control = JsonSerializer.Deserialize<ControlRequest>(body, Options);
                    if (control?.Mode is not ("auto" or "paused")) throw new JsonException("Unknown control mode");
                    ledger.SetPaused(control.Mode == "paused");
                    result = new { schema_version = "1.0", control_mode = control.Mode, busy = ledger.Busy };
                }
                else
                {
                    var action = JsonSerializer.Deserialize<ActionRequest>(body, Options);
                    if (action == null || string.IsNullOrWhiteSpace(action.RequestId) || action.RequestId.Length > 128
                        || string.IsNullOrWhiteSpace(action.RunId) || string.IsNullOrWhiteSpace(action.SessionEpoch)
                        || string.IsNullOrWhiteSpace(action.ActionId) || action.ExpectedStateVersion < 0) throw new JsonException("Invalid action request");
                    result = Receipt(ledger.Submit(action));
                }
            }
            else { context.Response.StatusCode = 404; return; }
            var bytes = JsonSerializer.SerializeToUtf8Bytes(result, Options);
            context.Response.ContentType = "application/json; charset=utf-8";
            context.Response.ContentLength64 = bytes.Length;
            await context.Response.OutputStream.WriteAsync(bytes);
        }
        catch (JsonException) { context.Response.StatusCode = 400; }
        catch (Exception e) { context.Response.StatusCode = 500; Console.Error.WriteLine("AutoSlayBridge request failed: " + e.Message); }
        finally { context.Response.Close(); }
    }
}
