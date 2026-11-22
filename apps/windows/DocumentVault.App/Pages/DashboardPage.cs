using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace DocumentVault.Pages;

/// What the vault holds, what is coming up, and what was added last.
public sealed class DashboardPage : UserControl
{
    private readonly ShellPage _shell;
    private readonly StackPanel _body = UI.Stack(20);

    public DashboardPage(ShellPage shell)
    {
        _shell = shell;
        _body.Padding = new Thickness(32, 24, 32, 32);
        Content = new ScrollViewer { Content = _body };
        Build();
        AppState.Current.Changed += Build;
        Unloaded += (_, _) => AppState.Current.Changed -= Build;
    }

    private void Build()
    {
        var state = AppState.Current;
        _body.Children.Clear();
        _body.Children.Add(UI.Title("Dashboard"));

        var stats = new VariableSizedWrapGrid { Orientation = Orientation.Horizontal, ItemWidth = 168, ItemHeight = 118 };
        stats.Children.Add(Stat("Documents", state.Documents.Count, "", "documents", accent: true));
        foreach (var category in Categories.All.Where(c => c != Category.Other))
            stats.Children.Add(Stat(category.Label(), state.Documents.Count(d => d.Category == category), category.Glyph(), category.Wire()));
        _body.Children.Add(stats);

        var coming = UI.Stack(10, UI.Subtitle("Coming up"));
        if (state.Upcoming.Count == 0) coming.Children.Add(UI.Text("Nothing expires or falls due in the next 90 days.", secondary: true));
        foreach (var item in state.Upcoming)
        {
            var when = UI.Caption(UI.DaysLeft(item.DaysLeft));
            if (item.DaysLeft <= 30)
                when.Foreground = (Brush)Application.Current.Resources[item.DaysLeft < 0 ? "SystemFillColorCriticalBrush" : "SystemFillColorCautionBrush"];
            var grid = new Grid { ColumnSpacing = 12 };
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            var icon = UI.Glyph(item.Category.Glyph());
            var info = UI.Stack(0, UI.Text(item.Filename), UI.Caption($"{(item.Kind == "due" ? "Due" : "Expires")} {UI.Date(item.Date)}"));
            Grid.SetColumn(info, 1);
            Grid.SetColumn(when, 2);
            grid.Children.Add(icon);
            grid.Children.Add(info);
            grid.Children.Add(when);
            coming.Children.Add(grid);
        }

        var recent = UI.Stack(10, UI.Subtitle("Recently added"));
        foreach (var document in state.Documents.OrderByDescending(d => d.AddedAt).Take(6))
        {
            var link = new HyperlinkButton
            {
                Content = UI.Row(10, UI.Glyph(document.Category.Glyph()),
                                 UI.Stack(0, UI.Text(document.Filename), UI.Caption($"{document.Category.Label()} · {UI.Date(document.AddedAt)}"))),
                Padding = new Thickness(4),
            };
            link.Click += (_, _) => new DocumentWindow(document).Activate();
            recent.Children.Add(link);
        }

        var columns = new Grid { ColumnSpacing = 20 };
        columns.ColumnDefinitions.Add(new ColumnDefinition());
        columns.ColumnDefinitions.Add(new ColumnDefinition());
        var left = UI.Card(coming);
        var right = UI.Card(recent);
        Grid.SetColumn(right, 1);
        columns.Children.Add(left);
        columns.Children.Add(right);
        _body.Children.Add(columns);
    }

    private Button Stat(string label, int value, string glyph, string tag, bool accent = false)
    {
        var number = UI.Text(value.ToString(), "TitleLargeTextBlockStyle");
        var button = new Button
        {
            Content = UI.Stack(4, UI.Row(6, UI.Glyph(glyph, 14), UI.Caption(label)), number),
            HorizontalAlignment = HorizontalAlignment.Stretch,
            VerticalAlignment = VerticalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Left,
            Margin = new Thickness(0, 0, 12, 12),
            Padding = new Thickness(14),
        };
        if (accent) button.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
        button.Click += (_, _) => _shell.Go(tag);
        return button;
    }
}
