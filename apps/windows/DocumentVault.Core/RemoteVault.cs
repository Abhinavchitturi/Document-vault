using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Net.Security;
using System.Runtime.CompilerServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace DocumentVault.Core;

/// The vault on another computer (the centre), reached over the network (home Wi-Fi, or Tailscale from anywhere)
/// the way a paired phone reaches it: HTTPS pinned to that vault's own certificate, and this device's access key.
public sealed record Connection(List<string> Urls, string Fingerprint, string Token, string Name, List<Centre>? Others = null);

/// 1.0: another computer that keeps the same vault, and its certificate: used when the paired one is off.
public sealed record Centre(string Name, List<string> Urls, string Fingerprint);

public sealed class RemoteVault : IVaultTransport, IDisposable
{
    public const int Port = 8443;
    private readonly HttpClient _http;
    private Connection _connection;
    private string? _preferred;  // the address that answered last (maybe another computer that keeps the vault)

    public RemoteVault(Connection connection)
    {
        _connection = connection;
        _http = Pinned(Trusted);  // the paired computer, and the others that keep the same vault
    }

    private bool Trusted(string fingerprint) =>
        fingerprint == _connection.Fingerprint || (_connection.Others ?? []).Any(c => c.Fingerprint == fingerprint);

    /// The paired computer's addresses first, then the other computers that keep the vault; the one that answered
    /// last before all of them.
    private List<string> Candidates()
    {
        var all = _connection.Urls.Concat((_connection.Others ?? []).SelectMany(c => c.Urls)).Distinct().ToList();
        if (_preferred is not null && all.Remove(_preferred)) all.Insert(0, _preferred);
        return all;
    }

    /// Learn which computers keep this vault now (the paired one tells), to use another when it is off. Returns the
    /// updated connection, to save.
    public async Task<Connection> RefreshCentresAsync(CancellationToken cancel = default)
    {
        var answer = JsonNode.Parse((await SendAsync(new VaultRequest("GET", "/api/mobile/addresses"), cancel)).Body);
        var centres = answer?["centres"]?.AsArray().Select(c => new Centre(
            c?["name"]?.GetValue<string>() ?? "", c?["urls"]?.AsArray().Select(u => u!.GetValue<string>()).ToList() ?? [],
            c?["fingerprint"]?.GetValue<string>() ?? "")).Where(c => c.Fingerprint.Length == 64).ToList();
        if (centres is null) return _connection;  // a vault from before 1.0
        _connection = _connection with { Others = centres.Where(c => c.Fingerprint != _connection.Fingerprint).ToList() };
        return _connection;
    }

    public Connection Connection => _connection;

    public void Dispose() => _http.Dispose();

    /// "192.168.1.20", "my-mac.tailnet.ts.net" or a full "https://…:8443" → the vault's address.
    public static string Address(string typed)
    {
        var text = typed.Trim().TrimEnd('/');
        if (!text.Contains("://")) text = "https://" + text;
        var uri = new UriBuilder(text) { Scheme = "https" };
        if (uri.Port is -1 or 443 && !text.Contains(":443")) uri.Port = Port;
        return uri.Uri.GetLeftPart(UriPartial.Authority);
    }

