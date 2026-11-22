using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace DocumentVault.Core;

/// Where the app keeps the sign-in (the vault's session cookie), like a browser keeps its cookies.
public interface ISessionStore
{
    string? Load();
    void Save(string? token);
}

public sealed class MemorySessionStore(string? token = null) : ISessionStore
{
    private string? _token = token;
    public string? Load() => _token;
    public void Save(string? token) => _token = token;
}

/// The vault's actions for the native app, over any transport. Mirrors the web app's api (frontend/lib/api.ts).
public sealed class VaultClient(IVaultTransport transport, ISessionStore session)
{
    public const string CookieName = "vault_session";
    public IVaultTransport Transport { get; } = transport;

    // ---- sign-in

    public Task<AuthStatus> AuthStatusAsync() => GetAsync<AuthStatus>("/api/auth/status");

    /// First start: choose the admin's name and password.
    public async Task<User> SetUpAsync(string name, string password) =>
        (await PostAsync<SignedIn>("/api/auth/setup", new JsonObject { ["name"] = name, ["password"] = password })).User;

    public async Task<User> SignInAsync(string name, string password) =>
        (await PostAsync<SignedIn>("/api/auth/login", new JsonObject { ["name"] = name, ["password"] = password })).User;

    public async Task SignOutAsync()
    {
        try { await SendAsync(new VaultRequest("POST", "/api/auth/logout")); } catch (VaultException) { /* signed out anyway */ }
        session.Save(null);
    }

    // ---- the vault

    public Task<Info> InfoAsync() => GetAsync<Info>("/api/info");
    public Task<List<VaultDocument>> DocumentsAsync() => GetAsync<List<VaultDocument>>("/api/documents");
    public Task<List<Upcoming>> UpcomingAsync(int days = 90) => GetAsync<List<Upcoming>>($"/api/upcoming?days={days}");
    public Task<Settings> SettingsAsync() => GetAsync<Settings>("/api/settings");

    /// Add files to the vault: their type is detected unless `category` is given. A locked PDF needs its `password`.
    public async Task<List<UploadResult>> UploadAsync(IReadOnlyList<string> files, Category? category = null, string? password = null)
    {
        var form = new Dictionary<string, string> { ["category"] = category?.Wire() ?? "auto" };
        if (password is not null) form["password"] = password;
        return Decode<List<UploadResult>>(await SendAsync(new VaultRequest("POST", "/api/documents") { Files = files, Form = form }));
    }

    public async Task DeleteAsync(VaultDocument document) =>
        await SendAsync(new VaultRequest("DELETE", $"/api/documents/{document.DocId}"));

    public async Task<VaultDocument> SetCategoryAsync(VaultDocument document, Category category) =>
        Decode<VaultDocument>(await SendAsync(new VaultRequest("PATCH", $"/api/documents/{document.DocId}")
            { Json = new JsonObject { ["category"] = category.Wire() } }));

    /// The original file, decrypted in memory (never written to disk by the vault).
    public async Task<byte[]> OriginalAsync(string docId) =>
        (await SendAsync(new VaultRequest("GET", $"/api/documents/{docId}/file"))).Body;

    /// Ask a question: the sources arrive first, then the answer word by word, then the citations.
    public async IAsyncEnumerable<ChatEvent> ChatAsync(string question, IReadOnlyList<ChatTurn>? history = null,
                                                       Category? category = null,
                                                       [EnumeratorCancellation] CancellationToken cancel = default)
    {
        var body = new JsonObject
        {
            ["question"] = question,
            ["history"] = new JsonArray((history ?? []).Select(t => (JsonNode?)new JsonObject { ["role"] = t.Role, ["content"] = t.Content }).ToArray()),
        };
        if (category is { } c) body["category"] = c.Wire();
        var request = new VaultRequest("POST", "/api/chat") { Json = body, Headers = CookieHeader() };
        IAsyncEnumerator<string> lines = Transport.LinesAsync(request, cancel).GetAsyncEnumerator(cancel);
        try
        {
            while (true)
            {
                try
                {
                    if (!await lines.MoveNextAsync()) yield break;
                }
                catch (VaultException e) when (e.Kind == VaultErrorKind.SignedOut)
                {
                    session.Save(null);
                    throw;
                }
                yield return ChatEvent.Parse(lines.Current);
            }
        }
        finally { await lines.DisposeAsync(); }
    }

    // ---- other devices

    /// A one-time code for another computer (`typed`: short, to type) or a phone (for a QR code), for 10 minutes.
    public async Task<Pairing> StartPairingAsync(bool typed) =>
        Decode<Pairing>(await SendAsync(new VaultRequest("POST", $"/api/mobile/pairing?typed={(typed ? "true" : "false")}")));

    // ---- plumbing

    private sealed record SignedIn(User User);

    private Dictionary<string, string> CookieHeader() =>
        session.Load() is { } token ? new() { ["cookie"] = $"{CookieName}={token}" } : [];

    private async Task<VaultResponse> SendAsync(VaultRequest request)
    {
        foreach (var (key, value) in CookieHeader()) request.Headers[key] = value;
        var response = await Transport.SendAsync(request);
        if (SessionToken(response.Headers.GetValueOrDefault("set-cookie")) is { } token)
            session.Save(token.Length == 0 ? null : token);  // signed in (a new token) or out (an emptied cookie)
        if (response.Status is < 200 or >= 300)
        {
            if (response.Status == 401) session.Save(null);
            throw VaultException.FromAnswer(response.Status, response.Body);
        }
        return response;
    }

    /// The session token from a Set-Cookie header ("" when the vault cleared it).
    public static string? SessionToken(string? header)
    {
        if (header is null) return null;
        var at = header.IndexOf(CookieName + "=", StringComparison.Ordinal);
        if (at < 0) return null;
        var value = new string(header[(at + CookieName.Length + 1)..].TakeWhile(c => c != ';' && c != ',').ToArray());
        return value == "\"\"" ? "" : value;
    }

    private static T Decode<T>(VaultResponse response) =>
        JsonSerializer.Deserialize<T>(response.Body, VaultJson.Options) ?? throw new VaultException(VaultErrorKind.Http, "The vault's answer was empty.");

    private async Task<T> GetAsync<T>(string path) => Decode<T>(await SendAsync(new VaultRequest("GET", path)));

    private async Task<T> PostAsync<T>(string path, JsonObject body) =>
        Decode<T>(await SendAsync(new VaultRequest("POST", path) { Json = body }));
}
