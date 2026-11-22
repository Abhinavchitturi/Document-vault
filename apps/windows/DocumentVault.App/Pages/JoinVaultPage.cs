using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// 1.0: this PC also keeps a vault another computer keeps: the same documents on both, kept up to date, so either one
/// can be off. Its address and the code it shows, a check that the security code matches, then a copy of the vault.
public sealed class JoinVaultPage : UserControl
{
    private readonly StackPanel _form = UI.Stack(14);

    public JoinVaultPage()
    {
        var address = new TextBox { Header = "The other computer's address", PlaceholderText = "for example 192.168.1.20" }.Id("JoinAddressBox");
        var code = new TextBox { Header = "The code it shows", PlaceholderText = "for example K7QF-2M9X" }.Id("JoinCodeBox");
        var problem = new InfoBar { Severity = InfoBarSeverity.Error, IsClosable = false };
        var join = new Button { Content = "Join", Style = (Style)Application.Current.Resources["AccentButtonStyle"],
                                HorizontalAlignment = HorizontalAlignment.Stretch }.Id("JoinButton");
        join.Click += async (_, _) =>
        {
            problem.IsOpen = false;
            join.IsEnabled = false;
            try
            {
                var client = AppState.Current.Client!;
                var probe = await client.SyncProbeAsync(address.Text.Trim());
                var check = new ContentDialog
                {
                    XamlRoot = XamlRoot, Title = "Check the security code",
                    Content = UI.Stack(10,
                        UI.Text("On the other computer, Settings shows a security code next to the code you typed. It must be:"),
                        UI.Text(RemoteVault.SecurityCode(probe.Fingerprint), "TitleTextBlockStyle"),
                        UI.Text("If it's different, don't continue: something else answered at that address.", secondary: true)),
                    PrimaryButtonText = "It matches", CloseButtonText = "Cancel", DefaultButton = ContentDialogButton.Primary,
                };
                if (await check.ShowAsync() != ContentDialogResult.Primary) { join.IsEnabled = true; return; }
                await client.JoinVaultAsync(probe.Address, code.Text.Trim(), probe.Fingerprint);
                await FollowCopyAsync();
            }
            catch (Exception e)
            {
                problem.Message = e.Message;
                problem.IsOpen = true;
                join.IsEnabled = true;
            }
        };
        var back = UI.Button("Back", async () => await App.Window!.CheckSignInAsync());
        _form.Children.Add(UI.Title("Join a vault kept on another computer"));
        _form.Children.Add(UI.Text("This PC will keep the same vault as the other computer: the same documents, family members and " +
                                   "passwords, kept up to date on both. Either one can be off; laptops and phones use the one that's on.",
                                   secondary: true));
        _form.Children.Add(UI.Text("On the other computer, open Settings → Computers that keep this vault → Add a computer.", secondary: true));
        foreach (var e in new UIElement[] { address, code, problem, join, back }) _form.Children.Add(e);
        _form.Width = 420;
        var card = UI.Card(_form, 28);
        card.HorizontalAlignment = HorizontalAlignment.Center;
        card.VerticalAlignment = VerticalAlignment.Center;
        Content = UI.Page(card);
    }

    /// Copying the vault: a progress bar until it's here, then the sign-in (with the same name and password).
    private async Task FollowCopyAsync()
    {
        var bar = new ProgressBar { Maximum = 100, IsIndeterminate = true, Width = 380 };
        var text = UI.Text("Joining…").Id("JoinProgressText");
        _form.Children.Clear();
        _form.Children.Add(UI.Title("Copying the vault"));
        _form.Children.Add(bar);
        _form.Children.Add(text);
        _form.Children.Add(UI.Caption("Documents arrive with their search data, so nothing is read again here."));
        while (true)
        {
            var joining = await AppState.Current.Client!.SyncJoiningAsync();
            if (joining?.State == "copying")
            {
                bar.IsIndeterminate = joining.Total == 0;
                if (joining.Total > 0) bar.Value = joining.Done * 100.0 / joining.Total;
                text.Text = joining.Total > 0 ? $"Copying from {joining.With}: {joining.Done} of {joining.Total} documents"
                                              : $"Joining the vault on {joining.With}…";
            }
            else
            {
                if (joining?.State == "failed")
                    await UI.Alert(XamlRoot, "The copy stopped", joining.Error ?? "It carries on by itself in the background.");
                await App.Window!.CheckSignInAsync();  // the vault is set up now: sign in with the same name and password
                return;
            }
            await Task.Delay(800);
        }
    }
}
