using System.Security.Cryptography;
using System.Text.Json;
using AutoSlay.Rules.Runtime;
using AutoSlay.RulesHost;
using AutoSlay.Rules.Parameters;

Console.InputEncoding = System.Text.Encoding.UTF8;
Console.OutputEncoding = new System.Text.UTF8Encoding(false);
RuleServer server;
try
{
    if (args.Length == 0) server = new RuleServer();
    else if (args.Length == 2 && args[0] == "--parameters") server = new RuleServer(CompiledRules.LoadParameters(File.ReadAllBytes(args[1])));
    else throw new ArgumentException("Usage: AutoSlay.RulesHost [--parameters PACKAGE.json]");
}
catch (Exception error)
{
    Console.Error.WriteLine((error is RuleBoundaryException boundary ? boundary.Code : "startup_error") + ": " + error.Message);
    return 1;
}
while (Console.ReadLine() is { } line)
{
    object response;
    try
    {
        if (line.Length > 4_000_000) throw new RuleBoundaryException("invalid_request", "Request exceeds protocol limit");
        using var document = JsonDocument.Parse(line);
        response = server.Process(document.RootElement);
    }
    catch (JsonException) { response = new { protocol_version = RuleServer.Protocol, ok = false, error = new { code = "invalid_json" } }; }
    catch (RuleBoundaryException error) { response = new { protocol_version = RuleServer.Protocol, ok = false, error = new { code = error.Code, message = error.Message } }; }
    catch (Exception error)
    {
        Console.Error.WriteLine(error.GetType().Name + ": " + error.Message);
        response = new { protocol_version = RuleServer.Protocol, ok = false, error = new { code = "internal_error" } };
    }
    Console.WriteLine(JsonSerializer.Serialize(response));
}
return 0;
