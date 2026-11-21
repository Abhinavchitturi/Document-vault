using Microsoft.UI.Xaml;

namespace DocumentVault;

public partial class App : Application
{
    public static MainWindow? Window { get; private set; }

    /// Where a crash is written down: %LOCALAPPDATA%\Document Vault\crash.log.
    public static readonly string CrashLog = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Document Vault", "crash.log");

    public App()
    {
        InitializeComponent();
        UnhandledException += (_, e) => WriteCrash("UI", e.Exception);
        AppDomain.CurrentDomain.UnhandledException += (_, e) => WriteCrash("app", e.ExceptionObject as Exception);
        TaskScheduler.UnobservedTaskException += (_, e) => { WriteCrash("background task", e.Exception); e.SetObserved(); };
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        Window = new MainWindow();
        Window.Activate();
    }

    public static void WriteCrash(string where, Exception? error)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(CrashLog)!);
            File.AppendAllText(CrashLog, $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} ({where}) {error}\n\n");
        }
        catch { /* nowhere left to write it */ }
    }
}
