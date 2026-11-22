using System.Runtime.InteropServices;
using DocumentVault.Core;

namespace DocumentVault.Core.Tests;

public class SessionAndEvents
{
    [Fact]
    public void ReadsTheSessionCookieTheVaultSets()
    {
        Assert.Equal("abc123", VaultClient.SessionToken("vault_session=abc123; HttpOnly; Max-Age=2592000; Path=/; SameSite=strict"));
        Assert.Equal("", VaultClient.SessionToken("vault_session=\"\"; expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/"));
        Assert.Null(VaultClient.SessionToken("other=1"));
        Assert.Null(VaultClient.SessionToken(null));
    }

    [Fact]
    public void ParsesEveryKindOfChatEvent()
    {
        var sources = Assert.IsType<ChatEvent.Sources>(ChatEvent.Parse(
            """{"type":"sources","sources":[{"rank":1,"score":0.9,"chunk_id":"c1","doc_id":"d1","filename":"Bike.pdf","category":"insurance","page":1,"text":"Expires 14 March 2027"}]}"""));
        Assert.Equal("Bike.pdf", sources.Items[0].Filename);
        Assert.Equal(Category.Insurance, sources.Items[0].Category);
        Assert.Equal(new ChatEvent.Token("Your bike "), ChatEvent.Parse("""{"type":"token","text":"Your bike "}"""));
        var done = Assert.IsType<ChatEvent.Done>(ChatEvent.Parse("""{"type":"done","citations":[1],"found":true,"llm":"llama.cpp/llama3.1:8b"}"""));
        Assert.Equal([1], done.Citations);
        Assert.Equal(new ChatEvent.Error("The assistant is offline.", "Restart."),
                     ChatEvent.Parse("""{"type":"error","message":"The assistant is offline.","detail":"Restart."}"""));
    }
}

public class TheRealEngine
{
    /// The project folder: up from the test's own folder until backend/ is there.
    private static string Repo()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "backend"))) dir = dir.Parent;
        return dir?.FullName ?? throw new InvalidOperationException("project folder not found");
    }

    /// $VAULT_TEST_PYTHON (CI), else the project's venv.
    private static string? Python(string repo)
    {
        var set = Environment.GetEnvironmentVariable("VAULT_TEST_PYTHON");
        if (!string.IsNullOrEmpty(set)) return set;
        var venv = RuntimeInformation.IsOSPlatform(OSPlatform.Windows)
            ? Path.Combine(repo, "venv", "Scripts", "python.exe") : Path.Combine(repo, "venv", "bin", "python");
        return File.Exists(venv) ? venv : null;
    }

    [Fact]
    public async Task TheAppTalksToTheRealEngineOverThePipe()
    {
        var repo = Repo();
        var python = Python(repo);
        if (python is null) return;  // no Python with the vault's packages here: nothing to run against
        var tmp = Directory.CreateTempSubdirectory("vault-core-").FullName;
        try
        {
            await using var engine = new EnginePipe(
                python, repo,
                ["--index-dir", Path.Combine(tmp, "index"), "--database-url", "sqlite:///" + Path.Combine(tmp, "vault.db").Replace('\\', '/'),
                 "--no-llm", "--no-phones", "--owner", "abhinav"],
                new Dictionary<string, string>
                {
                    ["VAULT_KEYCHAIN"] = "off", ["VAULT_AI_AUTO_DOWNLOAD"] = "0", ["VAULT_KEY_DIR"] = Path.Combine(tmp, "keys"),
                    ["VAULT_LLM_DIR"] = Path.Combine(tmp, "llm"), ["VAULT_FACTS_READER"] = "rules",
                },
                Path.Combine(tmp, "engine.log"));
            await engine.WaitUntilReadyAsync().WaitAsync(TimeSpan.FromMinutes(5));

            var session = new MemorySessionStore();
            var vault = new VaultClient(engine, session);
            Assert.True((await vault.AuthStatusAsync()).SetupNeeded);
            var me = await vault.SetUpAsync("abhinav", "a long test password");
            Assert.True(me.IsAdmin);
            Assert.NotNull(session.Load());

            var file = Path.Combine(tmp, "Bike Insurance 2026.txt");
            await File.WriteAllTextAsync(file, "Two-wheeler policy TW-778812. Period: 15 March 2026 to 14 March 2027.");
            var added = await vault.UploadAsync([file]);
            Assert.Equal("added", added[0].Status);
            var docs = await vault.DocumentsAsync();
            Assert.Equal(["Bike Insurance 2026.txt"], docs.Select(d => d.Filename));
            Assert.Equal(await File.ReadAllBytesAsync(file), await vault.OriginalAsync(docs[0].DocId));
            Assert.Equal(1, (await vault.InfoAsync()).NumDocuments);

            // started with --no-llm: the chat says so instead of answering
            await Assert.ThrowsAsync<VaultException>(async () => { await foreach (var _ in vault.ChatAsync("When does it expire?")) { } });

            await vault.SignOutAsync();
            Assert.Null(session.Load());
            var signedOut = await Assert.ThrowsAsync<VaultException>(() => vault.DocumentsAsync());
            Assert.Equal(VaultErrorKind.SignedOut, signedOut.Kind);
        }
        finally
        {
            try { Directory.Delete(tmp, recursive: true); } catch { /* files still closing */ }
        }
    }
}
