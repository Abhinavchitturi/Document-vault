using System.Collections.Concurrent;
using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;
using System.Threading.Channels;

namespace DocumentVault.Core;

/// The vault's engine (`python -m backend.engine`) as a hidden helper of the app, reached over a private pipe: one
/// JSON object per line on its stdin and stdout (backend/engine/bridge.py). Nothing listens on the network. The
/// engine stops when the app closes its end of the pipe.
public sealed class EnginePipe : IVaultTransport, IAsyncDisposable
{
    private readonly Process _process;
    private readonly StreamWriter _stdin;
    private readonly SemaphoreSlim _writeLock = new(1, 1);
    private readonly ConcurrentDictionary<int, TaskCompletionSource<VaultResponse>> _waiting = new();
    private readonly ConcurrentDictionary<int, Channel<string>> _streams = new();
    private readonly TaskCompletionSource _ready = new(TaskCreationOptions.RunContinuationsAsynchronously);
    private readonly StreamWriter? _log;
    private int _nextId;
    private string? _failure;

    /// Start the engine: `python` is the vault's own Python (its venv), `folder` the vault's folder.
    public EnginePipe(string python, string folder, IEnumerable<string>? arguments = null,
                      IDictionary<string, string>? environment = null, string? logFile = null)
    {
        var start = new ProcessStartInfo(python)
        {
            WorkingDirectory = folder,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,  // no console window behind the app
            StandardInputEncoding = new UTF8Encoding(false),  // no byte-order mark in front of the first request
            StandardOutputEncoding = new UTF8Encoding(false),
            StandardErrorEncoding = new UTF8Encoding(false),
        };
        start.ArgumentList.Add("-m");
        start.ArgumentList.Add("backend.engine");
        foreach (var argument in arguments ?? []) start.ArgumentList.Add(argument);
        start.Environment["PYTHONUNBUFFERED"] = "1";
        start.Environment["PYTHONUTF8"] = "1";
        if (!start.Environment.ContainsKey("VAULT_DATABASE_URL") || string.IsNullOrEmpty(start.Environment["VAULT_DATABASE_URL"]))
            start.Environment["VAULT_DATABASE_URL"] = "sqlite:///data/index/vault.db";
        foreach (var (key, value) in environment ?? new Dictionary<string, string>()) start.Environment[key] = value;

        if (logFile is not null)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(logFile))!);
            _log = new StreamWriter(logFile, append: true, new UTF8Encoding(false)) { AutoFlush = true };
        }
        _process = new Process { StartInfo = start, EnableRaisingEvents = true };
        _process.ErrorDataReceived += (_, e) => { if (e.Data is not null) lock (_process) _log?.WriteLine(e.Data); };
        _process.Exited += (_, _) => Ended($"The vault's engine stopped (code {SafeExitCode()}). See its log for why.");
        _process.Start();
        _process.BeginErrorReadLine();
        _stdin = _process.StandardInput;
        _ = Task.Run(ReadLoop);
    }

    /// Wait until the vault is open (the engine says "ready"), or throw why it couldn't open.
    public Task WaitUntilReadyAsync(CancellationToken cancel = default) => _ready.Task.WaitAsync(cancel);

    public async ValueTask DisposeAsync()
    {
        try { _stdin.Close(); } catch { /* already gone */ }  // the engine finishes what it is doing and exits
        try { await _process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(15)); }
        catch { try { _process.Kill(entireProcessTree: true); } catch { /* gone */ } }
        _log?.Dispose();
    }

    public async Task<VaultResponse> SendAsync(VaultRequest request, CancellationToken cancel = default)
    {
        if (_failure is not null) throw VaultException.Engine(_failure);
        var id = Interlocked.Increment(ref _nextId);
        var answer = new TaskCompletionSource<VaultResponse>(TaskCreationOptions.RunContinuationsAsynchronously);
        _waiting[id] = answer;
        await WriteAsync(request, id, cancel);
        return await answer.Task.WaitAsync(cancel);
    }

    public async IAsyncEnumerable<string> LinesAsync(VaultRequest request, [EnumeratorCancellation] CancellationToken cancel = default)
    {
        if (_failure is not null) throw VaultException.Engine(_failure);
        var id = Interlocked.Increment(ref _nextId);
        var lines = Channel.CreateUnbounded<string>();
        _streams[id] = lines;
        await WriteAsync(request, id, cancel);
        await foreach (var line in lines.Reader.ReadAllAsync(cancel)) yield return line;
    }

    private async Task WriteAsync(VaultRequest request, int id, CancellationToken cancel)
    {
        var message = new JsonObject
        {
            ["id"] = id,
            ["method"] = request.Method,
            ["path"] = request.Path,
            ["headers"] = new JsonObject(request.Headers.Select(h => KeyValuePair.Create(h.Key, (JsonNode?)h.Value))),
        };
        if (request.Json is not null) message["json"] = request.Json.DeepClone();
        if (request.Files.Count > 0)
        {
            message["files"] = new JsonArray(request.Files.Select(f => (JsonNode?)new JsonObject { ["field"] = "files", ["path"] = f }).ToArray());
            message["form"] = new JsonObject(request.Form.Select(f => KeyValuePair.Create(f.Key, (JsonNode?)f.Value)));
        }
        var line = message.ToJsonString();
        await _writeLock.WaitAsync(cancel);  // one whole line at a time
        try
        {
            await _stdin.WriteLineAsync(line.AsMemory(), cancel);
            await _stdin.FlushAsync(cancel);
        }
        finally { _writeLock.Release(); }
    }

    private async Task ReadLoop()
    {
        try
        {
            while (await _process.StandardOutput.ReadLineAsync() is { } line)
            {
                if (line.Length == 0) continue;
                try { Handle(JsonNode.Parse(line)!.AsObject()); }
                catch (JsonException) { /* not a message */ }
            }
        }
        catch (Exception e) { Ended($"Lost the vault's engine: {e.Message}"); }
        Ended("The vault's engine stopped.");
    }

    private void Handle(JsonObject message)
    {
        if (message["event"]?.GetValue<string>() is { } evt)
        {
            if (evt == "ready") _ready.TrySetResult();
            if (evt == "failed") Ended(message["message"]?.GetValue<string>() ?? "The vault couldn't open.");
            return;
        }
        if (message["id"] is not JsonValue idValue) return;
        var id = idValue.GetValue<int>();
        var status = message["status"]?.GetValue<int>() ?? 0;

        if (_streams.TryGetValue(id, out var stream))
        {
            if (message["chunk"]?.GetValue<string>() is { } chunk) stream.Writer.TryWrite(chunk);
            else if (message.ContainsKey("end")) { stream.Writer.TryComplete(); _streams.TryRemove(id, out _); }
            else if (!message.ContainsKey("stream") && status != 0)  // refused before streaming: an error answer
            {
                stream.Writer.TryComplete(VaultException.FromAnswer(status, Body(message)));
                _streams.TryRemove(id, out _);
            }
            return;
        }
        if (_waiting.TryRemove(id, out var waiter))
        {
            var headers = message["headers"]?.AsObject().ToDictionary(h => h.Key, h => h.Value?.GetValue<string>() ?? "") ?? [];
            waiter.TrySetResult(new VaultResponse(status, headers, Body(message)));
        }
    }

    private static byte[] Body(JsonObject message)
    {
        if (message["base64"]?.GetValue<string>() is { } encoded) return Convert.FromBase64String(encoded);
        if (message.ContainsKey("json")) return Encoding.UTF8.GetBytes(message["json"]?.ToJsonString() ?? "null");
        return [];
    }

    private void Ended(string why)
    {
        _failure ??= why;
        var error = VaultException.Engine(_failure);
        _ready.TrySetException(error);
        foreach (var id in _waiting.Keys) if (_waiting.TryRemove(id, out var w)) w.TrySetException(error);
        foreach (var id in _streams.Keys) if (_streams.TryRemove(id, out var s)) s.Writer.TryComplete(error);
    }

    private string SafeExitCode()
    {
        try { return _process.ExitCode.ToString(); } catch { return "?"; }
    }
}
