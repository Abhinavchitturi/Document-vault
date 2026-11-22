import AppKit
import Observation
import VaultKit

/// The app's state: the engine (the vault, started as a hidden helper), who is signed in, and what the screens show.
@MainActor @Observable
final class AppModel {
    enum Phase {
        case choosing  // first start: this Mac as the centre, or connect to another computer
        case connecting  // the connect form
        case starting
        case notInstalled(folder: String)  // no vault on this Mac yet
        case failed(String)  // the vault couldn't open; says why
        case signIn(AuthStatus)
        case ready(User)
    }

    var phase: Phase = .starting
    var info: Info?
    var documents: [VaultDocument] = []
    var upcoming: [Upcoming] = []
    var problem: String?  // shown as an alert
    private(set) var client: VaultClient?
    private var engine: EnginePipe?
    private(set) var remote: RemoteVault?
    let connections = ConnectionStore()

    /// "here" (this Mac is the centre: the engine runs here) or "remote" (it uses the vault on another computer).
    var mode: String? {
        get { UserDefaults.standard.string(forKey: "mode") }
        set { UserDefaults.standard.set(newValue, forKey: "mode") }
    }
    /// The engine inside the app (Contents/Resources/Engine: its own Python, the vault's code, Tesseract), or nil
    /// when the app runs against a development checkout.
    static let bundledEngine: URL? = {
        guard let engine = Bundle.main.resourceURL?.appending(path: "Engine"),
              FileManager.default.isExecutableFile(atPath: engine.appending(path: "python/bin/python3").path) else { return nil }
        return engine
    }()

    /// The vault's own folder (its data/ holds the documents, the index and the logs), where the engine runs. Apart
    /// from the app, so replacing or deleting the app never touches it.
    let folder: URL

    init() {
        folder = Self.bundledEngine == nil ? Self.vaultFolder()
            : FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Application Support/Document Vault/Vault")
    }

    var user: User? {
        if case .ready(let user) = phase { return user }
        return nil
    }

    /// Where the vault lives: $DOCUMENT_VAULT_DIR, else where the installer put it, else ~/Document-Vault.
    static func vaultFolder() -> URL {
        let home = FileManager.default.homeDirectoryForCurrentUser
        if let set = ProcessInfo.processInfo.environment["DOCUMENT_VAULT_DIR"], !set.isEmpty {
            return URL(fileURLWithPath: (set as NSString).expandingTildeInPath)
        }
        let recorded = home.appending(path: ".document-vault/install-dir")
        if let text = try? String(contentsOf: recorded, encoding: .utf8) {
            let path = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if !path.isEmpty { return URL(fileURLWithPath: path) }
        }
        return home.appending(path: "Document-Vault")
    }

    var logFile: URL { folder.appending(path: "data/logs/app-engine.log") }

    // MARK: - Starting and stopping

    func start() async {
        switch mode {
        case nil:
            phase = .choosing
            return
        case "remote":
            guard let connection = connections.load() else { phase = .connecting; return }
            phase = .starting
            let remote = RemoteVault(connection)
            self.remote = remote
            client = VaultClient(transport: remote, session: MemorySessionStore())  // a paired device signs in with its key
            await checkSignIn()
            return
        default:
            break
        }
        let python = Self.bundledEngine?.appending(path: "python/bin/python3") ?? folder.appending(path: "venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            phase = .notInstalled(folder: folder.path)
            return
        }
        phase = .starting
        do {
            try FileManager.default.createDirectory(at: logFile.deletingLastPathComponent(), withIntermediateDirectories: true)
            var environment: [String: String] = [:]
            if let bundled = Self.bundledEngine {
                environment["PYTHONPATH"] = bundled.path
                environment["PYTHONNOUSERSITE"] = "1"
                environment["PYTHONDONTWRITEBYTECODE"] = "1"  // the app stays as it was signed (compiled at build)
                Self.allowBundledEngine(bundled)
            }
            let engine = try EnginePipe(python: python, folder: folder, environment: environment, log: logFile)
            self.engine = engine
            try await engine.waitUntilReady()
            client = VaultClient(transport: engine)
            await checkSignIn()
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    /// A downloaded app's files carry the "downloaded from the internet" mark. The person allowed this app already,
    /// so take the mark off its own engine, or macOS would ask again about its Python and Tesseract.
    private static func allowBundledEngine(_ engine: URL) {
        let xattr = Process()
        xattr.executableURL = URL(fileURLWithPath: "/usr/bin/xattr")
        xattr.arguments = ["-dr", "com.apple.quarantine", engine.path]
        try? xattr.run()
        xattr.waitUntilExit()
    }

    func stop() {
        engine?.stop()
        engine = nil
        remote = nil
        client = nil
    }

    /// Pair with the centre computer (after the person confirmed its security code) and use its vault from now on.
    func connect(address: String, code: String, fingerprint: String) async throws {
        let name = Host.current().localizedName ?? "Mac"
        let connection = try await RemoteVault.pair(address, code: code, fingerprint: fingerprint, deviceName: name)
        connections.save(connection)
        mode = "remote"
        await start()
    }

    /// Forget the centre computer (its vault stays there) and choose again.
    func disconnect() async {
        stop()
        connections.save(nil)
        mode = nil
        await start()
    }

    /// Stop being the centre and connect to another computer instead (this Mac's own vault stays in its folder).
    func useAnotherComputer() {
        stop()
        mode = "remote"
        phase = .connecting
    }

    // MARK: - Signing in

    func checkSignIn() async {
        guard let client else { return }
        do {
            let status = try await client.authStatus()
            if let user = status.user {
                phase = .ready(user)
                await reload()
            } else {
                phase = .signIn(status)
            }
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    /// Sign in, or (the first time) choose the admin's name and password. Throws so the form can show why not.
    func signIn(name: String, password: String, firstTime: Bool) async throws {
        guard let client else { return }
        let user = firstTime ? try await client.setUp(name: name, password: password)
                             : try await client.signIn(name: name, password: password)
        phase = .ready(user)
        await reload()
    }

    func signOut() async {
        await client?.signOut()
        documents = []
        upcoming = []
        info = nil
        await checkSignIn()
    }

    // MARK: - The vault's contents

    func reload() async {
        guard let client else { return }
        do {
            async let info = client.info()
            async let documents = client.documents()
            async let upcoming = client.upcoming()
            (self.info, self.documents, self.upcoming) = try await (info, documents, upcoming)
        } catch {
            report(error)
        }
    }

    /// Show an error; a lost sign-in goes back to the sign-in screen instead.
    func report(_ error: Error) {
        if case VaultError.signedOut = error {
            Task { await checkSignIn() }
        } else {
            problem = error.localizedDescription
        }
    }

    func add(_ files: [URL], password: String? = nil) async -> [UploadResult] {
        guard let client else { return [] }
        do {
            let results = try await client.upload(files, password: password)
            await reload()
            return results
        } catch {
            report(error)
            return []
        }
    }

    func delete(_ document: VaultDocument) async {
        do {
            try await client?.delete(document)
            await reload()
        } catch {
            report(error)
        }
    }

    func setCategory(_ document: VaultDocument, _ category: Category) async {
        do {
            _ = try await client?.setCategory(document, to: category)
            await reload()
        } catch {
            report(error)
        }
    }

    func showLog() {
        NSWorkspace.shared.activateFileViewerSelecting([logFile])
    }
}
