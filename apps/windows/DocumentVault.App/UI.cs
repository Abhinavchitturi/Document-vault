using Microsoft.UI.Text;
using Windows.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using DocumentVault.Core;

namespace DocumentVault;

/// Small helpers to build the pages in code with the platform's own styles.
public static class UI
{
    public static TextBlock Text(string text, string style = "BodyTextBlockStyle", bool secondary = false)
    {
        var block = new TextBlock
        {
            Text = text,
            Style = (Style)Application.Current.Resources[style],
            TextWrapping = TextWrapping.Wrap,
            IsTextSelectionEnabled = true,
        };
        if (secondary) block.Foreground = (Brush)Application.Current.Resources["TextFillColorSecondaryBrush"];
        return block;
    }

    /// A large bold title, like macOS: Apple's design rather than Windows' lighter titles.
    public static TextBlock Title(string text)
    {
        var title = Text(text, "TitleTextBlockStyle");
        title.FontSize = 30;
        title.FontWeight = Microsoft.UI.Text.FontWeights.Bold;
        title.FontFamily = new FontFamily("Segoe UI Variable Display, Segoe UI");
        title.CharacterSpacing = -20;  // Apple tightens large titles slightly
        return title;
    }
    public static TextBlock Subtitle(string text) => Text(text, "SubtitleTextBlockStyle");
    public static TextBlock Caption(string text) => Text(text, "CaptionTextBlockStyle", secondary: true);

    /// A rounded card on the layer above the window's backdrop, like Windows 11's Settings.
    public static Border Card(UIElement child, double padding = 16) => new()
    {
        Child = child,
        Padding = new Thickness(padding),
        CornerRadius = new CornerRadius(14),
        Background = (Brush)Application.Current.Resources["CardBackgroundFillColorDefaultBrush"],
        BorderBrush = (Brush)Application.Current.Resources["CardStrokeColorDefaultBrush"],
        BorderThickness = new Thickness(1),
    };

    /// A full-window page on Apple's solid grey (black in dark mode).
    public static Grid Page(UIElement child) => new()
    {
        Background = (Brush)Application.Current.Resources["VaultBackgroundBrush"],
        Children = { child },
    };

    public static StackPanel Stack(double spacing, params UIElement[] children)
    {
        var panel = new StackPanel { Spacing = spacing };
        foreach (var child in children) panel.Children.Add(child);
        return panel;
    }

    public static StackPanel Row(double spacing, params UIElement[] children)
    {
        var panel = new StackPanel { Orientation = Orientation.Horizontal, Spacing = spacing };
        foreach (var child in children) panel.Children.Add(child);
        return panel;
    }

    public static Button Button(string label, Action onClick, bool accent = false)
    {
        var button = new Button { Content = label };
        if (accent) button.Style = (Style)Application.Current.Resources["AccentButtonStyle"];
        button.Click += (_, _) => onClick();
        return button;
    }

    /// A stable name for UI Automation (the click-through test, screen readers).
    public static T Id<T>(this T element, string id) where T : DependencyObject
    {
        Microsoft.UI.Xaml.Automation.AutomationProperties.SetAutomationId(element, id);
        return element;
    }

    public static FontIcon Glyph(string glyph, double size = 16) => new() { Glyph = glyph, FontSize = size };

    /// Segoe Fluent Icons for each type of document.
    public static string Glyph(this Category category) => category switch
    {
        Category.Insurance => "",  // shield
        Category.Warranty => "",  // check mark
        Category.Bill => "",  // document
        Category.Property => "",  // home
        Category.Identity => "",  // contact
        _ => "",  // folder
    };

    public static async Task Alert(XamlRoot? root, string title, string message)
    {
        if (root is null) return;
        await new ContentDialog { XamlRoot = root, Title = title, Content = message, CloseButtonText = "OK" }.ShowAsync();
    }

    public static async Task<bool> Confirm(XamlRoot root, string title, string message, string action)
    {
        var dialog = new ContentDialog
        {
            XamlRoot = root, Title = title, Content = message, PrimaryButtonText = action, CloseButtonText = "Cancel",
            DefaultButton = ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    public static string Date(string value) =>
        DateTime.TryParse(value.Length >= 10 ? value[..10] : value, out var date) ? date.ToString("d MMM yyyy") : value;

    public static string Size(int bytes) => bytes switch
    {
        < 1024 => $"{bytes} B",
        < 1024 * 1024 => $"{bytes / 1024.0:0} KB",
        _ => $"{bytes / 1024.0 / 1024.0:0.0} MB",
    };

    public static string DaysLeft(int days) => days switch
    {
        < -1 => $"{-days} days ago",
        -1 => "Yesterday",
        0 => "Today",
        1 => "Tomorrow",
        _ => $"In {days} days",
    };

    public static FontWeight Semibold => FontWeights.SemiBold;
}
