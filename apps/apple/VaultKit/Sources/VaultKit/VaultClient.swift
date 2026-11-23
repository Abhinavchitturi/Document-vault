import Foundation

/// The vault's actions for the native apps, over any transport. Mirrors the web app's api (frontend/lib/api.ts).
public final class VaultClient: Sendable {
    public let transport: VaultTransport
    let session: SessionStore
    static let cookieName = "vault_session"

    public init(transport: VaultTransport, session: SessionStore = FileSessionStore()) {
        self.transport = transport
        self.session = session
    }

    // MARK: - Sign-in

    public func authStatus() async throws -> AuthStatus { try await get("/api/auth/status") }

    /// First start: choose the admin's name and password.
    public func setUp(name: String, password: String) async throws -> User {
        try await post("/api/auth/setup", ["name": name, "password": password], as: SignedIn.self).user
    }

    public func signIn(name: String, password: String) async throws -> User {
        try await post("/api/auth/login", ["name": name, "password": password], as: SignedIn.self).user
    }

    public func signOut() async {
        _ = try? await send(VaultRequest("POST", "/api/auth/logout"))
        session.save(nil)
    }

    // MARK: - The vault

    public func info() async throws -> Info { try await get("/api/info") }
    public func documents() async throws -> [VaultDocument] { try await get("/api/documents") }
    public func upcoming(days: Int = 90) async throws -> [Upcoming] { try await get("/api/upcoming?days=\(days)") }
    public func settings() async throws -> Settings { try await get("/api/settings") }

    public func reminders() async throws -> [Reminder] {
        try await get("/api/reminders", as: RemindersAnswer.self).items
    }

    /// Add files to the vault: their type is detected unless `category` is given. A locked PDF needs its `password`.
    public func upload(_ files: [URL], category: Category? = nil, password: String? = nil) async throws -> [UploadResult] {
        var form = ["category": category?.rawValue ?? "auto"]
        if let password { form["password"] = password }
        return try decode(try await send(VaultRequest("POST", "/api/documents", files: files, form: form)))
    }

    public func delete(_ document: VaultDocument) async throws {
        _ = try await send(VaultRequest("DELETE", "/api/documents/\(document.docId)"))
    }

    public func setCategory(_ document: VaultDocument, to category: Category) async throws -> VaultDocument {
        try await patch("/api/documents/\(document.docId)", ["category": category.rawValue])
    }

    /// The original file, decrypted in memory (never written to disk by the vault).
    public func original(_ docId: String) async throws -> Data {
        try await send(VaultRequest("GET", "/api/documents/\(docId)/file")).body
    }

    /// One page as an image (scans and photos come straightened).
    public func pageImage(_ docId: String, page: Int) async throws -> Data {
        try await send(VaultRequest("GET", "/api/documents/\(docId)/pages/\(page)/image")).body
    }

    /// Ask a question: the sources arrive first, then the answer word by word, then the citations.
    public func chat(_ question: String, history: [ChatTurn] = [], category: Category? = nil) -> AsyncThrowingStream<ChatEvent, Error> {
        var body: [String: Any] = ["question": question,
                                   "history": history.map { ["role": $0.role, "content": $0.content] }]
        if let category { body["category"] = category.rawValue }
        var request = VaultRequest("POST", "/api/chat", json: try? JSONSerialization.data(withJSONObject: body))
        request.headers = cookieHeader
        let lines = transport.lines(request)
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    for try await line in lines {
                        continuation.yield(try VaultJSON.decoder.decode(ChatEvent.self, from: Data(line.utf8)))
                    }
                    continuation.finish()
                } catch VaultError.signedOut {
                    session.save(nil)
                    continuation.finish(throwing: VaultError.signedOut)
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: - 1.0: several computers keep this vault

    public func syncStatus() async throws -> SyncStatus { try await get("/api/sync/status") }

    /// A code for another computer to also keep this vault (admins).
    public func syncInvite() async throws -> SyncInvite { try await post("/api/sync/invite", [:]) }

    /// The certificate of the computer at `address`, to show its security code before joining it.
    public func syncProbe(_ address: String) async throws -> SyncProbe {
        try await get("/api/sync/probe?address=\(address.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? address)")
    }

    /// This new, empty vault joins the one kept on another computer (whose security code the person checked).
    public func joinVault(address: String, code: String, fingerprint: String) async throws {
        _ = try await send(VaultRequest("POST", "/api/sync/join-vault", json: try JSONSerialization.data(
            withJSONObject: ["address": address, "code": code, "fingerprint": fingerprint])))
    }

    public func syncJoining() async throws -> SyncJoining? {
        struct Answer: Decodable { let joining: SyncJoining? }
        return try await get("/api/sync/joining", as: Answer.self).joining
    }

    // MARK: - The search model

    /// Downloaded and loaded in the background on the first start; until it's ready, searches wait.
    public func searchStatus() async throws -> SearchStatus { try await get("/api/search/status") }

    // MARK: - The local AI

    /// How far the download of the local AI is.
    public func aiDownloadStatus() async throws -> AIDownloadStatus { try await get("/api/ai/download") }

    /// Download llama.cpp and the answer model (about 5 GB, once), then start it. Admins only.
    public func startAIDownload() async throws -> AIDownloadStatus { try await post("/api/ai/download", [:]) }

    // MARK: - Other devices

    /// A one-time code for another computer (`typed`: short, to type) or a phone (for a QR code), for 10 minutes.
    public func startPairing(typed: Bool) async throws -> Pairing {
        try decode(try await send(VaultRequest("POST", "/api/mobile/pairing?typed=\(typed)")))
    }

    // MARK: - Plumbing

    private struct SignedIn: Decodable { let user: User }
    private struct RemindersAnswer: Decodable { let items: [Reminder] }

    private var cookieHeader: [String: String] {
        session.load().map { ["cookie": "\(Self.cookieName)=\($0)"] } ?? [:]
    }

    func send(_ request: VaultRequest) async throws -> VaultResponse {
        var request = request
        request.headers.merge(cookieHeader) { _, new in new }
        let response = try await transport.send(request)
        if let token = Self.sessionToken(in: response.headers["set-cookie"]) {
            session.save(token.isEmpty ? nil : token)  // signed in (a new token) or out (an emptied cookie)
        }
        guard (200..<300).contains(response.status) else {
            if response.status == 401 { session.save(nil) }
            throw EnginePipe.error(status: response.status, body: response.body)
        }
        return response
    }

    /// The session token from a Set-Cookie header ("" when the vault cleared it).
    static func sessionToken(in header: String?) -> String? {
        guard let header, let range = header.range(of: "\(cookieName)=") else { return nil }
        let rest = header[range.upperBound...]
        let value = rest.prefix { $0 != ";" && $0 != "," }
        return value == "\"\"" ? "" : String(value)
    }

    private func decode<T: Decodable>(_ response: VaultResponse) throws -> T {
        try VaultJSON.decoder.decode(T.self, from: response.body)
    }

    private func get<T: Decodable>(_ path: String, as: T.Type = T.self) async throws -> T {
        try decode(try await send(VaultRequest("GET", path)))
    }

    private func post<T: Decodable>(_ path: String, _ body: [String: Any], as: T.Type = T.self) async throws -> T {
        try decode(try await send(VaultRequest("POST", path, json: try JSONSerialization.data(withJSONObject: body))))
    }

    private func patch<T: Decodable>(_ path: String, _ body: [String: Any], as: T.Type = T.self) async throws -> T {
        try decode(try await send(VaultRequest("PATCH", path, json: try JSONSerialization.data(withJSONObject: body))))
    }
}
