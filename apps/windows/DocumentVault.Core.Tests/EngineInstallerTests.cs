using System.IO.Compression;
using System.Security.Cryptography;

namespace DocumentVault.Core.Tests;

public class SettingUpTheEngine
{
    private static (string zip, string sha, long size) MakeEngine(string dir, string content)
    {
        var source = Path.Combine(dir, "source");
        Directory.CreateDirectory(Path.Combine(source, "python"));
        File.WriteAllText(Path.Combine(source, "python", "python.exe"), content);
        Directory.CreateDirectory(Path.Combine(source, "backend"));
        File.WriteAllText(Path.Combine(source, "backend", "engine.py"), "print('hello')");
        var zip = Path.Combine(dir, $"engine-{content}.zip");
        ZipFile.CreateFromDirectory(source, zip);
        Directory.Delete(source, recursive: true);
        return (zip, Convert.ToHexStringLower(SHA256.HashData(File.ReadAllBytes(zip))), new FileInfo(zip).Length);
    }

    [Fact]
    public async Task DownloadsChecksAndUnpacksItOnce()
    {
        var dir = Directory.CreateTempSubdirectory("engine-").FullName;
        var (zip, sha, size) = MakeEngine(dir, "v1");
        var release = new EngineRelease("1.0", "https://example.invalid/engine.zip", sha, size);
        var folder = Path.Combine(dir, "Engine");
        Assert.False(EngineInstaller.IsInstalled(folder, release));

        var steps = new List<EngineProgress>();
        await EngineInstaller.InstallAsync(release, folder, new SyncProgress(steps), archive: zip);
        Assert.True(EngineInstaller.IsInstalled(folder, release));
        Assert.Equal("print('hello')", File.ReadAllText(Path.Combine(folder, "backend", "engine.py")));
        Assert.Contains(steps, s => s.Step == "downloading" && s.Done == size);
        Assert.Contains(steps, s => s.Step == "unpacking" && s.Done == s.Total);
        Assert.False(File.Exists(Path.Combine(dir, "engine-download.zip")));  // the download is cleaned up

        // a newer app version needs a newer engine: replaced whole
        var (zip2, sha2, size2) = MakeEngine(dir, "v2");
        var newer = new EngineRelease("1.1", release.Url, sha2, size2);
        Assert.False(EngineInstaller.IsInstalled(folder, newer));
        await EngineInstaller.InstallAsync(newer, folder, archive: zip2);
        Assert.True(EngineInstaller.IsInstalled(folder, newer));
        Assert.Equal("v2", File.ReadAllText(Path.Combine(folder, "python", "python.exe")));
        Directory.Delete(dir, recursive: true);
    }

    [Fact]
    public async Task RefusesAnEngineThatDoesNotMatch()
    {
        var dir = Directory.CreateTempSubdirectory("engine-").FullName;
        var (zip, _, size) = MakeEngine(dir, "v1");
        var release = new EngineRelease("1.0", "https://example.invalid/engine.zip", new string('0', 64), size);
        var folder = Path.Combine(dir, "Engine");
        var error = await Assert.ThrowsAsync<VaultException>(() => EngineInstaller.InstallAsync(release, folder, archive: zip));
        Assert.Contains("doesn't match", error.Message);
        Assert.False(Directory.Exists(folder));
        Directory.Delete(dir, recursive: true);
    }

    [Fact]
    public void ReadsTheReleaseTheAppWasBuiltWith()
    {
        var file = Path.GetTempFileName();
        File.WriteAllText(file, """{"version":"0.9.1","url":"https://github.com/x/releases/download/v0.9.1/e.zip","sha256":"ab","size":12}""");
        Assert.Equal(new EngineRelease("0.9.1", "https://github.com/x/releases/download/v0.9.1/e.zip", "ab", 12), EngineInstaller.Load(file));
        Assert.Null(EngineInstaller.Load(file + ".missing"));
        File.Delete(file);
    }

    private sealed class SyncProgress(List<EngineProgress> into) : IProgress<EngineProgress>
    {
        public void Report(EngineProgress value) { lock (into) into.Add(value); }
    }
}
