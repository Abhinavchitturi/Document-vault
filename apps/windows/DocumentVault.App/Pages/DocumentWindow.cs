using System.Runtime.InteropServices.WindowsRuntime;
using System.Text;
using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Data.Pdf;
using Windows.Storage.Streams;

namespace DocumentVault.Pages;

/// An original document in its own window, decrypted in memory only: nothing is written to disk.
public sealed class DocumentWindow : Window
{
    private readonly StackPanel _pages = new() { Spacing = 16, Padding = new Thickness(24), HorizontalAlignment = HorizontalAlignment.Center };

    public DocumentWindow(VaultDocument document)
    {
        Title = $"{document.Filename} · Document Vault";
        SystemBackdrop = new MicaBackdrop();
        AppWindow.Resize(new Windows.Graphics.SizeInt32(900, 1040));
        _pages.Children.Add(new ProgressRing { IsActive = true });
        Content = new ScrollViewer { Content = _pages, ZoomMode = ZoomMode.Enabled, HorizontalScrollBarVisibility = ScrollBarVisibility.Auto };
        Activated += async (_, _) => { if (_pages.Children.Count == 1 && _pages.Children[0] is ProgressRing) await LoadAsync(document); };
    }

    private async Task LoadAsync(VaultDocument document)
    {
        try
        {
            var data = await AppState.Current.Client!.OriginalAsync(document.DocId);
            _pages.Children.Clear();
            if (data.Length > 4 && Encoding.ASCII.GetString(data, 0, 4) == "%PDF")
            {
                var pdf = await PdfDocument.LoadFromStreamAsync(await Stream(data));
                for (uint i = 0; i < pdf.PageCount; i++)
                {
                    using var page = pdf.GetPage(i);
                    var rendered = new InMemoryRandomAccessStream();
                    await page.RenderToStreamAsync(rendered, new PdfPageRenderOptions { DestinationWidth = 1600 });
                    _pages.Children.Add(await Picture(rendered));
                }
            }
            else
            {
                try { _pages.Children.Add(await Picture(await Stream(data))); }
                catch { _pages.Children.Add(UI.Text(Encoding.UTF8.GetString(data))); }  // a text file
            }
        }
        catch (Exception e)
        {
            _pages.Children.Clear();
            _pages.Children.Add(UI.Text($"Couldn't open it: {e.Message}"));
        }
    }

    private static async Task<IRandomAccessStream> Stream(byte[] data)
    {
        var stream = new InMemoryRandomAccessStream();
        await stream.WriteAsync(data.AsBuffer());
        stream.Seek(0);
        return stream;
    }

    private static async Task<Border> Picture(IRandomAccessStream stream)
    {
        stream.Seek(0);
        var bitmap = new BitmapImage();
        await bitmap.SetSourceAsync(stream);
        return new Border
        {
            Child = new Image { Source = bitmap, MaxWidth = 820 },
            Background = new SolidColorBrush(Microsoft.UI.Colors.White),
            CornerRadius = new CornerRadius(4),
        };
    }
}
