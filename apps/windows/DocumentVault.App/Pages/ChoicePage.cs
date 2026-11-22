using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// The first start: is this computer the centre (the vault lives here), or does it connect to the vault on another one?
public sealed class ChoicePage : UserControl
{
    public ChoicePage()
    {
        var here = Option("", "Keep the vault on this computer",
            "This computer is the centre: your documents and the AI stay here. Other computers and phones can connect to it.",
            "ChooseHere", async () =>
            {
                AppState.Current.Mode = "here";
                await App.Window!.StartAsync();
            });
        var remote = Option("", "Connect to the vault on another computer",
            "Another computer at home is the centre. This one uses its documents over Wi-Fi, or from anywhere with Tailscale. Nothing to download here.",
            "ChooseRemote", () =>
            {
                AppState.Current.Mode = "remote";
                App.Window!.Show(new ConnectPage());
                return Task.CompletedTask;
            });
        var panel = UI.Stack(16, UI.Title("How will you use Document Vault here?"),
                             UI.Text("You can change this later in Settings.", secondary: true), here, remote);
        panel.MaxWidth = 560;
        panel.HorizontalAlignment = HorizontalAlignment.Center;
        panel.VerticalAlignment = VerticalAlignment.Center;
        Content = UI.Page(panel);
    }

    private static Button Option(string glyph, string title, string detail, string id, Func<Task> choose)
    {
        var heading = UI.Subtitle(title);
        var button = new Button
        {
            Content = UI.Row(16, UI.Glyph(glyph, 28), UI.Stack(4, heading, UI.Text(detail, secondary: true))),
            HorizontalAlignment = HorizontalAlignment.Stretch,
            HorizontalContentAlignment = HorizontalAlignment.Left,
            Padding = new Thickness(20),
        }.Id(id);
        ((StackPanel)((StackPanel)button.Content).Children[1]).MaxWidth = 440;
        button.Click += async (_, _) => await choose();
        return button;
    }
}
