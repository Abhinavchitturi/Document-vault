using System.Security.Cryptography;
using System.Text;
using DocumentVault.Core;

namespace DocumentVault;

/// The app's state: the engine (the vault, started as a hidden helper), who is signed in, and what the pages show.
public sealed class AppState
{
    public static AppState Current { get; } = new();

    public string Folder { get; } = VaultFolder();
    public string LogFile => Path.Combine(Folder, "data", "logs", "app-engine.log");
    /// The vault's own Python (its venv); %DOCUMENT_VAULT_PYTHON% overrides it (development, CI).
    public string Python => Environment.GetEnvironmentVariable("DOCUMENT_VAULT_PYTHON") is { Length: > 0 } set
        ? set : Path.Combine(Folder, "venv", "Scripts", "python.exe");
    public bool Installed => File.Exists(Python);

    public EnginePipe? Engine { get; private set; }
    public VaultClient? Client { get; private set; }
    public User? User { get; set; }
    public Info? Info { get; private set; }
    public List<VaultDocument> Documents { get; private set; } = [];
    public List<Upcoming> Upcoming { get; private set; } = [];

    /// Raised (on the UI thread) after the vault's contents were reloaded.
    public event Action? Changed;

    /// Where the vault lives: %DOCUMENT_VAULT_DIR%, else where the installer put it, else %USERPROFILE%\Document-Vault.
    public static string VaultFolder()
    {
        var home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var set = Environment.GetEnvironmentVariable("DOCUMENT_VAULT_DIR");
        if (!string.IsNullOrWhiteSpace(set)) return set;
        var recorded = Path.Combine(home, ".document-vault", "install-dir");
        if (File.Exists(recorded) && File.ReadAllText(recorded).Trim() is { Length: > 0 } path) return path;
        return Path.Combine(home, "Document-Vault");
    }

    public async Task StartAsync()
    {
        Engine = new EnginePipe(Python, Folder, logFile: LogFile);
        await Engine.WaitUntilReadyAsync();
        Client = new VaultClient(Engine, new ProtectedSessionStore());
    }

    public async Task StopAsync()
    {
        if (Engine is not null) await Engine.DisposeAsync();  // the engine (and llama.cpp with it) stops with the app
    }

    public async Task ReloadAsync()
    {
        if (Client is null) return;
        var info = Client.InfoAsync();
        var documents = Client.DocumentsAsync();
        var upcoming = Client.UpcomingAsync();
        Info = await info;
        Documents = await documents;
        Upcoming = await upcoming;
        Changed?.Invoke();
    }
}

/// The sign-in, encrypted for this Windows user with Windows' own data protection (DPAPI), in %LOCALAPPDATA%.
public sealed class ProtectedSessionStore : ISessionStore
{
    private static readonly string File_ = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Document Vault", "session.bin");
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("Document Vault session");

    public string? Load()
    {
        try
        {
            return File.Exists(File_)
                ? Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(File_), Entropy, DataProtectionScope.CurrentUser))
                : null;
        }
        catch (CryptographicException) { return null; }  // another user's, or damaged: sign in again
    }

    public void Save(string? token)
    {
        if (token is null) { File.Delete(File_); return; }
        Directory.CreateDirectory(Path.GetDirectoryName(File_)!);
        File.WriteAllBytes(File_, ProtectedData.Protect(Encoding.UTF8.GetBytes(token), Entropy, DataProtectionScope.CurrentUser));
    }
}
