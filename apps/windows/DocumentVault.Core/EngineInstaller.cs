using System.IO.Compression;
using System.Security.Cryptography;
using System.Text.Json;

namespace DocumentVault.Core;

/// The engine this app version needs (engine.json next to the app, written when the installer is built): where to
/// download it and its SHA-256. A computer that only connects to another computer's vault never downloads it.
public sealed record EngineRelease(string Version, string Url, string Sha256, long Size);

/// How far setting up the engine is: "downloading" or "unpacking", Done of Total bytes.
public sealed record EngineProgress(string Step, long Done, long Total);

/// Downloads the vault's engine (a private Python with the vault's packages, its code, Tesseract), checks it against
/// the SHA-256 the app was built with, and unpacks it. Only when this computer keeps the vault (it is the centre).
public static class EngineInstaller
{
    private const string Marker = ".engine-sha256";

    public static EngineRelease? Load(string file)
    {
        if (!File.Exists(file)) return null;
        return JsonSerializer.Deserialize<EngineRelease>(File.ReadAllText(file), VaultJson.Options);
    }

    /// The engine in `folder` is complete and is the one this app version needs.
    public static bool IsInstalled(string folder, EngineRelease release) =>
        File.Exists(Path.Combine(folder, "python", "python.exe")) || File.Exists(Path.Combine(folder, "python", "bin", "python3"))
            ? File.Exists(Path.Combine(folder, Marker)) && File.ReadAllText(Path.Combine(folder, Marker)).Trim() == release.Sha256
            : false;

    /// Download (or, for tests and CI, copy from `archive`), check, unpack into `folder`, replacing an older engine.
    /// The documents are never in this folder, so replacing it touches nothing of the person's.
    public static async Task InstallAsync(EngineRelease release, string folder, IProgress<EngineProgress>? progress = null,
                                          string? archive = null, HttpClient? http = null, CancellationToken cancel = default)
    {
        var parent = Path.GetDirectoryName(Path.GetFullPath(folder))!;
        Directory.CreateDirectory(parent);
        var download = Path.Combine(parent, "engine-download.zip");
        var unpacked = folder + ".new";
        try
        {
            var sha = await DownloadAsync(release, archive, download, progress, http, cancel);
            if (!string.Equals(sha, release.Sha256, StringComparison.OrdinalIgnoreCase))
                throw new VaultException(VaultErrorKind.Engine,
                    "The downloaded engine doesn't match this version of the app, so it wasn't used. Try again, or install the latest Document Vault from github.com/Abhinavchitturi/Document-vault/releases.");
            if (Directory.Exists(unpacked)) Directory.Delete(unpacked, recursive: true);
            await Task.Run(() => Unpack(download, unpacked, progress, cancel), cancel);
            File.WriteAllText(Path.Combine(unpacked, Marker), release.Sha256);
            if (Directory.Exists(folder)) Directory.Delete(folder, recursive: true);
            Directory.Move(unpacked, folder);
        }
        finally
        {
            try { File.Delete(download); } catch { /* in use: removed next time */ }
            try { if (Directory.Exists(unpacked)) Directory.Delete(unpacked, recursive: true); } catch { /* next time */ }
        }
    }

    private static async Task<string> DownloadAsync(EngineRelease release, string? archive, string to,
                                                    IProgress<EngineProgress>? progress, HttpClient? http, CancellationToken cancel)
    {
        Stream source;
        long total = release.Size;
        HttpResponseMessage? response = null;
        if (archive is not null)
        {
            source = File.OpenRead(archive);
        }
        else
        {
            http ??= new HttpClient { Timeout = Timeout.InfiniteTimeSpan };
            try
            {
                response = await http.GetAsync(release.Url, HttpCompletionOption.ResponseHeadersRead, cancel);
                response.EnsureSuccessStatusCode();
            }
            catch (HttpRequestException e)
            {
                throw new VaultException(VaultErrorKind.Engine, $"Couldn't download the vault's engine ({e.Message}). Check the internet connection and try again.");
            }
            total = response.Content.Headers.ContentLength ?? total;
            source = await response.Content.ReadAsStreamAsync(cancel);
        }
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        try
        {
            await using var output = File.Create(to);
            var buffer = new byte[1 << 20];
            long done = 0, shown = 0;
            int read;
            while ((read = await source.ReadAsync(buffer, cancel)) > 0)
            {
                await output.WriteAsync(buffer.AsMemory(0, read), cancel);
                hash.AppendData(buffer, 0, read);
                done += read;
                if (done - shown >= 4 << 20 || done == total) { progress?.Report(new("downloading", done, total)); shown = done; }
            }
        }
        catch (IOException e) when (archive is null)
        {
            throw new VaultException(VaultErrorKind.Engine, $"The download of the vault's engine stopped ({e.Message}). Try again.");
        }
        finally
        {
            await source.DisposeAsync();
            response?.Dispose();
        }
        return Convert.ToHexStringLower(hash.GetHashAndReset());
    }

    private static void Unpack(string zip, string to, IProgress<EngineProgress>? progress, CancellationToken cancel)
    {
        using var archive = ZipFile.OpenRead(zip);
        var root = Path.GetFullPath(to) + Path.DirectorySeparatorChar;
        long total = archive.Entries.Sum(e => e.Length), done = 0, shown = 0;
        foreach (var entry in archive.Entries)
        {
            cancel.ThrowIfCancellationRequested();
            var target = Path.GetFullPath(Path.Combine(to, entry.FullName));
            if (!target.StartsWith(root, StringComparison.OrdinalIgnoreCase)) continue;  // never outside the folder
            if (entry.FullName.EndsWith('/') || entry.FullName.EndsWith('\\')) { Directory.CreateDirectory(target); continue; }
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            entry.ExtractToFile(target, overwrite: true);
            done += entry.Length;
            if (done - shown >= 8 << 20 || done == total) { progress?.Report(new("unpacking", done, total)); shown = done; }
        }
        progress?.Report(new("unpacking", total, total));
    }
}
