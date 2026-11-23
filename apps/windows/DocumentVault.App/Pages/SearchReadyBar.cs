using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// Across the top of the window while the vault gets ready on its first start: the search model downloading and
/// loading, then the private AI downloading by itself (about 5 GB, once), each with a progress bar, so it's clear why
/// searching or answers wait. It goes away by itself when both are ready.
public sealed class SearchReadyBar : UserControl
{
    private readonly TextBlock _text = UI.Text("");
    private readonly ProgressBar _bar = new() { Maximum = 100, IsIndeterminate = true };
    private readonly TextBlock _note = UI.Caption("You can add documents meanwhile; searching and asking start when it's ready.");
    private readonly StackPanel _actions = UI.Row(8);

    public SearchReadyBar()
    {
        Visibility = Visibility.Collapsed;
        _text.FontWeight = UI.Semibold;
        Content = new Border
        {
            Padding = new Thickness(20, 12, 20, 12),
            Background = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
            BorderBrush = (Microsoft.UI.Xaml.Media.Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
            BorderThickness = new Thickness(0, 0, 0, 1),
            Child = UI.Stack(6, _text, _bar, _note, _actions),
        }.Id("SearchReadyBar");
        Loaded += async (_, _) => await FollowAsync();
    }

    private async Task FollowAsync()
    {
        if (!await FollowSearchAsync()) return;
        await FollowAIAsync();
    }

    /// The search model: true once it is ready.
    private async Task<bool> FollowSearchAsync()
    {
        while (IsLoaded && AppState.Current.Client is { } client)
        {
            SearchStatus status;
            try { status = await client.SearchStatusAsync(); }
            catch { Visibility = Visibility.Collapsed; return false; }  // a vault without it (older), or signed out
            if (status.State == "ready") { Visibility = Visibility.Collapsed; return true; }
            Visibility = Visibility.Visible;
            if (status.State == "failed")
            {
                Show("Search couldn't get ready", status.Error ?? "", null);
                return false;
            }
            if (status.State == "downloading" && status.Total > 0)
                Show($"Getting search ready: downloading the search model, {status.Done / 1_000_000:N0} of {status.Total / 1_000_000:N0} MB (once)",
                     "You can add documents meanwhile; searching and asking start when it's ready.",
                     (double)status.Done / status.Total * 100);
            else
                Show("Getting search ready…", "You can add documents meanwhile; searching and asking start when it's ready.", -1);
            await Task.Delay(700);
        }
        return false;
    }

    /// Then the local AI, which the vault downloads by itself (about 5 GB, once): answers start when it's done.
    private async Task FollowAIAsync()
    {
        var waited = 0;
        while (IsLoaded && AppState.Current.Client is { } client)
        {
            AIDownloadStatus status;
            try { status = await client.AIDownloadStatusAsync(); }
            catch { Visibility = Visibility.Collapsed; return; }  // nothing to download here
            if (status.State == "done") { Visibility = Visibility.Collapsed; await App.Window!.Reload(); return; }
            if (status.State == "failed")
            {
                Visibility = Visibility.Visible;
                Show("The private AI didn't finish downloading", status.Error ?? "", null);
                if (AppState.Current.User?.IsAdmin == true)
                    _actions.Children.Add(UI.Button("Try Again", async () =>
                    {
                        try { await client.StartAIDownloadAsync(); } catch { /* shown on the next check */ }
                        await FollowAIAsync();
                    }, accent: true));
                return;
            }
            if (status.State != "downloading")
            {
                if (AppState.Current.Info?.LlmStatus.Ready == true || status.State == "idle" && AppState.Current.Info?.LlmStatus.CanDownload != true)
                { Visibility = Visibility.Collapsed; return; }
                if (++waited > 20) { Visibility = Visibility.Collapsed; return; }  // not starting by itself here
                await Task.Delay(1500);  // about to start (it waits for the search model)
                continue;
            }
            Visibility = Visibility.Visible;
            var step = status.Step == "model" ? "the private AI" : "the AI's program";
            Show(status.Total > 0
                    ? $"Downloading {step}: {status.Done / 1e9:0.0} of {status.Total / 1e9:0.0} GB (once)"
                    : $"Downloading {step}…",
                 "Everything else works meanwhile; the assistant answers as soon as it's done.",
                 status.Total > 0 ? (double)status.Done / status.Total * 100 : -1);
            await Task.Delay(1000);
        }
    }

    private void Show(string title, string note, double? share)
    {
        _actions.Children.Clear();
        _text.Text = title;
        _note.Text = note;
        _bar.Visibility = share is null ? Visibility.Collapsed : Visibility.Visible;
        _bar.IsIndeterminate = share is < 0;
        if (share is >= 0) _bar.Value = share.Value;
    }
}
