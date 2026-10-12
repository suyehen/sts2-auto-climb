using System.Windows;
using System.IO;

namespace AutoSlay.Observer;

public class App : Application
{
    [STAThread]
    public static void Main(string[] args)
    {
        var root = args.FirstOrDefault(a => a.StartsWith("--root="))?[7..] ?? Environment.CurrentDirectory;
        using var mutex = new System.Threading.Mutex(true, "AutoSlayObserver-" + Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(System.Text.Encoding.UTF8.GetBytes(Path.GetFullPath(root))))[..20], out var created);
        if (!created) return;
        var app = new App();
        app.Run(new ObserverWindow(Path.GetFullPath(root)));
    }
}
