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
        case settingUpEngine  // this Mac keeps the vault: downloading its engine (once, or after an update)
        case notInstalled(folder: String)  // no vault on this Mac yet
        case failed(String)  // the vault couldn't open; says why
        case signIn(AuthStatus)
        case joining  // 1.0: joining a vault kept on another computer
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
    /// Asked again for every newly installed copy of the app: the answer belongs to the copy it was given to.
    var mode: String? {
        get {
            guard UserDefaults.standard.string(forKey: "modeInstall") == Self.installIdentity else { return nil }
            return UserDefaults.standard.string(forKey: "mode")
        }
        set {
            UserDefaults.standard.set(newValue, forKey: "mode")
            UserDefaults.standard.set(Self.installIdentity, forKey: "modeInstall")
        }
    }

    /// This installed copy of the app: where it is and when it was put there (a new download dragged to
    /// Applications is a new copy).
    static let installIdentity: String = {
        let app = Bundle.main.bundleURL
        let created = (try? app.resourceValues(forKeys: [.creationDateKey]).creationDate)?.timeIntervalSince1970 ?? 0
        return "\(app.path)|\(Int(created))"
    }()
    /// The engine this app version needs (engine.json, written when the DMG is built), or nil when the app runs
    /// against a development checkout. It is downloaded only when this Mac keeps the vault.
    static let release: EngineRelease? = Bundle.main.url(forResource: "engine", withExtension: "json").flatMap(EngineInstaller.load)

    /// Where the downloaded engine lives (its own Python, the vault's code, Tesseract), or nil in development.
    static let bundledEngine: URL? = release == nil ? nil
        : FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Application Support/Document Vault/Engine")

    var choosingFiles = false  // the Add Documents file chooser is open (from anywhere in the window)
    var engineProgress: EngineProgress?
    var engineProblem: String?

    /// Download and unpack the engine, then open the vault ($DOCUMENT_VAULT_ENGINE_ARCHIVE: a local copy, for CI).
    func setUpEngine() async {
        guard let release = Self.release, let folder = Self.bundledEngine else { return }
        engineProblem = nil
        engineProgress = nil
        let archive = ProcessInfo.processInfo.environment["DOCUMENT_VAULT_ENGINE_ARCHIVE"].flatMap { $0.isEmpty ? nil : URL(fileURLWithPath: $0) }
        do {
            try await EngineInstaller.install(release, into: folder, archive: archive) { step in
                Task { @MainActor in self.engineProgress = step }
            }
            await start()
        } catch {
            engineProblem = error.localizedDescription
        }
    }

    /// The vault's own folder (its data/ holds the documents, the index and the logs), where the engine runs. Apart
    /// from the app, so replacing or deleting the app never touches it.
    let folder: URL

    init() {
        folder = Self.bundledEngine == nil ? Self.vaultFolder()
            : FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Application Support/Document Vault/Vault")
        if UserDefaults.standard.bool(forKey: "keepAnswerAcrossUpdate") {  // updated by the app itself: same answer
            UserDefaults.standard.removeObject(forKey: "keepAnswerAcrossUpdate")
            if UserDefaults.standard.string(forKey: "mode") != nil {
                UserDefaults.standard.set(Self.installIdentity, forKey: "modeInstall")
            }
        } else if let earlier = UserDefaults.standard.string(forKey: "modeInstall"), earlier != Self.installIdentity {
            connections.save(nil)  // a newly installed copy starts fresh: asked again, paired again if it connects
            UserDefaults.standard.removeObject(forKey: "mode")
            UserDefaults.standard.removeObject(forKey: "modeInstall")
        }
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
            // 1.0: remember every computer that keeps this vault, so this Mac still works when the paired one is off
            if let updated = try? await remote.refreshCentres() { connections.save(updated) }
            return
        default:
            break
        }
        if let release = Self.release, let engine = Self.bundledEngine, !EngineInstaller.isInstalled(engine, release) {
            phase = .settingUpEngine
            await setUpEngine()
            return
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
                environment["PYTHONPATH"] = Bundle.main.resourceURL!.path  // the vault's code comes with the app
                environment["VAULT_RUNTIME"] = bundled.path  // the engine it downloaded: Python, its packages, Tesseract
                environment["PYTHONNOUSERSITE"] = "1"
                environment["PYTHONDONTWRITEBYTECODE"] = "1"  // the app stays as it was signed (compiled at build)
            }
            // Just after an update (or a quick reopen) the previous copy's engine can still be closing and holding the
            // vault: wait for it (up to half a minute) instead of saying the vault is open elsewhere.
            var attempt = 0
            while true {
                let engine = try EnginePipe(python: python, folder: folder, environment: environment, log: logFile)
                self.engine = engine
                do {
                    try await engine.waitUntilReady()
                    break
                } catch let failure where attempt < 15 && failure.localizedDescription.contains("already open") {
                    engine.stop()
                    attempt += 1
                    try await Task.sleep(for: .seconds(2))
                }
            }
            client = VaultClient(transport: engine!)
            await checkSignIn()
            watchAssistant()
        } catch {
            phase = .failed(error.localizedDescription)
        }
    }

    /// The assistant starts a moment after the vault opens (its model loads): check again until it's ready, so
    /// "The answer model is loading" doesn't stay on screen.
    private func watchAssistant() {
        Task {
            for _ in 0..<60 {
                try? await Task.sleep(for: .seconds(3))
                guard let client, case .ready = phase else { return }
                if let fresh = try? await client.info() { info = fresh }
                if info?.llmStatus.ready == true || info?.llmStatus.canDownload == true { return }
            }
        }
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
        let (send, refused) = AcceptedFiles.sort(files)  // zips and other files are turned away here, not sent
        guard !send.isEmpty else { return refused }
        do {
            let results = try await client.upload(send, password: password)
            await reload()
            return results + refused
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
