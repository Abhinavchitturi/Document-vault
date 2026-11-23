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
        _nav.MenuItems.Add(_typesHeader);
        ShowUsedTypes();
        AppState.Current.Changed += ShowUsedTypes;
        Unloaded += (_, _) => AppState.Current.Changed -= ShowUsedTypes;
        // adding documents, from every page (and files dropped anywhere on the window)
        var add = new Button
        {
            Content = UI.Row(8, UI.Glyph("\uE710", 14), new TextBlock { Text = "Add documents" }),
            Style = (Style)Application.Current.Resources["AccentButtonStyle"],
            HorizontalAlignment = HorizontalAlignment.Stretch, Margin = new Thickness(12, 4, 12, 8),
        }.Id("AddAnywhereButton");
        add.Click += async (_, _) => await Adding.PickAsync(XamlRoot);
        _nav.PaneCustomContent = add;
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
        layout.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        layout.RowDefinitions.Add(new RowDefinition());
        var update = new UpdateBar();
        var bar = new SearchReadyBar();
        Grid.SetRow(bar, 1);
        Grid.SetRow(_nav, 2);
        layout.Children.Add(update);
        layout.Children.Add(bar);
        layout.Children.Add(_nav);
        Adding.AcceptDrops(layout);
        Content = layout;
        WatchAssistant();
    }

    /// The assistant starts a moment after the vault opens (its model loads): check again until it's ready, so
    /// "Assistant offline" doesn't stay on screen.
    private void WatchAssistant()
    {
        var checks = 0;
        var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(3) };
        timer.Tick += async (_, _) =>
        {
            var status = AppState.Current.Info?.LlmStatus;
            if (++checks > 60 || status?.Ready == true || status?.CanDownload == true || !IsLoaded) { timer.Stop(); return; }
            await App.Window!.Reload();
        };
        timer.Start();
        Unloaded += (_, _) => timer.Stop();
    }

    private readonly NavigationViewItemHeader _typesHeader = new() { Content = "Types" };

    /// Only the types that have documents: the others appear once a document of that type is added.
    private void ShowUsedTypes()
    {
        var used = Categories.All.Where(c => AppState.Current.Documents.Any(d => d.Category == c)).ToList();
        var shown = _nav.MenuItems.OfType<NavigationViewItem>().Where(i => Enum.TryParse<Category>((string)i.Tag, true, out _)).ToList();
        if (shown.Count > 0 && shown.Select(i => (string)i.Tag).SequenceEqual(used.Select(c => c.Wire()))) return;
        foreach (var item in shown) _nav.MenuItems.Remove(item);
        foreach (var category in used) _nav.MenuItems.Add(Item(category.Label(), category.Glyph(), category.Wire()));
        _typesHeader.Visibility = used.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
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
