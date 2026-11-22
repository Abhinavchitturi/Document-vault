using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace DocumentVault.Core;

/// A newer version of the app on the releases page: its installer (or DMG), and the SHA-256 GitHub publishes for it.
public sealed record AppUpdate(string Version, string Url, string Sha256, long Size, string Notes);

/// Checks the project's releases for a newer app, downloads it and checks it against GitHub's SHA-256 (over HTTPS from
/// GitHub's API). The documents and the engine are untouched; a new app that needs a new engine downloads it itself.
public static class Updates
{
    public const string Releases = "https://api.github.com/repos/Abhinavchitturi/Document-vault/releases?per_page=20";

    /// Newer than `current`, from GitHub's releases list (drafts left out), with a file named `asset`.
    public static AppUpdate? Newest(string releasesJson, string current, string asset)
    {
        AppUpdate? best = null;
        foreach (var release in JsonNode.Parse(releasesJson)?.AsArray() ?? [])
        {
            if (release?["draft"]?.GetValue<bool>() == true) continue;
            var version = release?["tag_name"]?.GetValue<string>()?.TrimStart('v');
            if (version is null || Compare(version, current) <= 0 || (best is not null && Compare(version, best.Version) <= 0)) continue;
            var file = release!["assets"]?.AsArray().FirstOrDefault(a => a?["name"]?.GetValue<string>() == asset);
            var digest = file?["digest"]?.GetValue<string>();
            if (file is null || digest is null || !digest.StartsWith("sha256:")) continue;  // nothing to check it against
            best = new AppUpdate(version, file["browser_download_url"]!.GetValue<string>(), digest["sha256:".Length..],
                                 file["size"]?.GetValue<long>() ?? 0, release["body"]?.GetValue<string>() ?? "");
        }
        return best;
    }

    /// 0.9.1 > 0.9.0 > 0.9.0-beta: numbers first; a version without a suffix is newer than the same with one.
    public static int Compare(string a, string b)
    {
        static (int[] numbers, string suffix) Parse(string v)
        {
            var dash = v.IndexOf('-');
            var core = dash < 0 ? v : v[..dash];
            var numbers = core.Split('.').Select(p => int.TryParse(p, out var n) ? n : 0).ToArray();
            return (numbers, dash < 0 ? "" : v[(dash + 1)..]);
        }
        var (x, xs) = Parse(a);
        var (y, ys) = Parse(b);
        for (var i = 0; i < Math.Max(x.Length, y.Length); i++)
        {
            var c = (i < x.Length ? x[i] : 0).CompareTo(i < y.Length ? y[i] : 0);
            if (c != 0) return c;
        }
        if (xs == ys) return 0;
        if (xs == "") return 1;
        if (ys == "") return -1;
        return string.CompareOrdinal(xs, ys);
    }

    /// Ask GitHub (or %DOCUMENT_VAULT_UPDATES_URL%, for tests) whether there is a newer version. Null when there isn't,
    /// or when GitHub can't be reached (checked again later).
    public static async Task<AppUpdate?> CheckAsync(string current, string asset, HttpClient? http = null, CancellationToken cancel = default)
    {
        var url = Environment.GetEnvironmentVariable("DOCUMENT_VAULT_UPDATES_URL") is { Length: > 0 } set ? set : Releases;
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, url);
            request.Headers.UserAgent.ParseAdd("DocumentVault-Updates");
            request.Headers.Accept.ParseAdd("application/vnd.github+json");
            http ??= new HttpClient { Timeout = TimeSpan.FromSeconds(20) };
            using var answer = await http.SendAsync(request, cancel);
            if (!answer.IsSuccessStatusCode) return null;
            return Newest(await answer.Content.ReadAsStringAsync(cancel), current, asset);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException) { return null; }
    }

    /// Download the update to `to` with progress, and check it against GitHub's SHA-256.
    public static async Task DownloadAsync(AppUpdate update, string to, IProgress<(long done, long total)>? progress = null,
                                           HttpClient? http = null, CancellationToken cancel = default)
    {
        http ??= new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
        using var answer = await http.GetAsync(update.Url, HttpCompletionOption.ResponseHeadersRead, cancel);
        answer.EnsureSuccessStatusCode();
        var total = answer.Content.Headers.ContentLength ?? update.Size;
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        await using (var source = await answer.Content.ReadAsStreamAsync(cancel))
        await using (var output = File.Create(to))
        {
            var buffer = new byte[1 << 20];
            long done = 0;
            int read;
            while ((read = await source.ReadAsync(buffer, cancel)) > 0)
            {
                await output.WriteAsync(buffer.AsMemory(0, read), cancel);
                hash.AppendData(buffer, 0, read);
                done += read;
                progress?.Report((done, total));
            }
        }
        if (!string.Equals(Convert.ToHexStringLower(hash.GetHashAndReset()), update.Sha256, StringComparison.OrdinalIgnoreCase))
        {
            File.Delete(to);
            throw new VaultException(VaultErrorKind.Engine, "The downloaded update doesn't match the one published on GitHub, so it wasn't installed.");
        }
    }
}
