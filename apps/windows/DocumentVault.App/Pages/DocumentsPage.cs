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
    private readonly AutoSuggestBox _search = new() { PlaceholderText = "Search names, policy or reference numbers", QueryIcon = new SymbolIcon(Symbol.Find), Width = 360 };
    private readonly ListView _list = new() { SelectionMode = ListViewSelectionMode.None, IsItemClickEnabled = true };
    private readonly TextBlock _count = UI.Caption("");

    public DocumentsPage(Category? category)
    {
        _category = category;
        var add = new Button { Content = UI.Row(8, UI.Glyph("", 14), new TextBlock { Text = "Add documents" }),
                               Style = (Style)Application.Current.Resources["AccentButtonStyle"] };
        add.Click += async (_, _) => await PickAsync();
        _search.TextChanged += (_, _) => Fill();
        _list.ItemClick += (_, e) => { if ((e.ClickedItem as FrameworkElement)?.Tag is VaultDocument d) new DocumentWindow(d).Activate(); };

        var header = new Grid { ColumnSpacing = 12 };
        header.ColumnDefinitions.Add(new ColumnDefinition());
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        var title = UI.Stack(2, UI.Title(category?.Label() ?? "All documents"), _count);
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

    private async Task PickAsync()
    {
        var picker = new FileOpenPicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(App.Window!));
        picker.FileTypeFilter.Add("*");
        var files = await picker.PickMultipleFilesAsync();
        await AddAsync(files.Select(f => f.Path).ToList());
    }

    private async Task AddAsync(IReadOnlyList<string> paths)
    {
        if (paths.Count == 0) return;
        var progress = new ContentDialog
        {
            XamlRoot = XamlRoot, Title = "Adding documents…",
            Content = UI.Stack(10, new ProgressBar { IsIndeterminate = true },
                               UI.Text("Reading and filing them. Scans and photos take a little longer.", secondary: true)),
        };
        _ = progress.ShowAsync();
        try
        {
            var results = await AppState.Current.Client!.UploadAsync(paths);
            progress.Hide();
            await App.Window!.Reload();
            var lines = results.Select(r => r.Status switch
            {
                "added" => $"✓ {r.Filename}: filed as {r.Document?.Category.Label() ?? "a document"}",
                "duplicate" => $"= {r.Filename}: already in the vault",
                _ => $"✗ {r.Filename}: {r.Error ?? "couldn't be added"}",
            });
            await UI.Alert(XamlRoot, "Added to your vault", string.Join("\n", lines));
        }
        catch (Exception e)
        {
            progress.Hide();
            await UI.Alert(XamlRoot, "Couldn't add them", e.Message);
        }
    }
}
