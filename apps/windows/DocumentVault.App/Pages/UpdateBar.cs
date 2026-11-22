using System.Diagnostics;
using System.Reflection;
using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// "Document Vault 0.9.2 is available": across the top of the window when the releases page has a newer version
/// (checked at start and every few hours). The person chooses: Restart & Update (download, install, reopen), Update
/// When I Quit (download now, install quietly when the app is closed) or Later. The installer is checked against the
/// SHA-256 GitHub publishes, replaces this app in place (same folder and shortcuts, this PC's answer kept, documents
/// untouched) and is deleted afterwards.
public sealed class UpdateBar : UserControl
{
    private const string Installer = "DocumentVault-Setup.exe";
    private static string? _waiting;  // a checked installer to run when the app is closed
    private readonly TextBlock _text = UI.Text("");
    private readonly ProgressBar _bar = new() { Maximum = 100, Visibility = Visibility.Collapsed, Width = 260 };
    private readonly StackPanel _buttons = UI.Row(8);
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromHours(6) };
    private AppUpdate? _update;

    /// This app's version (the release it was built for), or null in development (no checking then).
    public static string? Version =>
        AppState.Release is null ? null
            : typeof(UpdateBar).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.Split('+')[0];

    private static string Download(string version) => Path.Combine(Path.GetTempPath(), $"DocumentVault-Setup-{version}.exe");

    public UpdateBar()
    {
        Visibility = Visibility.Collapsed;
        _text.FontWeight = UI.Semibold;
        var row = UI.Row(14, _text, _bar, _buttons);
        row.VerticalAlignment = VerticalAlignment.Center;
        Content = new Border
        {
            Padding = new Thickness(20, 10, 20, 10),
            Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
            BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(0, 0, 0, 1),
            Child = row,
        }.Id("UpdateBar");
        _timer.Tick += async (_, _) => await CheckAsync();
        Loaded += async (_, _) => { CleanUp(); _timer.Start(); await CheckAsync(); };
        Unloaded += (_, _) => _timer.Stop();
    }

    /// Installers of earlier updates, already installed: deleted.
    private static void CleanUp()
    {
        foreach (var old in Directory.EnumerateFiles(Path.GetTempPath(), "DocumentVault-Setup-*.exe"))
            if (old != _waiting) try { File.Delete(old); } catch { /* still running: next time */ }
    }

    /// The app is closing: if an update is waiting for that, start its installer (quiet, no reopening). The next
    /// start of the app deletes the installer file.
    public static void InstallIfWaiting()
    {
        if (_waiting is not { } file || !File.Exists(file)) return;
        Start(file, reopen: false);
        _waiting = null;
    }

    private static void Start(string file, bool reopen)
    {
        var mode = AppState.Current.Mode is { } m ? $" /MODE={m}" : "";
        Process.Start(new ProcessStartInfo(file, $"/SILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE={(reopen ? 1 : 0)}{mode}")
            { UseShellExecute = true });
    }

    private async Task CheckAsync()
    {
        if (Version is not { } current || _update is not null) return;
        _update = await Updates.CheckAsync(current, Installer);
        if (_update is null) return;
        _text.Text = $"Document Vault {_update.Version} is available.";
        _buttons.Children.Clear();
        _buttons.Children.Add(UI.Button("Later", () => Visibility = Visibility.Collapsed));
        _buttons.Children.Add(UI.Button("Update When I Quit", async () => await GetAsync(now: false)).Id("UpdateOnQuitButton"));
        _buttons.Children.Add(UI.Button("Restart & Update", async () => await GetAsync(now: true), accent: true).Id("UpdateButton"));
        Visibility = Visibility.Visible;
    }

    private async Task GetAsync(bool now)
    {
        var update = _update!;
        _buttons.Children.Clear();
        _bar.Visibility = Visibility.Visible;
        var file = Download(update.Version);
        try
        {
            if (_waiting != file)
            {
                var progress = new Progress<(long done, long total)>(p =>
                {
                    _bar.Value = p.total > 0 ? (double)p.done / p.total * 100 : 0;
                    _text.Text = $"Downloading Document Vault {update.Version}: {p.done / 1_000_000:N0} of {p.total / 1_000_000:N0} MB";
                });
                await Updates.DownloadAsync(update, file, progress);
            }
            _bar.Visibility = Visibility.Collapsed;
            if (!now)
            {
                _waiting = file;
                _text.Text = $"Document Vault {update.Version} will be installed when you close the app.";
                _buttons.Children.Add(UI.Button("Restart & Update Now", async () => await GetAsync(now: true), accent: true));
                return;
            }
            _waiting = null;
            _text.Text = $"Installing Document Vault {update.Version}…";
            await AppState.Current.StopAsync();  // the engine, before the app's files are replaced
            Start(file, reopen: true);
            Application.Current.Exit();  // the installer opens the app again when it's done
        }
        catch (Exception e)
        {
            _bar.Visibility = Visibility.Collapsed;
            _text.Text = $"The update didn't work: {e.Message}";
            _buttons.Children.Add(UI.Button("Try Again", async () => await GetAsync(now), accent: true));
        }
    }
}
