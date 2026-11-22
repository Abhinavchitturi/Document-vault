using System.Net;
using System.Security.Cryptography;
using System.Text;

namespace DocumentVault.Core.Tests;

public class GettingUpdates
{
    private static string Releases(params (string tag, bool draft, string? digest)[] releases) =>
        "[" + string.Join(",", releases.Select(r => $$"""
            {"tag_name":"{{r.tag}}","draft":{{(r.draft ? "true" : "false")}},"body":"notes {{r.tag}}","assets":[
              {"name":"DocumentVault-Setup.exe","size":10,"browser_download_url":"https://example.invalid/{{r.tag}}/DocumentVault-Setup.exe"
               {{(r.digest is null ? "" : $",\"digest\":\"sha256:{r.digest}\"")}}},
              {"name":"DocumentVault-macOS.dmg","size":5,"browser_download_url":"https://example.invalid/{{r.tag}}/DocumentVault-macOS.dmg","digest":"sha256:dd"}]}
            """)) + "]";

    [Fact]
    public void ComparesVersionsLikePeopleDo()
    {
        Assert.True(Updates.Compare("0.9.1", "0.9.0-beta") > 0);
        Assert.True(Updates.Compare("0.9.0", "0.9.0-beta") > 0);
        Assert.True(Updates.Compare("0.10.0", "0.9.9") > 0);
        Assert.True(Updates.Compare("1.0", "0.99.0") > 0);
        Assert.Equal(0, Updates.Compare("0.9.1", "0.9.1"));
        Assert.True(Updates.Compare("0.9.0-beta", "0.9.1") < 0);
    }

    [Fact]
    public void PicksTheNewestReleaseWithACheckableInstaller()
    {
        var json = Releases(("v0.9.0-beta", false, "aa"), ("v0.9.1", false, "bb"), ("v0.9.3", true, "cc"), ("v0.9.2", false, null));
        var update = Updates.Newest(json, "0.9.0-beta", "DocumentVault-Setup.exe")!;
        Assert.Equal("0.9.1", update.Version);  // 0.9.3 is a draft, 0.9.2 has nothing to check it against
        Assert.Equal("bb", update.Sha256);
        Assert.Equal("https://example.invalid/v0.9.1/DocumentVault-Setup.exe", update.Url);
        Assert.Null(Updates.Newest(json, "0.9.1", "DocumentVault-Setup.exe"));  // already the newest
        Assert.Equal("0.9.2", Updates.Newest(json, "0.9.0-beta", "DocumentVault-macOS.dmg")!.Version);  // its DMG can be checked
    }

    [Fact]
    public async Task DownloadsAndChecksTheUpdate()
    {
        var bytes = Encoding.UTF8.GetBytes("the new installer");
        var good = new AppUpdate("0.9.1", "https://example.invalid/setup.exe", Convert.ToHexStringLower(SHA256.HashData(bytes)), bytes.Length, "");
        var http = new HttpClient(new Serve(bytes));
        var file = Path.GetTempFileName();
        var seen = new List<(long, long)>();
        await Updates.DownloadAsync(good, file, new SyncProgress(seen), http);
        Assert.Equal(bytes, await File.ReadAllBytesAsync(file));
        Assert.Contains(seen, s => s.Item1 == bytes.Length);

        var bad = good with { Sha256 = new string('0', 64) };
        var error = await Assert.ThrowsAsync<VaultException>(() => Updates.DownloadAsync(bad, file, null, http));
        Assert.Contains("doesn't match", error.Message);
        Assert.False(File.Exists(file));  // a file that doesn't match is never left to run
    }

    private sealed class Serve(byte[] bytes) : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancel) =>
            Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK) { Content = new ByteArrayContent(bytes) });
    }

    private sealed class SyncProgress(List<(long, long)> into) : IProgress<(long done, long total)>
    {
        public void Report((long done, long total) value) { lock (into) into.Add(value); }
    }
}
