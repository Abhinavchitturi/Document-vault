using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// This computer keeps the vault: download the vault's engine (once), with a progress bar. A computer that only
/// connects to another one never sees this.
public sealed class EngineSetupPage : UserControl
{
    private readonly TextBlock _status = UI.Text("Starting the download…", secondary: true);
    private readonly ProgressBar _bar = new() { Maximum = 100, IsIndeterminate = true, Width = 420 };
    private readonly StackPanel _actions = UI.Row(8);
    private readonly InfoBar _problem = new() { IsClosable = false, Severity = InfoBarSeverity.Error };

    public EngineSetupPage()
    {
        var size = AppState.Release is { Size: > 0 } r ? $" ({r.Size / 1_000_000:N0} MB, once)" : "";
        var title = UI.Title("Setting up this computer as the vault");
        var detail = UI.Text($"Downloading the vault's engine{size}: what reads, searches and keeps your documents on this " +
                             "computer. Your documents never leave it.", secondary: true);
        _status.Id("EngineSetupStatus");
        _actions.HorizontalAlignment = HorizontalAlignment.Center;
        var panel = UI.Stack(14, title, detail, _bar, _status, _problem, _actions);
        foreach (var child in new FrameworkElement[] { title, _bar, _status }) child.HorizontalAlignment = HorizontalAlignment.Center;
        detail.TextAlignment = TextAlignment.Center;
        panel.MaxWidth = 520;
        panel.HorizontalAlignment = HorizontalAlignment.Center;
        panel.VerticalAlignment = VerticalAlignment.Center;
        Content = UI.Page(panel);
        Loaded += async (_, _) => await RunAsync();
    }

    private async Task RunAsync()
    {
        _problem.IsOpen = false;
        _actions.Children.Clear();
        _bar.IsIndeterminate = true;
        var progress = new Progress<EngineProgress>(p =>  // reported on the UI thread
        {
            var share = p.Total > 0 ? (double)p.Done / p.Total * 100 : 0;
            _bar.IsIndeterminate = false;
            _bar.Value = share;
            _status.Text = p.Step == "downloading"
                ? $"Downloading: {p.Done / 1_000_000:N0} of {p.Total / 1_000_000:N0} MB"
                : $"Unpacking… {share:0}%";
        });
        try
        {
            await AppState.Current.InstallEngineAsync(progress);
            _status.Text = "Done. Opening your vault…";
            await App.Window!.StartAsync();
        }
        catch (Exception e)
        {
            _bar.IsIndeterminate = false;
            _problem.Message = e.Message;
            _problem.IsOpen = true;
            _status.Text = "";
            _actions.Children.Add(UI.Button("Try Again", async () => await RunAsync(), accent: true));
            _actions.Children.Add(UI.Button("Use the vault on another computer instead", () =>
            {
                AppState.Current.Mode = "remote";
                App.Window!.Show(new ConnectPage());
            }));
        }
    }
}
