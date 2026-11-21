using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// A full-window message: starting up, or why the vault can't open.
public sealed class StatusPage : UserControl
{
    public StatusPage(string title, string? detail, bool busy, bool retry = false)
    {
        var panel = UI.Stack(14);
        panel.HorizontalAlignment = HorizontalAlignment.Center;
        panel.VerticalAlignment = VerticalAlignment.Center;
        panel.MaxWidth = 520;
        panel.Children.Add(new Image
        {
            Source = new Microsoft.UI.Xaml.Media.Imaging.BitmapImage(new Uri(Path.Combine(AppContext.BaseDirectory, "DocumentVault.ico"))),
            Width = 72, Height = 72,
        });
        var heading = UI.Title(title);
        heading.HorizontalAlignment = HorizontalAlignment.Center;
        panel.Children.Add(heading);
        if (detail is not null)
        {
            var text = UI.Text(detail, secondary: true);
            text.TextAlignment = TextAlignment.Center;
            panel.Children.Add(text);
        }
        if (busy) panel.Children.Add(new ProgressRing { IsActive = true, Width = 28, Height = 28 });
        if (retry)
        {
            var buttons = UI.Row(8,
                UI.Button("Show Log", () => System.Diagnostics.Process.Start("explorer.exe", $"/select,\"{AppState.Current.LogFile}\"")),
                UI.Button("Try Again", () => _ = App.Window!.StartAsync(), accent: true));
            buttons.HorizontalAlignment = HorizontalAlignment.Center;
            panel.Children.Add(buttons);
        }
        Content = panel;
    }
}
