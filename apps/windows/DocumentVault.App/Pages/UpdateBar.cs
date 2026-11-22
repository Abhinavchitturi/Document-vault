using System.Diagnostics;
using System.Reflection;
using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// "Document Vault 0.9.2 is available": across the top of the window when the releases page has a newer version
/// (checked at start and every few hours). Update downloads the new installer, checks it against the SHA-256 GitHub
/// publishes, runs it quietly (this PC's answer kept, the documents untouched) and the app opens again.
public sealed class UpdateBar : UserControl
{
    private const string Installer = "DocumentVault-Setup.exe";
    private readonly TextBlock _text = UI.Text("");
    private readonly ProgressBar _bar = new() { Maximum = 100, Visibility = Visibility.Collapsed, Width = 260 };
    private readonly StackPanel _buttons = UI.Row(8);
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromHours(6) };
    private AppUpdate? _update;

    /// This app's version (the release it was built for), or null in development (no checking then).
    public static string? Version =>
        AppState.Release is null ? null
            : typeof(UpdateBar).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion.Split('+')[0];

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
        Loaded += async (_, _) => { _timer.Start(); await CheckAsync(); };
        Unloaded += (_, _) => _timer.Stop();
    }

    private async Task CheckAsync()
    {
        if (Version is not { } current || _update is not null) return;
        _update = await Updates.CheckAsync(current, Installer);
        if (_update is null) return;
        _text.Text = $"Document Vault {_update.Version} is available.";
        _buttons.Children.Clear();
        _buttons.Children.Add(UI.Button("Update", async () => await UpdateAsync(), accent: true).Id("UpdateButton"));
        _buttons.Children.Add(UI.Button("Later", () => Visibility = Visibility.Collapsed));
        Visibility = Visibility.Visible;
    }

    private async Task UpdateAsync()
    {
        var update = _update!;
        _buttons.Children.Clear();
        _bar.Visibility = Visibility.Visible;
        var file = Path.Combine(Path.GetTempPath(), $"DocumentVault-Setup-{update.Version}.exe");
        try
        {
            var progress = new Progress<(long done, long total)>(p =>
            {
                _bar.Value = p.total > 0 ? (double)p.done / p.total * 100 : 0;
                _text.Text = $"Downloading Document Vault {update.Version}: {p.done / 1_000_000:N0} of {p.total / 1_000_000:N0} MB";
            });
            await Updates.DownloadAsync(update, file, progress);
            _text.Text = $"Installing Document Vault {update.Version}…";
            await AppState.Current.StopAsync();  // the engine, before its program files are replaced
            var mode = AppState.Current.Mode is { } m ? $" /MODE={m}" : "";
            Process.Start(new ProcessStartInfo(file, $"/SILENT /SUPPRESSMSGBOXES /NORESTART /UPDATE=1{mode}") { UseShellExecute = true });
            Application.Current.Exit();  // the installer opens the app again when it's done
        }
        catch (Exception e)
        {
            _bar.Visibility = Visibility.Collapsed;
            _text.Text = $"The update didn't work: {e.Message}";
            _buttons.Children.Add(UI.Button("Try Again", async () => await UpdateAsync(), accent: true));
        }
    }
}
