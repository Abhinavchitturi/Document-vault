using System.Security.Cryptography;
using System.Text;
using DocumentVault.Core;

namespace DocumentVault;

/// The app's state: the engine (the vault, started as a hidden helper), who is signed in, and what the pages show.
public sealed class AppState
{
    public static AppState Current { get; } = new();

    /// The engine this app version needs (engine.json, written when the installer is built), or null when the app runs
    /// from a development checkout. It is downloaded only when this computer keeps the vault.
    public static readonly EngineRelease? Release = EngineInstaller.Load(Path.Combine(AppContext.BaseDirectory, "engine.json"));

    /// Where the downloaded engine lives (its own Python, code and Tesseract): %LOCALAPPDATA%\Document Vault\Engine,
    /// or null in development.
    public static readonly string? BundledEngine = Release is null ? null
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Document Vault", "Engine");

    /// The engine is here and is the one this app version needs (always, in development).
    public bool EngineReady => Release is null || EngineInstaller.IsInstalled(BundledEngine!, Release);

    /// Download and unpack the engine (%DOCUMENT_VAULT_ENGINE_ARCHIVE%: a local copy instead, for CI).
    public Task InstallEngineAsync(IProgress<EngineProgress> progress) =>
        EngineInstaller.InstallAsync(Release!, BundledEngine!, progress,
            archive: Environment.GetEnvironmentVariable("DOCUMENT_VAULT_ENGINE_ARCHIVE") is { Length: > 0 } a ? a : null);

    /// The engine's folder: the bundled one, else a development checkout (%DOCUMENT_VAULT_DIR%, the recorded one).
    public string Folder { get; } = BundledEngine ?? VaultFolder();

    /// The vault's own folder (its data\ holds the documents, the index and the logs), never inside the app's files,
    /// so updating or removing the app can't touch them: %LOCALAPPDATA%\Document Vault\Vault, or the checkout itself
    /// in development. The engine runs in it.
    public string VaultHome => BundledEngine is null ? Folder
        : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Document Vault", "Vault");

    public string LogFile => Path.Combine(VaultHome, "data", "logs", "app-engine.log");

    /// The engine's Python: the bundled one, else the checkout's venv; %DOCUMENT_VAULT_PYTHON% overrides it (CI).
    public string Python => Environment.GetEnvironmentVariable("DOCUMENT_VAULT_PYTHON") is { Length: > 0 } set ? set
        : BundledEngine is not null ? Path.Combine(BundledEngine, "python", "python.exe")
        : Path.Combine(Folder, "venv", "Scripts", "python.exe");

    public bool Installed => File.Exists(Python);

    public EnginePipe? Engine { get; private set; }
    public RemoteVault? Remote { get; private set; }
    public VaultClient? Client { get; private set; }

    /// How this computer uses the vault: "here" (it is the centre, the engine runs here) or "remote" (it connects to
    /// the vault on another computer). Null until the person chooses, on the first start.
    public string? Mode { get => Saved.Load("mode"); set => Saved.Save("mode", value); }
    public Connection? SavedConnection
    {
        get => Saved.LoadProtected("connection") is { } json ? System.Text.Json.JsonSerializer.Deserialize<Connection>(json) : null;
        set => Saved.SaveProtected("connection", value is null ? null : System.Text.Json.JsonSerializer.Serialize(value));
    }
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
        // The engine's code is read from the app's Engine\ folder; it runs in the vault's folder, so everything it keeps
        // (data\index, data\logs, backups) lands there.
        Directory.CreateDirectory(Path.Combine(VaultHome, "data", "logs"));
        var environment = BundledEngine is null ? null : new Dictionary<string, string> { ["PYTHONPATH"] = BundledEngine };
        Engine = new EnginePipe(Python, VaultHome, environment: environment, logFile: LogFile);
        await Engine.WaitUntilReadyAsync();
        Client = new VaultClient(Engine, new ProtectedSessionStore());
    }

    /// Use the vault on another computer: nothing runs here but the app.
    public void StartRemote(Connection connection)
    {
        Remote = new RemoteVault(connection);
        Client = new VaultClient(Remote, new MemorySessionStore());  // a paired device signs in with its key, not a cookie
    }

    public async Task StopAsync()
    {
        if (Engine is not null) await Engine.DisposeAsync();  // the engine (and llama.cpp with it) stops with the app
        Remote?.Dispose();
        Engine = null;
        Remote = null;
        Client = null;
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

/// Small things the app remembers, in %LOCALAPPDATA%\Document Vault: plain ones, and secret ones encrypted with DPAPI.
public static class Saved
{
    private static readonly string Folder = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Document Vault");
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("Document Vault settings");

    public static string? Load(string name) =>
        File.Exists(Path.Combine(Folder, name + ".txt")) ? File.ReadAllText(Path.Combine(Folder, name + ".txt")).Trim() : null;

    public static void Save(string name, string? value)
    {
        Directory.CreateDirectory(Folder);
        if (value is null) File.Delete(Path.Combine(Folder, name + ".txt"));
        else File.WriteAllText(Path.Combine(Folder, name + ".txt"), value);
    }

    public static string? LoadProtected(string name)
    {
        var file = Path.Combine(Folder, name + ".bin");
        try
        {
            return File.Exists(file)
                ? Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(file), Entropy, DataProtectionScope.CurrentUser))
                : null;
        }
        catch (CryptographicException) { return null; }
    }

    public static void SaveProtected(string name, string? value)
    {
        var file = Path.Combine(Folder, name + ".bin");
        if (value is null) { File.Delete(file); return; }
        Directory.CreateDirectory(Folder);
        File.WriteAllBytes(file, ProtectedData.Protect(Encoding.UTF8.GetBytes(value), Entropy, DataProtectionScope.CurrentUser));
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
