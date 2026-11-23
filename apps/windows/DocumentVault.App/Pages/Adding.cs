using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.ApplicationModel.DataTransfer;
using Windows.Storage.Pickers;

namespace DocumentVault.Pages;

/// Adding documents, from anywhere: the "Add documents" button at the top of the side pane (every page), the one on
/// All documents, the dashboard's "Add your first documents", or files dropped anywhere on the window.
public static class Adding
{
    public static async Task PickAsync(XamlRoot root)
    {
        var picker = new FileOpenPicker();
        WinRT.Interop.InitializeWithWindow.Initialize(picker, WinRT.Interop.WindowNative.GetWindowHandle(App.Window!));
        foreach (var ext in AcceptedFiles.Extensions) picker.FileTypeFilter.Add(ext);  // no zips or other files
        var files = await picker.PickMultipleFilesAsync();
        await AddAsync(root, files.Select(f => f.Path).ToList());
    }

    public static async Task AddAsync(XamlRoot root, IReadOnlyList<string> paths)
    {
        if (paths.Count == 0) return;
        var (send, refused) = AcceptedFiles.Sort(paths);  // zips and other files are turned away here, not sent
        if (send.Count == 0)
        {
            await UI.Alert(root, "Couldn't add them", string.Join("\n", refused.Select(r => $"✗ {r.Filename}: {r.Error}")));
            return;
        }
        var progress = new ContentDialog
        {
            XamlRoot = root, Title = "Adding documents…",
            Content = UI.Stack(10, new ProgressBar { IsIndeterminate = true },
                               UI.Text("Reading and filing them. Scans and photos take a little longer.", secondary: true)),
        };
        _ = progress.ShowAsync();
        try
        {
            var results = (await AppState.Current.Client!.UploadAsync(send)).Concat(refused).ToList();
            progress.Hide();
            await App.Window!.Reload();
            var lines = results.Select(r => r.Status switch
            {
                "added" => $"✓ {r.Filename}: filed as {r.Document?.Category.Label() ?? "a document"}",
                "duplicate" => $"= {r.Filename}: already in the vault",
                _ => $"✗ {r.Filename}: {r.Error ?? "couldn't be added"}",
            });
            await UI.Alert(root, "Added to your vault", string.Join("\n", lines));
        }
        catch (Exception e)
        {
            progress.Hide();
            await UI.Alert(root, "Couldn't add them", e.Message);
        }
    }

    /// Let files be dropped on this part of the window to add them.
    public static void AcceptDrops(UIElement target)
    {
        target.AllowDrop = true;
        target.DragOver += (_, e) =>
        {
            if (!e.DataView.Contains(StandardDataFormats.StorageItems)) return;
            e.AcceptedOperation = DataPackageOperation.Copy;
            e.DragUIOverride.Caption = "Add to the vault";
        };
        target.Drop += async (_, e) =>
        {
            if (!e.DataView.Contains(StandardDataFormats.StorageItems)) return;
            var items = await e.DataView.GetStorageItemsAsync();
            await AddAsync(target.XamlRoot, items.Select(i => i.Path).Where(p => !string.IsNullOrEmpty(p)).ToList());
        };
    }
}
