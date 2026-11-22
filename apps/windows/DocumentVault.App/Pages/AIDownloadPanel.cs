using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// The one-click "Download the AI" button, with its progress, wherever answers aren't available yet.
public sealed class AIDownloadPanel : UserControl
{
    private readonly StackPanel _panel = UI.Stack(8);
    private bool _following;

    public AIDownloadPanel()
    {
        Content = _panel;
        Loaded += async (_, _) => await FollowAsync();
        Show(null);
    }

    private void Show(AIDownloadStatus? status)
    {
        var state = AppState.Current;
        _panel.Children.Clear();
        var downloading = status?.State == "downloading";
        if (state.Info?.LlmStatus.CanDownload != true && !downloading) { Visibility = Visibility.Collapsed; return; }
        Visibility = Visibility.Visible;
        if (downloading)
        {
            var share = status!.Total > 0 ? (double)status.Done / status.Total * 100 : 0;
            _panel.Children.Add(UI.Text($"Downloading {(status.Step == "model" ? "the answer model" : "llama.cpp")}… {share:0}%"));
            _panel.Children.Add(new ProgressBar { Value = share, Maximum = 100 });
            _panel.Children.Add(UI.Caption("You can keep using the vault; answers start by themselves when it's done."));
        }
        else if (state.User?.IsAdmin == true)
        {
            var button = UI.Button("Download the AI (about 5 GB)", async () => await StartAsync(), accent: true).Id("DownloadAIButton");
            _panel.Children.Add(UI.Row(10, button, UI.Caption("Once. After that it works offline, on this computer.")));
        }
        else
        {
            _panel.Children.Add(UI.Caption("Ask the vault's admin to download the AI in Settings."));
        }
        if (status?.State == "failed" && status.Error is { } error)
            _panel.Children.Add(new InfoBar { IsOpen = true, IsClosable = false, Severity = InfoBarSeverity.Error, Message = error });
    }

    private async Task StartAsync()
    {
        try
        {
            Show(await AppState.Current.Client!.StartAIDownloadAsync());
            await FollowAsync();
        }
        catch (Exception e)
        {
            _panel.Children.Add(new InfoBar { IsOpen = true, IsClosable = false, Severity = InfoBarSeverity.Error, Message = e.Message });
        }
    }

    /// Check every second while it downloads; reload the vault's state when it's done.
    private async Task FollowAsync()
    {
        if (_following || AppState.Current.Client is null) return;
        _following = true;
        try
        {
            while (IsLoaded)
            {
                AIDownloadStatus status;
                try { status = await AppState.Current.Client.AIDownloadStatusAsync(); }
                catch { return; }  // nothing to download here
                Show(status);
                if (status.State == "done") { await App.Window!.Reload(); Show(status); return; }
                if (status.State != "downloading") return;
                await Task.Delay(1000);
            }
        }
        finally { _following = false; }
    }
}
