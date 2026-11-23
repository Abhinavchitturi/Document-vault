using System.Text.Json;
using System.Text.Json.Serialization;

namespace DocumentVault.Core;

// The vault's data, as the engine sends it (backend/api/app.py; the web app's frontend/lib/api.ts has the same shapes).

// lower case on the wire ("insurance"): VaultJson.Options converts it
public enum Category { Insurance, Warranty, Bill, Property, Identity, Other }

public static class Categories
{
    public static readonly Category[] All = Enum.GetValues<Category>();

    public static string Label(this Category category) => category switch
    {
        Category.Insurance => "Insurance",
        Category.Warranty => "Warranties",
        Category.Bill => "Bills",
        Category.Property => "Property",
        Category.Identity => "Identity",
        _ => "Other",
    };

    public static string Wire(this Category category) => category.ToString().ToLowerInvariant();
}

public sealed record User(int Id, string Name, bool IsAdmin);
public sealed record AuthStatus(User? User, bool SetupNeeded, string? SuggestedName);
public sealed record LlmStatus(bool Ready, string Message, bool CanDownload = false);

/// How far the one-click download of the local AI is: idle, downloading (step, done, total), done or failed.
public sealed record AIDownloadStatus(string State, string? Step, long Done, long Total, string? Error);
/// 1.0: several computers keep one vault. A code for another computer to join this vault.
public sealed record SyncInvite(string Code, int ExpiresIn, List<string> Urls, string Fingerprint, string Name);
/// Another computer that keeps this vault: when it was last caught up with, or why not.
public sealed record SyncPeer(string CentreId, string Name, List<string> Urls, string Fingerprint, DateTimeOffset? SyncedAt, string? Problem);
/// Copying a joined vault: copying (Done of Total documents), done or failed.
public sealed record SyncJoining(string State, int Done, int Total, string? Error, string? With);
public sealed record SyncStatus(string CentreId, string Name, List<SyncPeer> Peers, SyncJoining? Joining, bool CanJoin);
public sealed record SyncProbe(string Fingerprint, string Address);
public sealed record SyncJoiningAnswer(SyncJoining? Joining);

/// The search model: downloading (Done of Total bytes, the first start only), loading, ready or failed.
public sealed record SearchStatus(string State, long Done, long Total, string? Error);
public sealed record Person(int Id, string Name);

public sealed record Info(string Owner, bool IsAdmin, string? Llm, LlmStatus LlmStatus, int NumDocuments, int NumChunks,
                          Dictionary<string, int> Categories, bool OcrAvailable, List<string> SupportedTypes, int MaxUploadMb);

public sealed record VaultDocument(
    string DocId, string Filename, Category Category, string Owner = "", bool Mine = true, bool Private = false,
    List<Person>? SharedWith = null, int Pages = 0, int OcrPages = 0, int? OcrConfidence = null, bool NeedsReview = false,
    string? ExpiresOn = null, string? DueOn = null, string? Amount = null, string? Reference = null, string? Issuer = null,
    int SizeBytes = 0, string AddedAt = "");

public sealed record Upcoming(string DocId, string Filename, Category Category, string Kind, string Date, int DaysLeft,
                              string? Amount, string? Issuer);

public sealed record Source(int Rank, double Score, string ChunkId, string DocId, string Filename, Category Category, int Page, string Text);

public sealed record UploadResult(string Filename, string Status, VaultDocument? Document, string? Error, string? Code);

/// What the vault takes: PDFs, Word (.docx) files, photos and plain text. Archives (zip, 7z, rar…) and other files are
/// turned away before they're sent; a file with no extension (as chat apps send them) is sent, and the vault checks it.
public static class AcceptedFiles
{
    public static readonly string[] Extensions =
        [".pdf", ".docx", ".txt", ".md", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".heic", ".heif"];
    private static readonly HashSet<string> Archives = new(StringComparer.OrdinalIgnoreCase)
        { ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst", ".cab", ".iso" };

    public static bool Accepts(string path)
    {
        var ext = Path.GetExtension(path);
        return ext.Length == 0 || Extensions.Contains(ext, StringComparer.OrdinalIgnoreCase);
    }

    public static string Reason(string path) => Path.GetExtension(path) switch
    {
        var e when Archives.Contains(e) =>
            "Zip and other archive files can't be added. Unzip it first, then add the PDFs, Word files or photos inside.",
        var e when e.Equals(".doc", StringComparison.OrdinalIgnoreCase) =>
            "This is an old Word (.doc) file. Open it and save it as PDF or .docx, then add that.",
        _ => "Only PDFs, Word (.docx) files, photos and text files can be added. Save or print it as PDF, then add that.",
    };

    /// Files to send (a folder's documents, inside it too) and the ones turned away, each with why.
    public static (List<string> Send, List<UploadResult> Refused) Sort(IEnumerable<string> paths)
    {
        var send = new List<string>();
        var refused = new List<UploadResult>();
        foreach (var path in paths)
        {
            if (Directory.Exists(path))
                send.AddRange(Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories)
                    .Where(f => Extensions.Contains(Path.GetExtension(f), StringComparer.OrdinalIgnoreCase)));
            else if (Accepts(path)) send.Add(path);
            else refused.Add(new UploadResult(Path.GetFileName(path), "error", null, Reason(path), null));
        }
        return (send, refused);
    }
}

public sealed record ChatTurn(string Role, string Content);

/// What the centre computer shows to connect another device: a one-time code, its addresses, its certificate.
public sealed record Pairing(string Code, int ExpiresIn, string Url, List<string> Urls, string Fingerprint);

public sealed record Settings(string Owner, bool IsAdmin, string? LlmModel, List<string> LlmModels, LlmStatus LlmStatus,
                              string EmbeddingModel, string Database, string IndexDir, bool OcrAvailable, string SearchMethod,
                              string FactsReader, string KeyKeptIn, bool FamilyMode);

/// One line of a streamed answer: the sources first, then the answer word by word, then which sources it cited.
public abstract record ChatEvent
{
    public sealed record Sources(List<Source> Items) : ChatEvent;
    public sealed record Token(string Text) : ChatEvent;
    public sealed record Done(int[] Citations, bool Found, string Llm) : ChatEvent;
    public sealed record Error(string Message, string? Detail) : ChatEvent;

    public static ChatEvent Parse(string line)
    {
        using var doc = JsonDocument.Parse(line);
        var e = doc.RootElement;
        string? Str(string name) => e.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        return Str("type") switch
        {
            "sources" => new Sources(e.GetProperty("sources").Deserialize<List<Source>>(VaultJson.Options) ?? []),
            "token" => new Token(Str("text") ?? ""),
            "done" => new Done(e.GetProperty("citations").Deserialize<int[]>() ?? [],
                               e.TryGetProperty("found", out var f) && f.GetBoolean(), Str("llm") ?? ""),
            _ => new Error(Str("message") ?? "Something went wrong.", Str("detail")),
        };
    }
}

/// The vault's JSON uses snake_case.
public static class VaultJson
{
    public static readonly JsonSerializerOptions Options = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower) },
        PropertyNameCaseInsensitive = true,
    };
}
