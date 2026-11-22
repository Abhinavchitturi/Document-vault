using DocumentVault.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace DocumentVault.Pages;

/// Sign in, or (the first time the vault opens) choose the admin's name and password.
public sealed class SignInPage : UserControl
{
    public SignInPage(AuthStatus status)
    {
        var firstTime = status.SetupNeeded;
        var name = new TextBox { Header = "Name", Text = status.SuggestedName ?? "" }.Id("NameBox");
        var password = new PasswordBox { Header = "Password", PlaceholderText = firstTime ? "8 or more characters" : "" }.Id("PasswordBox");
        var again = new PasswordBox { Header = "Password again", Visibility = firstTime ? Visibility.Visible : Visibility.Collapsed }.Id("PasswordAgainBox");
        var problem = new InfoBar { Severity = InfoBarSeverity.Error, IsClosable = false };
        var submit = new Button
        {
            Content = firstTime ? "Create vault" : "Sign in",
            Style = (Style)Application.Current.Resources["AccentButtonStyle"],
            HorizontalAlignment = HorizontalAlignment.Stretch,
        }.Id("SubmitButton");

        async void Go()
        {
            var ready = name.Text.Trim().Length > 0 && password.Password.Length >= 8 && (!firstTime || password.Password == again.Password);
            if (!ready)
            {
                problem.Message = firstTime && password.Password != again.Password ? "The two passwords don't match."
                                                                                   : "Enter your name and a password of 8 or more characters.";
                problem.IsOpen = true;
                return;
            }
            submit.IsEnabled = false;
            try
            {
                var client = AppState.Current.Client!;
                AppState.Current.User = firstTime ? await client.SetUpAsync(name.Text.Trim(), password.Password)
                                                  : await client.SignInAsync(name.Text.Trim(), password.Password);
                App.Window!.Show(new ShellPage());
                await App.Window.Reload();
            }
            catch (Exception e)
            {
                problem.Message = e.Message;
                problem.IsOpen = true;
                password.Password = again.Password = "";
                submit.IsEnabled = true;
            }
        }

        submit.Click += (_, _) => Go();
        foreach (var box in new Control[] { name, password, again })
            box.KeyDown += (_, e) => { if (e.Key == Windows.System.VirtualKey.Enter) Go(); };

        var form = UI.Stack(14,
            UI.Title(firstTime ? "Welcome to Document Vault" : "Document Vault"),
            UI.Text(firstTime ? "Choose the name and password for this vault's admin. Your documents never leave this PC."
                              : "Your family's papers, answered privately on this PC.", secondary: true),
            name, password, again, problem, submit);
        form.Width = 380;
        form.HorizontalAlignment = HorizontalAlignment.Center;
        form.VerticalAlignment = VerticalAlignment.Center;
        var card = UI.Card(form, 28);
        card.HorizontalAlignment = HorizontalAlignment.Center;
        card.VerticalAlignment = VerticalAlignment.Center;
        Content = UI.Page(card);
    }
}
