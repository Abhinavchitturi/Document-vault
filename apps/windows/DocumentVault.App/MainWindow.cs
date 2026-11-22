using DocumentVault.Core;
using DocumentVault.Pages;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace DocumentVault;

/// The one main window: starting up, then sign-in, then the app (ShellPage).
public sealed class MainWindow : Window
{
    private readonly ContentControl _host = new()
    {
        HorizontalContentAlignment = HorizontalAlignment.Stretch,
        VerticalContentAlignment = VerticalAlignment.Stretch,
    };
    private readonly Grid _root = new();
    private bool _started;

    public MainWindow()
    {
        Title = "Document Vault";
        SystemBackdrop = new MicaBackdrop();  // Windows 11's material, like its own apps
        ExtendsContentIntoTitleBar = false;
        AppWindow.Resize(new Windows.Graphics.SizeInt32(1240, 820));
        var icon = Path.Combine(AppContext.BaseDirectory, "DocumentVault.ico");
        if (File.Exists(icon)) AppWindow.SetIcon(icon);
        _root.Children.Add(_host);
        Content = _root;
        Activated += async (_, _) =>
        {
            if (_started) return;
            _started = true;
            await StartAsync();
        };
        Closed += async (_, _) =>
        {
            Pages.UpdateBar.InstallIfWaiting();  // "Update when I quit": the installer runs now, quietly, no reopening
            await AppState.Current.StopAsync();
        };
    }

    public void Show(UIElement page) => _host.Content = page;

    public async Task StartAsync()
    {
        var state = AppState.Current;
        if (state.Mode is null)  // first start: this computer as the centre, or connect to another one
        {
            Show(new ChoicePage());
            return;
        }
        if (state.Mode == "remote")
        {
            if (state.SavedConnection is not { } connection) { Show(new ConnectPage()); return; }
            Show(new StatusPage($"Connecting to {connection.Name}…", null, busy: true));
            try
            {
                state.StartRemote(connection);
                await CheckSignInAsync();
                _ = state.RefreshCentresAsync();
            }
            catch (Exception e)
            {
                Show(new StatusPage("Couldn't reach the vault", e.Message, busy: false, retry: true));
            }
            return;
        }
        if (!state.EngineReady)  // this computer keeps the vault: download its engine first (once, or after an update)
        {
            Show(new EngineSetupPage());
            return;
        }
        if (!state.Installed)
        {
            Show(new StatusPage("Document Vault isn't set up on this PC yet",
                $"Install it again with DocumentVault-Setup.exe from github.com/Abhinavchitturi/Document-vault/releases. Looked for it in {state.Folder}.",
                busy: false));
            return;
        }
        Show(new StatusPage("Opening your vault…", "The first time after installing takes a little longer.", busy: true));
        try
        {
            await state.StartAsync();
            await CheckSignInAsync();
        }
        catch (Exception e)
        {
            Show(new StatusPage("Document Vault couldn't open", e.Message, busy: false, retry: true));
        }
    }

    public async Task CheckSignInAsync()
    {
        var state = AppState.Current;
        var status = await state.Client!.AuthStatusAsync();
        if (status.User is { } user)
        {
            state.User = user;
            Show(new ShellPage());
            await Reload();
        }
        else
        {
            Show(new SignInPage(status));
        }
    }

    public async Task Reload()
    {
        try { await AppState.Current.ReloadAsync(); }
        catch (VaultException e) when (e.Kind == VaultErrorKind.SignedOut) { await CheckSignInAsync(); }
        catch (Exception e) { await UI.Alert(Content.XamlRoot, "Something went wrong", e.Message); }
    }
}
