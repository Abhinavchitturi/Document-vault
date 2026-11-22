using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// The app: a navigation pane like Windows 11's Settings, and the chosen page beside it.
public sealed class ShellPage : UserControl
{
    private readonly NavigationView _nav = new()
    {
        IsBackButtonVisible = NavigationViewBackButtonVisible.Collapsed,
        IsSettingsVisible = true,
        PaneDisplayMode = NavigationViewPaneDisplayMode.Left,
        OpenPaneLength = 240,
    };

    public ShellPage()
    {
        _nav.MenuItems.Add(Item("Dashboard", "", "dashboard"));
        _nav.MenuItems.Add(Item("Ask", "", "chat"));
        _nav.MenuItems.Add(Item("All documents", "", "documents"));
        _nav.MenuItems.Add(new NavigationViewItemHeader { Content = "Types" });
        foreach (var category in Categories.All)
            _nav.MenuItems.Add(Item(category.Label(), category.Glyph(), category.Wire()));
        _nav.PaneFooter = new AccountFooter();
        _nav.SelectionChanged += (_, e) =>
        {
            if (e.IsSettingsSelected) { _nav.Content = new SettingsPage(); return; }
            Go((e.SelectedItem as NavigationViewItem)?.Tag as string ?? "dashboard");
        };
        _nav.SelectedItem = _nav.MenuItems[0];
        // the pages sit on Apple's solid grey (black in dark mode); the pane stays translucent, like a Mac sidebar
        _nav.Resources["NavigationViewContentBackground"] = Application.Current.Resources["VaultBackgroundBrush"];
        _nav.Resources["NavigationViewContentGridBorderBrush"] = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Transparent);
        var layout = new Grid();
        layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        layout.RowDefinitions.Add(new RowDefinition());
        var bar = new SearchReadyBar();
        Grid.SetRow(_nav, 1);
        layout.Children.Add(bar);
        layout.Children.Add(_nav);
        Content = layout;
    }

    private static NavigationViewItem Item(string label, string glyph, string tag) =>
        new NavigationViewItem { Content = label, Icon = UI.Glyph(glyph), Tag = tag }.Id($"Nav-{tag}");

    public void Go(string tag)
    {
        _nav.Content = tag switch
        {
            "dashboard" => new DashboardPage(this),
            "chat" => new ChatPage(),
            "documents" => new DocumentsPage(null),
            _ => new DocumentsPage(Enum.Parse<Category>(tag, ignoreCase: true)),
        };
        var item = _nav.MenuItems.OfType<NavigationViewItem>().FirstOrDefault(i => (string)i.Tag == tag);
        if (item is not null && _nav.SelectedItem != item) _nav.SelectedItem = item;
    }
}

/// Who is signed in, and whether the assistant is ready, at the bottom of the pane.
public sealed class AccountFooter : UserControl
{
    public AccountFooter()
    {
        var state = AppState.Current;
        var who = UI.Text(state.User?.Name ?? "");
        who.FontWeight = UI.Semibold;
        var assistant = UI.Caption("");
        void Refresh() => assistant.Text = state.Info?.LlmStatus.Ready == true ? "● Assistant ready" : "● Assistant offline";
        Refresh();
        state.Changed += Refresh;
        Unloaded += (_, _) => state.Changed -= Refresh;
        Content = new Border { Padding = new Thickness(16, 10, 16, 12), Child = UI.Stack(2, who, assistant) };
    }
}
