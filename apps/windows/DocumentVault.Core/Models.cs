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
public sealed record LlmStatus(bool Ready, string Message);
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
