using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// Across the top of the window while the search model downloads (the first start only) and loads: a progress bar,
/// so it's clear why searching, asking and adding documents wait. It goes away by itself when search is ready.
public sealed class SearchReadyBar : UserControl
{
    private readonly TextBlock _text = UI.Text("");
    private readonly ProgressBar _bar = new() { Maximum = 100, IsIndeterminate = true };
    private readonly TextBlock _note = UI.Caption("You can look around meanwhile; searching, asking and adding documents start when it's ready.");

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
            Child = UI.Stack(6, _text, _bar, _note),
        }.Id("SearchReadyBar");
        Loaded += async (_, _) => await FollowAsync();
    }

    private async Task FollowAsync()
    {
        while (IsLoaded && AppState.Current.Client is { } client)
        {
            SearchStatus status;
            try { status = await client.SearchStatusAsync(); }
            catch { Visibility = Visibility.Collapsed; return; }  // a vault without it (older), or signed out
            if (status.State == "ready") { Visibility = Visibility.Collapsed; return; }
            Visibility = Visibility.Visible;
            if (status.State == "failed")
            {
                _text.Text = "Search couldn't get ready";
                _note.Text = status.Error ?? "";
                _bar.Visibility = Visibility.Collapsed;
                return;
            }
            if (status.State == "downloading" && status.Total > 0)
            {
                _text.Text = $"Getting search ready: downloading the search model, {status.Done / 1_000_000:N0} of {status.Total / 1_000_000:N0} MB (once)";
                _bar.IsIndeterminate = false;
                _bar.Value = (double)status.Done / status.Total * 100;
            }
            else
            {
                _text.Text = "Getting search ready…";
                _bar.IsIndeterminate = true;
            }
            await Task.Delay(700);
        }
    }
}
