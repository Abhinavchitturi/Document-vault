using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// Account, the assistant, and where things are kept.
public sealed class SettingsPage : UserControl
{
    public SettingsPage()
    {
        var state = AppState.Current;
        var body = UI.Stack(16);
        body.Padding = new Thickness(32, 24, 32, 32);
        body.MaxWidth = 760;
        body.HorizontalAlignment = HorizontalAlignment.Left;
        body.Children.Add(UI.Title("Settings"));

        var signOut = UI.Button("Sign out", async () =>
        {
            await state.Client!.SignOutAsync();
            await App.Window!.CheckSignInAsync();
        });
        body.Children.Add(UI.Card(UI.Stack(8, UI.Subtitle("Account"),
            UI.Text($"Signed in as {state.User?.Name} ({(state.User?.IsAdmin == true ? "admin" : "family member")})"), signOut)));

        var details = UI.Stack(6, UI.Subtitle("Vault"), UI.Caption("Loading…"));
        body.Children.Add(UI.Card(details));
        body.Children.Add(UI.Card(UI.Stack(8, UI.Subtitle("Privacy"),
            UI.Text("Everything stays on this PC. Nothing listens on the network for this app; phones connect only if you pair them.", secondary: true),
            UI.Text($"Vault folder: {state.Folder}", secondary: true),
            UI.Button("Show engine log", () => System.Diagnostics.Process.Start("explorer.exe", $"/select,\"{state.LogFile}\"")))));
        Content = new ScrollViewer { Content = body };

        Loaded += async (_, _) =>
        {
            try
            {
                var s = await state.Client!.SettingsAsync();
                details.Children.RemoveAt(1);
                details.Children.Add(UI.Text($"Assistant: {(s.LlmStatus.Ready ? "ready" : "offline")}. {s.LlmStatus.Message}"));
                details.Children.Add(UI.Text($"Answer model: {s.LlmModel ?? "none"}", secondary: true));
                details.Children.Add(UI.Text($"Search: {s.SearchMethod}, {s.EmbeddingModel}", secondary: true));
                details.Children.Add(UI.Text($"Encryption key: {s.KeyKeptIn}", secondary: true));
            }
            catch (Exception e) { details.Children.Add(UI.Text(e.Message, secondary: true)); }
        };
    }
}
