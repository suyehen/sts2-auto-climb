using System.Diagnostics;
using System.IO;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace AutoSlay.Observer;

public sealed class ObserverWindow : Window
{
    private const string BaseUrl = "http://127.0.0.1:18767";
    private readonly WebView2 web = new();
    private readonly string root;
    private readonly string settingsPath;
    private readonly TextBlock notice = new() { Text = "正在连接观察台…", Margin = new Thickness(8), TextWrapping = TextWrapping.Wrap };

    private sealed record Settings(double Left, double Top, double Width, double Height, bool Pinned, double FontScale = 1);
    private double fontScale = 1;

    public ObserverWindow(string workspace)
    {
        root = workspace;
        settingsPath = Path.Combine(root, ".runtime", "local", "observer-window.json");
        Title = "AutoSlay · 实时观察";
        Width = 420; Height = 820; MinWidth = 300; MinHeight = 280; ShowActivated = false;
        Background = new System.Windows.Media.SolidColorBrush(System.Windows.Media.Color.FromRgb(16, 23, 25));
        Foreground = System.Windows.Media.Brushes.Gainsboro;
        var area = SystemParameters.WorkArea;
        Left = area.Right - Width; Top = area.Top;
        try
        {
            if (File.Exists(settingsPath))
            {
                var settings = JsonSerializer.Deserialize<Settings>(File.ReadAllText(settingsPath));
                if (settings is not null)
                {
                    Width = Math.Clamp(settings.Width, 300, area.Width); Height = Math.Clamp(settings.Height, 280, area.Height);
                    Left = Math.Clamp(settings.Left, area.Left, Math.Max(area.Left, area.Right - Width));
                    Top = Math.Clamp(settings.Top, area.Top, Math.Max(area.Top, area.Bottom - Height));
                    Topmost = settings.Pinned; fontScale = Math.Clamp(settings.FontScale, .8, 1.6);
                }
            }
        }
        catch (Exception) { notice.Text = "窗口设置无法读取，已恢复默认布局。"; }
        var dock = new DockPanel();
        DockPanel.SetDock(notice, Dock.Bottom); dock.Children.Add(notice);dock.Children.Add(web); Content = dock;
        Loaded += async (_, _) => await Initialize();
        Closing += (_, _) => SaveSettings();
    }

    private async Task Initialize()
    {
        try
        {
            if (web.CoreWebView2 is null)
            {
                var env = await CoreWebView2Environment.CreateAsync(null, Path.Combine(root, ".runtime", "cache", "observer-webview"));
                await web.EnsureCoreWebView2Async(env);
                if (web.CoreWebView2 is null) throw new InvalidOperationException("WebView2 did not initialize.");
                web.CoreWebView2.Settings.AreDevToolsEnabled = false;
                web.CoreWebView2.Settings.IsStatusBarEnabled = false;
                web.CoreWebView2.NavigationCompleted += (_, e) =>
                {
                    if (web.CoreWebView2.Source == "about:blank") return;
                    var statusPath = Path.Combine(root, ".runtime", "logs", "observer-ready.json");
                    Directory.CreateDirectory(Path.GetDirectoryName(statusPath)!);
                    File.WriteAllText(statusPath, JsonSerializer.Serialize(new { ready = e.IsSuccess, url = web.Source?.ToString(), at = DateTimeOffset.UtcNow, error = e.WebErrorStatus.ToString() }));
                };
                web.CoreWebView2.NavigationStarting += (_, e) =>
                {
                    if (e.Uri != "about:blank" && (!Uri.TryCreate(e.Uri, UriKind.Absolute, out var uri) || uri.Scheme != "http" || uri.Host != "127.0.0.1" || uri.Port != 18767))
                        e.Cancel = true;
                };
                web.CoreWebView2.NewWindowRequested += (_, e) =>
                {
                    e.Handled = true;
                    if (e.Uri.StartsWith(BaseUrl + "/", StringComparison.Ordinal)) OpenBrowser(e.Uri);
                };
                web.CoreWebView2.WebMessageReceived += (_, e) =>
                {
                    try
                    {
                        using var doc = JsonDocument.Parse(e.WebMessageAsJson);
                        var message = doc.RootElement;
                        if (message.GetProperty("kind").GetString() == "observer_status")
                        {
                            var path = Path.Combine(root, ".runtime", "logs", "observer-view.json");
                            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
                            File.WriteAllText(path, JsonSerializer.Serialize(new { rendered_at = DateTimeOffset.UtcNow, status = message.Clone() }));
                        }
                        if (message.GetProperty("kind").GetString() == "open_full")
                        {
                            var id = message.TryGetProperty("archive_id", out var value) ? value.GetString() : null;
                            var query = new List<string>();
                            if (id is not null) query.Add("archive=" + Uri.EscapeDataString(id));
                            foreach (var key in new[] { "act", "node", "visit", "decision" })
                                if (message.TryGetProperty(key, out var target) && target.ValueKind != JsonValueKind.Null)
                                    query.Add(key + "=" + Uri.EscapeDataString(target.ToString()));
                            OpenBrowser(BaseUrl + "/" + (query.Count == 0 ? "" : "?" + string.Join("&", query)));
                        }
                    }
                    catch (JsonException) { notice.Text = "无法解析页面请求。"; }
                };
            }
            web.ZoomFactor = fontScale;
            web.Source = new Uri(BaseUrl + "/sidebar.html");
            notice.Text = "只读观察 · 关闭窗口不停止游戏或记录";
        }
        catch (Exception ex)
        {
            notice.Text = "观察窗加载失败，请重新打开观察窗口。" + ex.Message;
            var log = Path.Combine(root, ".runtime", "logs", "observer-error.log");
            Directory.CreateDirectory(Path.GetDirectoryName(log)!);
            File.AppendAllText(log, DateTimeOffset.UtcNow + " " + ex + Environment.NewLine);
        }
    }

    private static void OpenBrowser(string url) => Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });

    private void SaveSettings()
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(settingsPath)!);
            var value = new Settings(Left, Top, Width, Height, Topmost, fontScale);
            File.WriteAllText(settingsPath, JsonSerializer.Serialize(value));
        }
        catch (IOException) { /* Window closure never changes game control. */ }
    }
}
