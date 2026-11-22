using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;

namespace DocumentVault.Pages;

/// Every document (or one type), with search. Drop files on the list, or click Add, to put them in the vault.
public sealed class DocumentsPage : UserControl
{
    private readonly Category? _category;
    private readonly AutoSuggestBox _search = new() { PlaceholderText = "Search names, policy or reference numbers", QueryIcon = new SymbolIcon(Symbol.Find), MaxWidth = 360, MinWidth = 180 };
    private readonly ListView _list = new ListView { SelectionMode = ListViewSelectionMode.None, IsItemClickEnabled = true }.Id("DocumentList");
    private readonly TextBlock _count = UI.Caption("");

    public DocumentsPage(Category? category)
    {
        _category = category;
        var add = new Button { Content = UI.Row(8, UI.Glyph("", 14), new TextBlock { Text = "Add documents" }),
                               Style = (Style)Application.Current.Resources["AccentButtonStyle"] }.Id("AddDocumentsButton");
        add.Click += async (_, _) => await PickAsync();
        _search.TextChanged += (_, _) => Fill();
        _list.ItemClick += (_, e) => { if ((e.ClickedItem as FrameworkElement)?.Tag is VaultDocument d) new DocumentWindow(d).Activate(); };

        var header = new Grid { ColumnSpacing = 12 };
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        header.ColumnDefinitions.Add(new ColumnDefinition());
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        _search.HorizontalAlignment = HorizontalAlignment.Right;
        var heading = UI.Title(category?.Label() ?? "All documents");
        heading.TextWrapping = TextWrapping.NoWrap;
        var title = UI.Stack(2, heading, _count);
        Grid.SetColumn(_search, 1);
        Grid.SetColumn(add, 2);
        _search.VerticalAlignment = add.VerticalAlignment = VerticalAlignment.Center;
        header.Children.Add(title);
        header.Children.Add(_search);
        header.Children.Add(add);

        var page = new Grid { Padding = new Thickness(32, 24, 32, 16), RowSpacing = 16, AllowDrop = true };
        page.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        page.RowDefinitions.Add(new RowDefinition());
        Grid.SetRow(_list, 1);
        page.Children.Add(header);
        page.Children.Add(_list);
        page.DragOver += (_, e) =>
        {
            if (e.DataView.Contains(StandardDataFormats.StorageItems))
            {
                e.AcceptedOperation = DataPackageOperation.Copy;
                e.DragUIOverride.Caption = "Add to the vault";
            }
        };
        page.Drop += async (_, e) =>
        {
            if (!e.DataView.Contains(StandardDataFormats.StorageItems)) return;
            var items = await e.DataView.GetStorageItemsAsync();
            await AddAsync(items.Select(i => i.Path).Where(p => !string.IsNullOrEmpty(p)).ToList());
        };
        Content = page;
        Fill();
        AppState.Current.Changed += Fill;
        Unloaded += (_, _) => AppState.Current.Changed -= Fill;
    }

    private void Fill()
    {
        var search = _search.Text.Trim();
        var shown = AppState.Current.Documents
            .Where(d => _category is null || d.Category == _category)
            .Where(d => search.Length == 0 || d.Filename.Contains(search, StringComparison.OrdinalIgnoreCase)
                        || (d.Reference ?? "").Contains(search, StringComparison.OrdinalIgnoreCase)
                        || (d.Issuer ?? "").Contains(search, StringComparison.OrdinalIgnoreCase))
            .OrderByDescending(d => d.AddedAt)
            .ToList();
        _count.Text = $"{shown.Count} document{(shown.Count == 1 ? "" : "s")} · drop files here to add them";
        _list.Items.Clear();
        foreach (var document in shown) _list.Items.Add(Row(document));
        if (AppState.Current.Documents.Count == 0)
            _list.Items.Add(UI.Text("No documents yet. Drop PDFs, Word files or photos here, or click Add documents.", secondary: true));
    }

