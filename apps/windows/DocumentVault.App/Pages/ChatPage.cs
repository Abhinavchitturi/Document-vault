using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace DocumentVault.Pages;

/// Ask in plain English; the answer streams in, citing the pages it came from.
public sealed class ChatPage : UserControl
{
    private readonly StackPanel _messages = UI.Stack(18);
    private readonly ScrollViewer _scroller;
    private readonly TextBox _question = new() { PlaceholderText = "Ask about your documents…", AcceptsReturn = false };
    private readonly Button _send = new() { Content = UI.Glyph(""), Style = (Style)Application.Current.Resources["AccentButtonStyle"] };
    private readonly List<ChatTurn> _history = [];
    private CancellationTokenSource? _answering;

    private static readonly string[] Suggestions =
        ["When does my bike insurance expire?", "What is the electricity bill for August?",
         "Find the washing machine warranty", "What is the carpet area of our flat?"];

    public ChatPage()
    {
        _messages.Padding = new Thickness(32, 24, 32, 24);
        _messages.MaxWidth = 820;
        _scroller = new ScrollViewer { Content = _messages };
        Welcome();

        _send.Click += (_, _) => { if (_answering is not null) _answering.Cancel(); else Ask(_question.Text); };
        _question.KeyDown += (_, e) => { if (e.Key == Windows.System.VirtualKey.Enter) Ask(_question.Text); };
        var composer = new Grid { ColumnSpacing = 8, Margin = new Thickness(32, 0, 32, 20), MaxWidth = 820 };
        composer.ColumnDefinitions.Add(new ColumnDefinition());
        composer.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        Grid.SetColumn(_send, 1);
        composer.Children.Add(_question);
        composer.Children.Add(_send);

        var page = new Grid();
        page.RowDefinitions.Add(new RowDefinition());
        page.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        Grid.SetRow(composer, 1);
        page.Children.Add(_scroller);
        page.Children.Add(composer);
        Content = page;
        Loaded += (_, _) => _question.Focus(FocusState.Programmatic);
    }

    private void Welcome()
    {
        var state = AppState.Current;
        var welcome = UI.Stack(12, UI.Title("What would you like to know?"),
            UI.Text($"Ask about your {state.Documents.Count} documents: expiry dates, amounts, policy numbers, warranties.", secondary: true));
        if (state.Info?.LlmStatus is { Ready: false } offline)
            welcome.Children.Add(new InfoBar { IsOpen = true, IsClosable = false, Severity = InfoBarSeverity.Warning, Message = offline.Message });
        var grid = new VariableSizedWrapGrid { Orientation = Orientation.Horizontal, ItemWidth = 380 };
        foreach (var suggestion in Suggestions)
        {
            var button = new Button { Content = suggestion, HorizontalAlignment = HorizontalAlignment.Stretch, Margin = new Thickness(0, 0, 8, 8) };
            button.Click += (_, _) => Ask(suggestion);
            grid.Children.Add(button);
        }
        welcome.Children.Add(grid);
        _messages.Children.Add(welcome);
    }

    private async void Ask(string text)
    {
        text = text.Trim();
        var client = AppState.Current.Client;
        if (text.Length == 0 || _answering is not null || client is null) return;
        if (_history.Count == 0) _messages.Children.Clear();
        _question.Text = "";

        var bubble = new Border
        {
            Background = (Brush)Application.Current.Resources["AccentFillColorDefaultBrush"],
            CornerRadius = new CornerRadius(16, 16, 4, 16),
            Padding = new Thickness(14, 8, 14, 8),
            HorizontalAlignment = HorizontalAlignment.Right,
            MaxWidth = 560,
            Child = new TextBlock
            {
                Text = text, TextWrapping = TextWrapping.Wrap, IsTextSelectionEnabled = true,
                Foreground = (Brush)Application.Current.Resources["TextOnAccentFillColorPrimaryBrush"],
            },
        };
        _messages.Children.Add(bubble);

        var answer = UI.Text("");
        var sources = new StackPanel { Orientation = Orientation.Horizontal, Spacing = 6 };
        var working = new ProgressRing { IsActive = true, Width = 18, Height = 18, HorizontalAlignment = HorizontalAlignment.Left };
        var reply = UI.Stack(8, working, answer, sources);
        _messages.Children.Add(reply);

        _answering = new CancellationTokenSource();
        _send.Content = UI.Glyph("");  // stop
        List<Source> found = [];
        try
        {
            await foreach (var evt in client.ChatAsync(text, _history.TakeLast(6).ToList(), cancel: _answering.Token))
            {
                switch (evt)
                {
                    case ChatEvent.Sources s: found = s.Items; break;
                    case ChatEvent.Token t: working.IsActive = false; working.Visibility = Visibility.Collapsed; answer.Text += t.Text; break;
                    case ChatEvent.Done d:
                        foreach (var n in d.Citations.Where(n => n >= 1 && n <= found.Count))
                        {
                            var source = found[n - 1];
                            var chip = new HyperlinkButton { Content = $"{source.Filename} · p. {source.Page}" };
                            chip.Click += (_, _) =>
                            {
                                var document = AppState.Current.Documents.FirstOrDefault(x => x.DocId == source.DocId);
                                if (document is not null) new DocumentWindow(document).Activate();
                            };
                            sources.Children.Add(chip);
                        }
                        break;
                    case ChatEvent.Error e: reply.Children.Add(new InfoBar { IsOpen = true, IsClosable = false, Severity = InfoBarSeverity.Warning, Message = e.Message }); break;
                }
                _scroller.ChangeView(null, _scroller.ScrollableHeight, null);
            }
        }
        catch (OperationCanceledException) { }
        catch (VaultException e) when (e.Kind == VaultErrorKind.SignedOut) { await App.Window!.CheckSignInAsync(); return; }
        catch (Exception e)
        {
            reply.Children.Add(new InfoBar { IsOpen = true, IsClosable = false, Severity = InfoBarSeverity.Warning, Message = e.Message });
        }
        working.Visibility = Visibility.Collapsed;
        _history.Add(new ChatTurn("user", text));
        _history.Add(new ChatTurn("assistant", answer.Text));
        _answering = null;
        _send.Content = UI.Glyph("");
    }
}
