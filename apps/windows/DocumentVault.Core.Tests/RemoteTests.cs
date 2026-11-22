using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using DocumentVault.Core;

namespace DocumentVault.Core.Tests;

public class ConnectingToTheCentreComputer
{
    [Fact]
    public void TypedAddressesBecomeTheVaultsAddress()
    {
        Assert.Equal("https://192.168.1.20:8443", RemoteVault.Address("192.168.1.20"));
        Assert.Equal("https://my-mac.tail1234.ts.net:8443", RemoteVault.Address(" my-mac.tail1234.ts.net "));
        Assert.Equal("https://10.0.0.5:9000", RemoteVault.Address("https://10.0.0.5:9000/"));
        Assert.Equal("AB12 CD34 EF56 7890", RemoteVault.SecurityCode("ab12cd34ef567890aaaabbbbccccdddd"));
    }

    private static string Repo()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "backend"))) dir = dir.Parent;
        return dir!.FullName;
    }

    private static string? Python(string repo)
    {
        var set = Environment.GetEnvironmentVariable("VAULT_TEST_PYTHON");
        if (!string.IsNullOrEmpty(set)) return set;
        var venv = RuntimeInformation.IsOSPlatform(OSPlatform.Windows)
            ? Path.Combine(repo, "venv", "Scripts", "python.exe") : Path.Combine(repo, "venv", "bin", "python");
        return File.Exists(venv) ? venv : null;
    }

    [Fact]
    public async Task ALaptopPairsWithTheCentreAndUsesItsVault()
    {
        var repo = Repo();
        var python = Python(repo);
        if (python is null) return;
        var tmp = Directory.CreateTempSubdirectory("vault-centre-").FullName;
        try
        {
            // the centre computer: its own vault, with device access on a test port
            await using var centre = new EnginePipe(python, repo,
                ["--index-dir", Path.Combine(tmp, "index"), "--database-url", "sqlite:///" + Path.Combine(tmp, "vault.db").Replace('\\', '/'),
                 "--no-llm", "--phone-port", "8463", "--phone-address", "127.0.0.1", "--owner", "abhinav"],
                new Dictionary<string, string>
                {
                    ["VAULT_KEYCHAIN"] = "off", ["VAULT_KEY_DIR"] = Path.Combine(tmp, "keys"),
                    ["VAULT_LLM_DIR"] = Path.Combine(tmp, "llm"), ["VAULT_FACTS_READER"] = "rules",
                },
                Path.Combine(tmp, "centre.log"));
            await centre.WaitUntilReadyAsync().WaitAsync(TimeSpan.FromMinutes(5));
            var host = new VaultClient(centre, new MemorySessionStore());
            await host.SetUpAsync("abhinav", "a long test password");
            var shown = await host.StartPairingAsync(typed: true);  // the centre's "connect another computer" screen

            // the other laptop: reach it, check the security code, pair with the typed code
            await Task.Delay(1500);  // the HTTPS listener starts just after the vault opens
            var seen = await RemoteVault.ProbeAsync("127.0.0.1:8463");
            Assert.Equal(shown.Fingerprint, seen);
            Assert.Matches("^[A-Z2-9]{4}-[A-Z2-9]{4}$", shown.Code);
            var connection = await RemoteVault.PairAsync("127.0.0.1:8463", shown.Code.ToLowerInvariant(), seen, "Test laptop");
            Assert.Equal("https://127.0.0.1:8463", connection.Urls[0]);

            using var remote = new RemoteVault(connection);
            var laptop = new VaultClient(remote, new MemorySessionStore());
            Assert.Equal("abhinav", (await laptop.AuthStatusAsync()).User?.Name);
            var file = Path.Combine(tmp, "Bike Insurance 2026.txt");
            await File.WriteAllTextAsync(file, "Two-wheeler policy TW-778812. Period: 15 March 2026 to 14 March 2027.");
            Assert.Equal("added", (await laptop.UploadAsync([file]))[0].Status);  // sent over the network as a real upload
            var docs = await laptop.DocumentsAsync();
            Assert.Equal(["Bike Insurance 2026.txt"], docs.Select(d => d.Filename));
            Assert.Equal(await File.ReadAllBytesAsync(file), await laptop.OriginalAsync(docs[0].DocId));
            Assert.Single(await host.DocumentsAsync());  // the same vault: the centre sees it too

            // a wrong certificate is refused, whatever the key
            using var impostor = new RemoteVault(connection with { Fingerprint = new string('0', 64) });
            var refused = await Assert.ThrowsAsync<VaultException>(() => new VaultClient(impostor, new MemorySessionStore()).InfoAsync());
            Assert.Equal(VaultErrorKind.Engine, refused.Kind);
        }
        finally
        {
            try { Directory.Delete(tmp, recursive: true); } catch { /* files still closing */ }
        }
    }
}
