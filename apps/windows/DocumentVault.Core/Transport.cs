using System.Text.Json.Nodes;

namespace DocumentVault.Core;

/// One request to the vault, in the vault's own terms (an API path), whatever carries it.
public sealed record VaultRequest(string Method, string Path)
{
    public JsonNode? Json { get; init; }
    public IReadOnlyList<string> Files { get; init; } = [];  // an upload, by path: the engine reads the files
    public IReadOnlyDictionary<string, string> Form { get; init; } = new Dictionary<string, string>();
    public Dictionary<string, string> Headers { get; init; } = [];
}

public sealed record VaultResponse(int Status, IReadOnlyDictionary<string, string> Headers, byte[] Body);

public enum VaultErrorKind { SignedOut, Http, Engine }

public sealed class VaultException(VaultErrorKind kind, string message, int status = 0) : Exception(message)
{
    public VaultErrorKind Kind { get; } = kind;
    public int Status { get; } = status;

    public static VaultException SignedOut() => new(VaultErrorKind.SignedOut, "You're signed out. Sign in again.", 401);
    public static VaultException Engine(string message) => new(VaultErrorKind.Engine, message);

    public static VaultException FromAnswer(int status, byte[] body)
    {
        if (status == 401) return SignedOut();
        string? detail = null;
        try { detail = JsonNode.Parse(body)?["detail"]?.GetValue<string>(); } catch { /* not JSON */ }
        return new(VaultErrorKind.Http, detail ?? $"The vault answered with an error ({status}).", status);
    }
}

/// What carries requests to the vault: the private pipe to the engine (EnginePipe).
public interface IVaultTransport
{
    Task<VaultResponse> SendAsync(VaultRequest request, CancellationToken cancel = default);
    /// A streamed answer, one line at a time (the chat). Throws VaultException for an error answer.
    IAsyncEnumerable<string> LinesAsync(VaultRequest request, CancellationToken cancel = default);
}
