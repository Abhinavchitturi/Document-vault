using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// Connect this computer to the vault on another computer (the centre): its address, the code it shows, and a check
/// that the security code matches, so this computer trusts that vault and nothing pretending to be it.
public sealed class ConnectPage : UserControl
{
    public ConnectPage()
    {
        var address = new TextBox { Header = "The other computer's address", PlaceholderText = "for example 192.168.1.20" }.Id("AddressBox");
        var code = new TextBox { Header = "The code it shows", PlaceholderText = "for example K7QF-2M9X" }.Id("CodeBox");
        var problem = new InfoBar { Severity = InfoBarSeverity.Error, IsClosable = false };
        var connect = new Button { Content = "Connect", Style = (Style)Application.Current.Resources["AccentButtonStyle"],
                                   HorizontalAlignment = HorizontalAlignment.Stretch }.Id("ConnectButton");
        var back = UI.Button("Use this computer as the centre instead", async () =>
        {
            AppState.Current.Mode = "here";
            await App.Window!.StartAsync();
        });

        connect.Click += async (_, _) =>
        {
            problem.IsOpen = false;
            if (address.Text.Trim().Length == 0 || code.Text.Trim().Length < 8)
            {
                problem.Message = "Enter the address and the code shown on the other computer.";
                problem.IsOpen = true;
                return;
            }
            connect.IsEnabled = false;
            try
            {
                var fingerprint = await RemoteVault.ProbeAsync(address.Text);
                var check = new ContentDialog
                {
                    XamlRoot = XamlRoot,
                    Title = "Check the security code",
                    Content = UI.Stack(12,
                        UI.Text("On the other computer, Document Vault shows a security code. Does it match this one?"),
                        UI.Text(RemoteVault.SecurityCode(fingerprint), "SubtitleTextBlockStyle"),
                        UI.Text("If it doesn't match, choose Cancel: something on the network may be pretending to be your vault.", secondary: true)),
                    PrimaryButtonText = "It matches", CloseButtonText = "Cancel", DefaultButton = ContentDialogButton.Primary,
                };
                if (await check.ShowAsync() != ContentDialogResult.Primary) { connect.IsEnabled = true; return; }
                var connection = await RemoteVault.PairAsync(address.Text, code.Text, fingerprint, Environment.MachineName);
                AppState.Current.SavedConnection = connection;
                AppState.Current.Mode = "remote";
                await App.Window!.StartAsync();
            }
            catch (Exception e)
            {
                problem.Message = e.Message;
                problem.IsOpen = true;
                connect.IsEnabled = true;
            }
        };

        var steps = UI.Text("On the computer that keeps the vault, open Document Vault → Settings → Connect another computer " +
                            "or phone → Show a code. Then type what it shows here.", secondary: true);
        var form = UI.Stack(14, UI.Title("Connect to your vault"), steps, address, code, problem, connect, back);
        form.Width = 420;
        var card = UI.Card(form, 28);
        card.HorizontalAlignment = HorizontalAlignment.Center;
        card.VerticalAlignment = VerticalAlignment.Center;
        Content = UI.Page(card);
    }
}
