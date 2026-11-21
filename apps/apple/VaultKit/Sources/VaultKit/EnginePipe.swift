import Foundation

/// The vault's engine (`python -m backend.engine`) as a hidden helper of the Mac app, reached over a private pipe:
/// one JSON object per line on its stdin and stdout (backend/engine/bridge.py). Nothing listens on the network.
/// The engine stops when the app quits, because its end of the pipe closes.
public final class EnginePipe: VaultTransport, @unchecked Sendable {
    public enum State: Equatable, Sendable { case starting, ready, failed(String) }

    private let process = Process()
    private let input = Pipe()
    private let output = Pipe()
    private let lock = NSLock()
    private var nextID = 1
    private var buffer = Data()
    private var waiting: [Int: CheckedContinuation<VaultResponse, Error>] = [:]
    private var streams: [Int: AsyncThrowingStream<String, Error>.Continuation] = [:]
    private var readiness: [CheckedContinuation<Void, Error>] = []
    private(set) public var state: State = .starting

    /// Start the engine: `python` is the vault's own Python (its venv), `folder` the vault's folder.
    public init(python: URL, folder: URL, arguments: [String] = [], environment: [String: String] = [:], log: URL? = nil) throws {
        process.executableURL = python
        process.arguments = ["-m", "backend.engine"] + arguments
        process.currentDirectoryURL = folder
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"
        env["VAULT_DATABASE_URL"] = env["VAULT_DATABASE_URL"] ?? "sqlite:///data/index/vault.db"
        environment.forEach { env[$0.key] = $0.value }
        process.environment = env
        process.standardInput = input
        process.standardOutput = output
        if let log {
            FileManager.default.createFile(atPath: log.path, contents: nil)
            let handle = try FileHandle(forWritingTo: log)
            handle.seekToEndOfFile()
            process.standardError = handle
        } else {
            process.standardError = FileHandle.nullDevice
        }
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            if data.isEmpty { self?.ended("The vault's engine stopped.") } else { self?.received(data) }
        }
        process.terminationHandler = { [weak self] process in
            self?.ended("The vault's engine stopped (code \(process.terminationStatus)). See its log for why.")
        }
        try process.run()
    }

    deinit { stop() }

    /// Wait until the vault is open (the engine says "ready"), or throw why it couldn't open.
    public func waitUntilReady() async throws {
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
            lock.lock()
            switch state {
            case .ready: lock.unlock(); c.resume()
            case .failed(let why): lock.unlock(); c.resume(throwing: VaultError.engine(why))
            case .starting: readiness.append(c); lock.unlock()
            }
        }
    }

    public func stop() {
        try? input.fileHandleForWriting.close()  // the engine finishes what it is doing and exits
    }

    // MARK: - VaultTransport

    public func send(_ request: VaultRequest) async throws -> VaultResponse {
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<VaultResponse, Error>) in
            lock.lock()
            if case .failed(let why) = state { lock.unlock(); c.resume(throwing: VaultError.engine(why)); return }
            let id = nextID
            nextID += 1
            waiting[id] = c
            lock.unlock()
            write(request, id: id)
        }
    }

    public func lines(_ request: VaultRequest) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            lock.lock()
            if case .failed(let why) = state {
                lock.unlock()
                continuation.finish(throwing: VaultError.engine(why))
                return
            }
            let id = nextID
            nextID += 1
            streams[id] = continuation
            lock.unlock()
            write(request, id: id)
        }
    }

    // MARK: - The pipe

    private func write(_ request: VaultRequest, id: Int) {
        var message: [String: Any] = ["id": id, "method": request.method, "path": request.path, "headers": request.headers]
        if let json = request.json, let object = try? JSONSerialization.jsonObject(with: json, options: [.fragmentsAllowed]) {
            message["json"] = object
        }
        if !request.files.isEmpty {
            message["files"] = request.files.map { ["field": "files", "path": $0.path] }
            message["form"] = request.form
        }
        guard var line = try? JSONSerialization.data(withJSONObject: message) else { return }
        line.append(0x0A)
        lock.lock()  // one whole line at a time
        defer { lock.unlock() }
        try? input.fileHandleForWriting.write(contentsOf: line)
    }

    private func received(_ data: Data) {
        lock.lock()
        buffer.append(data)
        var lines: [Data] = []
        while let newline = buffer.firstIndex(of: 0x0A) {
            lines.append(buffer[buffer.startIndex..<newline])
            buffer = Data(buffer[buffer.index(after: newline)...])
        }
        lock.unlock()
        for line in lines where !line.isEmpty {
            if let message = (try? JSONSerialization.jsonObject(with: line)) as? [String: Any] { handle(message) }
        }
    }

    private func handle(_ message: [String: Any]) {
        if let event = message["event"] as? String {
            if event == "ready" { settle(.ready) }
            if event == "failed" { settle(.failed(message["message"] as? String ?? "The vault couldn't open.")) }
            return
        }
        guard let id = message["id"] as? Int else { return }
        lock.lock()
        let stream = streams[id]
        let waiter = waiting[id]
        if message["end"] != nil || message["status"] != nil && message["stream"] == nil { streams[id] = nil }
        if message["status"] != nil { waiting[id] = nil }
        lock.unlock()

        let status = message["status"] as? Int ?? 0
        let headers = message["headers"] as? [String: String] ?? [:]
        if let stream {
            if let chunk = message["chunk"] as? String {
                stream.yield(chunk)
            } else if message["end"] != nil {
                stream.finish()
            } else if message["stream"] == nil, status != 0 {  // refused before streaming: an error answer
                stream.finish(throwing: Self.error(status: status, body: Self.body(of: message)))
            }
            return
        }
        waiter?.resume(returning: VaultResponse(status: status, headers: headers, body: Self.body(of: message)))
    }

    private static func body(of message: [String: Any]) -> Data {
        if let encoded = message["base64"] as? String { return Data(base64Encoded: encoded) ?? Data() }
        if let json = message["json"] {
            return (try? JSONSerialization.data(withJSONObject: json, options: [.fragmentsAllowed])) ?? Data()
        }
        return Data()
    }

    static func error(status: Int, body: Data) -> VaultError {
        if status == 401 { return .signedOut }
        let detail = ((try? JSONSerialization.jsonObject(with: body)) as? [String: Any])?["detail"] as? String
        return .http(status: status, detail: detail ?? "The vault answered with an error (\(status)).")
    }

    private func settle(_ new: State) {
        lock.lock()
        guard state == .starting else { lock.unlock(); return }
        state = new
        let waiters = readiness
        readiness = []
        lock.unlock()
        for waiter in waiters {
            if case .failed(let why) = new { waiter.resume(throwing: VaultError.engine(why)) } else { waiter.resume() }
        }
    }

    private func ended(_ why: String) {
        output.fileHandleForReading.readabilityHandler = nil
        settle(.failed(why))
        lock.lock()
        if case .ready = state { state = .failed(why) }
        let waiters = waiting
        let open = streams
        waiting = [:]
        streams = [:]
        lock.unlock()
        waiters.values.forEach { $0.resume(throwing: VaultError.engine(why)) }
        open.values.forEach { $0.finish(throwing: VaultError.engine(why)) }
    }
}