    private Grid Row(VaultDocument document)
    {
        var grid = new Grid { ColumnSpacing = 14, Padding = new Thickness(4, 8, 4, 8), Tag = document };
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        grid.ColumnDefinitions.Add(new ColumnDefinition());
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(120) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(110) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(80) });
        grid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(40) });  // remove
        var key = document.DueOn is { } due ? $"Due {UI.Date(due)}" : document.ExpiresOn is { } exp ? $"Expires {UI.Date(exp)}" : null;
        var name = UI.Stack(0, UI.Text(document.Filename));
        if (key is not null) name.Children.Add(UI.Caption(key));
        var cells = new UIElement[] { UI.Glyph(document.Category.Glyph()), name, UI.Caption(document.Category.Label()),
                                      UI.Caption(UI.Date(document.AddedAt)), UI.Caption(UI.Size(document.SizeBytes)) };
        for (var i = 0; i < cells.Length; i++)
        {
            Grid.SetColumn((FrameworkElement)cells[i], i);
            ((FrameworkElement)cells[i]).VerticalAlignment = VerticalAlignment.Center;
            grid.Children.Add(cells[i]);
        }

        var menu = new MenuFlyout();
        var open = new MenuFlyoutItem { Text = "Open", Icon = UI.Glyph("") };
        open.Click += (_, _) => new DocumentWindow(document).Activate();
        menu.Items.Add(open);
        if (document.Mine)
        {
            var type = new MenuFlyoutSubItem { Text = "Change type" };
            foreach (var category in Categories.All)
            {
                var choice = new MenuFlyoutItem { Text = category.Label(), IsEnabled = category != document.Category };
                choice.Click += async (_, _) => await Run(() => AppState.Current.Client!.SetCategoryAsync(document, category));
                type.Items.Add(choice);
            }
            menu.Items.Add(type);
            menu.Items.Add(new MenuFlyoutSeparator());
            var remove = new MenuFlyoutItem { Text = "Remove from vault…", Icon = UI.Glyph("") };
            remove.Click += async (_, _) =>
            {
                if (await UI.Confirm(XamlRoot, $"Remove {document.Filename} from the vault?",
                        "The vault's encrypted copy and its search data are deleted. Your original file, if you still have it, isn't touched.",
                        "Remove"))
                    await Run(() => AppState.Current.Client!.DeleteAsync(document));
            };
            menu.Items.Add(remove);
        }
        grid.ContextFlyout = menu;
        if (document.Mine)  // removing, without having to find the right-click menu
        {
            var trash = new Button { Content = UI.Glyph("\uE74D", 14), Padding = new Thickness(8, 6, 8, 6), VerticalAlignment = VerticalAlignment.Center,
                                     Background = new Microsoft.UI.Xaml.Media.SolidColorBrush(Microsoft.UI.Colors.Transparent),
                                     BorderThickness = new Thickness(0) }.Id($"Remove-{document.DocId}");
            ToolTipService.SetToolTip(trash, "Remove from vault");
            trash.Click += async (_, _) =>
            {
                if (await UI.Confirm(XamlRoot, $"Remove {document.Filename} from the vault?",
                        "The vault's encrypted copy and its search data are deleted, on every computer that keeps this vault. " +
                        "Your original file, if you still have it, isn't touched.", "Remove"))
                    await Run(() => AppState.Current.Client!.DeleteAsync(document));
            };
            Grid.SetColumn(trash, 5);
            grid.Children.Add(trash);
        }
        return grid;
    }

    private async Task Run(Func<Task> action)
    {
        try
        {
            await action();
            await App.Window!.Reload();
        }
        catch (Exception e) { await UI.Alert(XamlRoot, "Something went wrong", e.Message); }
    }

    private Task PickAsync() => Adding.PickAsync(XamlRoot);

    private Task AddAsync(IReadOnlyList<string> paths) => Adding.AddAsync(XamlRoot, paths);
}
