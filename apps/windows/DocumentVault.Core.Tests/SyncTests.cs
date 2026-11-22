using System.Runtime.InteropServices;

namespace DocumentVault.Core.Tests;

/// 1.0: two computers keep one vault. A second one joins with a code and copies it; a laptop paired with the first
/// keeps working through the second when the first is off.
public class TwoComputersKeepOneVault
{
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

    private static EnginePipe Engine(string python, string repo, string folder, int port) => new(python, repo,
        ["--index-dir", Path.Combine(folder, "index"), "--database-url", "sqlite:///" + Path.Combine(folder, "vault.db").Replace('\\', '/'),
         "--no-llm", "--phone-port", port.ToString(), "--phone-address", "127.0.0.1"],
        new Dictionary<string, string>
        {
            ["VAULT_KEYCHAIN"] = "off", ["VAULT_AI_AUTO_DOWNLOAD"] = "0", ["VAULT_KEY_DIR"] = Path.Combine(folder, "keys"),
            ["VAULT_LLM_DIR"] = Path.Combine(folder, "llm"), ["VAULT_FACTS_READER"] = "rules",
        },
        Path.Combine(folder, "engine.log"));

    private static async Task<T> Eventually<T>(Func<Task<T>> attempt, Func<T, bool> done, int seconds = 60)
    {
        var deadline = DateTime.UtcNow.AddSeconds(seconds);
        while (true)
        {
            try
            {
                var value = await attempt();
                if (done(value) || DateTime.UtcNow > deadline) return value;
            }
            catch when (DateTime.UtcNow < deadline) { /* not yet */ }
            await Task.Delay(1000);
        }
    }

    [Fact]
    public async Task ASecondComputerJoinsAndALaptopUsesItWhenTheFirstIsOff()
    {
        var repo = Repo();
        var python = Python(repo);
        if (python is null) return;
        var tmp = Directory.CreateTempSubdirectory("vault-two-").FullName;
        EnginePipe? first = null;
        try
        {
            // the first computer keeps the vault, with a document in it
            first = Engine(python, repo, Path.Combine(tmp, "a"), 8465);
            await first.WaitUntilReadyAsync().WaitAsync(TimeSpan.FromMinutes(5));
            var a = new VaultClient(first, new MemorySessionStore());
            await a.SetUpAsync("abhinav", "a long test password");
            var file = Path.Combine(tmp, "Bike Insurance 2026.txt");
            await File.WriteAllTextAsync(file, "Two-wheeler policy TW-778812. Period: 15 March 2026 to 14 March 2027.");
            Assert.Equal("added", (await a.UploadAsync([file]))[0].Status);
            await Task.Delay(1500);  // the HTTPS listener starts just after the vault opens
            var invite = await a.SyncInviteAsync();

            // a second computer joins it with the code (after checking the security code) and copies it
            await using var second = Engine(python, repo, Path.Combine(tmp, "b"), 8466);
            await second.WaitUntilReadyAsync().WaitAsync(TimeSpan.FromMinutes(5));
            var b = new VaultClient(second, new MemorySessionStore());
            await Task.Delay(1500);
            var probe = await b.SyncProbeAsync("127.0.0.1:8465");
            Assert.Equal(invite.Fingerprint, probe.Fingerprint);
            await b.JoinVaultAsync(probe.Address, invite.Code, probe.Fingerprint);
            var copied = await Eventually(b.SyncJoiningAsync, j => j?.State != "copying");
            Assert.Equal("done", copied?.State);
            await b.SignInAsync("abhinav", "a long test password");  // the same name and password
            Assert.Equal(["Bike Insurance 2026.txt"], (await b.DocumentsAsync()).Select(d => d.Filename));

            // a laptop pairs with the first computer, and learns about the second
            var pairing = await a.StartPairingAsync(typed: true);
            var connection = await RemoteVault.PairAsync("127.0.0.1:8465", pairing.Code, invite.Fingerprint, "Test laptop");
            using var remote = new RemoteVault(connection);
            connection = await remote.RefreshCentresAsync();
            Assert.Single(connection.Others!);

            // the second computer catches up (every 10 s): it now knows the laptop too
            var paired = DateTimeOffset.UtcNow;
            await Eventually(b.SyncStatusAsync, s => s.Peers.Single().SyncedAt > paired.AddSeconds(1), 40);

            // the first computer is switched off: the laptop carries on with the second
            await first.DisposeAsync();
            first = null;
            using var later = new RemoteVault(connection);
            var laptop = new VaultClient(later, new MemorySessionStore());
            var docs = await Eventually(laptop.DocumentsAsync, d => d.Count == 1, 45);
            Assert.Equal(["Bike Insurance 2026.txt"], docs.Select(d => d.Filename));
        }
        catch (Exception e)
        {
            var logs = new[] { "a", "b" }.Select(n => Path.Combine(tmp, n, "engine.log")).Where(File.Exists)
                .Select(p => $"--- {p}\n" + string.Join("\n", File.ReadLines(p).TakeLast(40)));
            throw new Exception($"{e.Message}\n{string.Join("\n", logs)}", e);
        }
        finally
        {
            if (first is not null) await first.DisposeAsync();
            try { Directory.Delete(tmp, recursive: true); } catch { /* files still closing */ }
        }
    }
}