    /// Step 1 of pairing: reach the address and read its certificate, without trusting it yet. Returns the
    /// fingerprint, so the person can check it matches the security code on the centre computer's screen.
    public static async Task<string> ProbeAsync(string typedAddress, CancellationToken cancel = default)
    {
        string? seen = null;
        using var handler = new SocketsHttpHandler
        {
            SslOptions = new SslClientAuthenticationOptions
            {
                RemoteCertificateValidationCallback = (_, cert, _, _) =>
                {
                    if (cert is not null) seen = Convert.ToHexStringLower(SHA256.HashData(cert.GetRawCertData()));
                    return cert is not null;
                },
            },
            ConnectTimeout = TimeSpan.FromSeconds(8),
        };
        using var http = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(12) };
        try
        {
            var hello = await http.GetFromJsonAsync<JsonObject>(Address(typedAddress) + "/api/mobile/hello", cancel);
            if (hello?["app"]?.GetValue<string>() != "document-vault")
                throw new VaultException(VaultErrorKind.Http, "That address answered, but it isn't a Document Vault.");
        }
        catch (HttpRequestException e)
        {
            throw VaultException.Engine($"Couldn't reach a vault at {Address(typedAddress)}. Is Document Vault open on that " +
                                        $"computer, and are both on the same Wi-Fi (or Tailscale)? ({e.Message})");
        }
        return seen ?? throw VaultException.Engine("The vault didn't show a certificate.");
    }

    /// The short security code people compare on both screens: the first 16 characters of the fingerprint.
    public static string SecurityCode(string fingerprint) =>
        string.Join(" ", Enumerable.Range(0, 4).Select(i => fingerprint.Substring(i * 4, 4).ToUpperInvariant()));

    /// Step 2: pair with the typed code, trusting only the certificate the person confirmed.
    public static async Task<Connection> PairAsync(string typedAddress, string code, string fingerprint, string deviceName,
                                                   CancellationToken cancel = default)
    {
        var address = Address(typedAddress);
        using var http = Pinned(fingerprint);
        var answer = await http.PostAsJsonAsync(address + "/api/mobile/pair", new { code, device_name = deviceName }, cancel);
        var body = await answer.Content.ReadAsByteArrayAsync(cancel);
        if (!answer.IsSuccessStatusCode) throw VaultException.FromAnswer((int)answer.StatusCode == 401 ? 400 : (int)answer.StatusCode, body);
        var token = JsonNode.Parse(body)!["token"]!.GetValue<string>();
        var connection = new Connection([address], fingerprint, token, address);
        using var remote = new RemoteVault(connection);
        var urls = JsonNode.Parse((await remote.SendAsync(new VaultRequest("GET", "/api/mobile/addresses"), cancel)).Body)?["urls"]
            ?.AsArray().Select(u => u!.GetValue<string>()).ToList() ?? [];
        return connection with { Urls = [address, .. urls.Where(u => u != address)] };  // Wi-Fi first, Tailscale too
    }

    public async Task<VaultResponse> SendAsync(VaultRequest request, CancellationToken cancel = default)
    {
        Exception? last = null;
        // uploads may wait while the centre reads big scans (or, just after it opened, loads its search model)
        var limit = request.Files is { Count: > 0 } ? TimeSpan.FromMinutes(30) : TimeSpan.FromMinutes(5);
        foreach (var url in Candidates())  // the address that answers: home Wi-Fi, Tailscale, another computer that keeps the vault
        {
            using var timer = CancellationTokenSource.CreateLinkedTokenSource(cancel);
            timer.CancelAfter(limit);
            try
            {
                using var message = Build(url, request);
                using var answer = await _http.SendAsync(message, timer.Token);
                var headers = answer.Headers.Concat(answer.Content.Headers)
                    .ToDictionary(h => h.Key.ToLowerInvariant(), h => string.Join(", ", h.Value));
                var body = await answer.Content.ReadAsByteArrayAsync(timer.Token);
                _preferred = url;
                return new VaultResponse((int)answer.StatusCode, headers, body);
            }
            catch (HttpRequestException e) { last = e; }
            catch (UriFormatException e) { last = e; }  // an address that can't be used: try the next one
            catch (OperationCanceledException e) when (!cancel.IsCancellationRequested &&
                                                       (!timer.IsCancellationRequested || request.Method == "GET"))
            {
                last = e;  // no connection in time (that computer is off), or only reading: the next address may answer
            }
            catch (OperationCanceledException) when (!cancel.IsCancellationRequested)
            {
                // it reached the centre but no answer came in time: never send it again to another address (an
                // upload would be added twice)
                throw VaultException.Engine($"The vault on {_connection.Name} didn't answer in time. It may still be busy; " +
                                            "check in a minute whether it was done.");
            }
        }
        throw VaultException.Engine($"Can't reach the vault on {_connection.Name}. Is that computer on, with Document Vault " +
                                    $"open, and on the same Wi-Fi or Tailscale? ({last?.Message})");
    }

    public async IAsyncEnumerable<string> LinesAsync(VaultRequest request, [EnumeratorCancellation] CancellationToken cancel = default)
    {
        using var message = Build(Candidates()[0], request);
        HttpResponseMessage answer;
        try { answer = await _http.SendAsync(message, HttpCompletionOption.ResponseHeadersRead, cancel); }
        catch (HttpRequestException e) { throw VaultException.Engine($"Can't reach the vault on {_connection.Name}: {e.Message}"); }
        using (answer)
        {
            if (!answer.IsSuccessStatusCode)
                throw VaultException.FromAnswer((int)answer.StatusCode, await answer.Content.ReadAsByteArrayAsync(cancel));
            using var reader = new StreamReader(await answer.Content.ReadAsStreamAsync(cancel), Encoding.UTF8);
            while (await reader.ReadLineAsync(cancel) is { } line)
                if (line.Length > 0) yield return line;
        }
    }

    private HttpRequestMessage Build(string url, VaultRequest request)
    {
        var message = new HttpRequestMessage(new HttpMethod(request.Method), url + request.Path);
        foreach (var (key, value) in request.Headers)
            if (!key.Equals("cookie", StringComparison.OrdinalIgnoreCase)) message.Headers.TryAddWithoutValidation(key, value);
        message.Headers.Authorization = new AuthenticationHeaderValue("Bearer", _connection.Token);
        if (request.Files.Count > 0)
        {
            var form = new MultipartFormDataContent();
            foreach (var (key, value) in request.Form) form.Add(new StringContent(value), key);
            foreach (var path in request.Files) form.Add(new ByteArrayContent(File.ReadAllBytes(path)), "files", Path.GetFileName(path));
            message.Content = form;
        }
        else if (request.Json is not null)
        {
            message.Content = new StringContent(request.Json.ToJsonString(), Encoding.UTF8, "application/json");
        }
        return message;
    }

    /// An HTTP client that trusts exactly one certificate: the vault's own (no certificate authority at all).
    private static HttpClient Pinned(string fingerprint) => Pinned(seen => seen == fingerprint);

    /// An HTTP client that trusts only the certificates `trusted` accepts.
    private static HttpClient Pinned(Func<string, bool> trusted)
    {
        var handler = new SocketsHttpHandler
        {
            SslOptions = new SslClientAuthenticationOptions
            {
                RemoteCertificateValidationCallback = (_, cert, _, _) =>
                    cert is not null && trusted(Convert.ToHexStringLower(SHA256.HashData(cert.GetRawCertData()))),
            },
            ConnectTimeout = TimeSpan.FromSeconds(6),
        };
        return new HttpClient(handler) { Timeout = Timeout.InfiniteTimeSpan };  // each request has its own limit
    }
}
