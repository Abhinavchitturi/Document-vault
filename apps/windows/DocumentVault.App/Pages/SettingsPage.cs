using DocumentVault.Core;
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

        if (state.Remote is { } remote)
        {
            // this computer uses the vault on the centre computer
            var disconnect = UI.Button("Disconnect", async () =>
            {
                if (!await UI.Confirm(XamlRoot, "Disconnect from the vault?",
                        "This computer forgets the connection. The vault and its documents stay on the other computer.", "Disconnect"))
                    return;
                await state.StopAsync();
                state.SavedConnection = null;
                state.Mode = null;
                await App.Window!.StartAsync();
            });
            body.Children.Add(UI.Card(UI.Stack(8, UI.Subtitle("This computer"),
                UI.Text($"Connected to the vault on {remote.Connection.Name}."),
                UI.Text("The documents and the AI stay on that computer; it has to be on, with Document Vault open.", secondary: true),
                disconnect)));
        }
        else
        {
            // this computer is the centre: let another computer or a phone connect
            var shown = UI.Stack(6);
            var show = UI.Button("Show a code", async () =>
            {
                try
                {
                    var pairing = await state.Client!.StartPairingAsync(typed: true);
                    shown.Children.Clear();
                    var code = UI.Text(pairing.Code, "TitleTextBlockStyle");
                    shown.Children.Add(UI.Text("On the other computer, choose \"Connect to the vault on another computer\" and type:"));
                    shown.Children.Add(UI.Caption("Code (works once, for 10 minutes)"));
                    shown.Children.Add(code);
                    shown.Children.Add(UI.Caption("Address"));
                    shown.Children.Add(UI.Text(string.Join("   or   ", pairing.Urls.Select(u => new Uri(u).Host))));
                    shown.Children.Add(UI.Caption("Security code (the other computer asks you to check it)"));
                    shown.Children.Add(UI.Text(RemoteVault.SecurityCode(pairing.Fingerprint), "SubtitleTextBlockStyle"));
                }
                catch (Exception e) { shown.Children.Clear(); shown.Children.Add(UI.Text(e.Message, secondary: true)); }
            }, accent: true).Id("ShowCodeButton");
            body.Children.Add(UI.Card(UI.Stack(8, UI.Subtitle("Connect another computer or phone"),
                UI.Text("This computer is the centre. Another laptop or a phone can use this vault over your Wi-Fi, or from " +
                        "anywhere with Tailscale. Document Vault has to be open here for them to connect.", secondary: true),
                show, shown,
                UI.Button("Use the vault on another computer instead", async () =>
                {
                    if (!await UI.Confirm(XamlRoot, "Use another computer's vault?",
                            "This computer stops being a centre. Its own vault stays in its folder, untouched, and you can switch back in Settings.",
                            "Continue"))
                        return;
                    await state.StopAsync();
                    state.Mode = "remote";
                    App.Window!.Show(new ConnectPage());
                }))));
        }

        var details = UI.Stack(6, UI.Subtitle("Vault"), UI.Caption("Loading…"));
        body.Children.Add(UI.Card(details));
        if (state.Remote is null) body.Children.Add(UI.Card(UI.Stack(8, UI.Subtitle("Local AI"), new AIDownloadPanel())));
        body.Children.Add(UI.Card(UI.Stack(8, UI.Subtitle("Privacy"),
            UI.Text("Everything stays on this PC. Nothing listens on the network for this app; phones connect only if you pair them.", secondary: true),
            UI.Text(state.Remote is null ? $"Vault folder: {state.VaultHome}" : "The vault's folder is on the other computer.", secondary: true),
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
