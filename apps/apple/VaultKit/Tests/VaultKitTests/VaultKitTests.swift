import Foundation
import Testing
@testable import VaultKit

@Test func readsTheSessionCookieTheVaultSets() {
    #expect(VaultClient.sessionToken(in: "vault_session=abc123; HttpOnly; Max-Age=2592000; Path=/; SameSite=strict") == "abc123")
    #expect(VaultClient.sessionToken(in: "vault_session=\"\"; expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/") == "")
    #expect(VaultClient.sessionToken(in: "other=1") == nil)
    #expect(VaultClient.sessionToken(in: nil) == nil)
}

@Test func decodesEveryKindOfChatEvent() throws {
    let lines = [
        #"{"type":"sources","sources":[{"rank":1,"score":0.9,"chunk_id":"c1","doc_id":"d1","filename":"Bike.pdf","category":"insurance","page":1,"text":"Expires 14 March 2027"}]}"#,
        #"{"type":"token","text":"Your bike "}"#,
        #"{"type":"done","citations":[1],"found":true,"llm":"llama.cpp/llama3.1:8b"}"#,
        #"{"type":"error","message":"The assistant is offline.","detail":"Restart the vault."}"#,
    ]
    let events = try lines.map { try VaultJSON.decoder.decode(ChatEvent.self, from: Data($0.utf8)) }
    guard case .sources(let sources) = events[0] else { Issue.record("no sources"); return }
    #expect(sources.first?.filename == "Bike.pdf" && sources.first?.category == .insurance)
    #expect(events[1] == .token("Your bike "))
    #expect(events[2] == .done(citations: [1], found: true, llm: "llama.cpp/llama3.1:8b"))
    #expect(events[3] == .error(message: "The assistant is offline.", detail: "Restart the vault."))
}

/// The project folder, from this file's place in it (apps/apple/VaultKit/Tests/VaultKitTests/).
private let repo = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

@Test(.enabled(if: FileManager.default.isExecutableFile(atPath: repo.appendingPathComponent("venv/bin/python").path),
               "needs the project's Python (venv) with its packages"),
      .timeLimit(.minutes(3)))
func theMacAppTalksToTheRealEngineOverThePipe() async throws {
    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("vaultkit-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: tmp) }
    let engine = try EnginePipe(
        python: repo.appendingPathComponent("venv/bin/python"), folder: repo,
        arguments: ["--index-dir", tmp.appendingPathComponent("index").path,
                    "--database-url", "sqlite:///\(tmp.appendingPathComponent("vault.db").path)",
                    "--no-llm", "--no-phones", "--owner", "abhinav"],
        environment: ["VAULT_KEYCHAIN": "off", "VAULT_AI_AUTO_DOWNLOAD": "0", "VAULT_KEY_DIR": tmp.appendingPathComponent("keys").path,
                      "VAULT_LLM_DIR": tmp.appendingPathComponent("llm").path, "VAULT_FACTS_READER": "rules"],
        log: tmp.appendingPathComponent("engine.log"))
    defer { engine.stop() }
    try await engine.waitUntilReady()

    let session = MemorySessionStore()
    let vault = VaultClient(transport: engine, session: session)
    #expect(try await vault.authStatus().setupNeeded)
    let me = try await vault.setUp(name: "abhinav", password: "a long test password")
    #expect(me.name == "abhinav" && me.isAdmin && session.load() != nil)

    let file = tmp.appendingPathComponent("Bike Insurance 2026.txt")
    try "Two-wheeler policy TW-778812. Period: 15 March 2026 to 14 March 2027.".write(to: file, atomically: true, encoding: .utf8)
    let added = try await vault.upload([file])
    #expect(added.first?.status == "added")
    let docs = try await vault.documents()
    #expect(docs.map(\.filename) == ["Bike Insurance 2026.txt"])
    #expect(try await vault.original(docs[0].docId) == (try Data(contentsOf: file)))
    #expect(try await vault.info().numDocuments == 1)

    await #expect(throws: VaultError.self) {  // started with --no-llm: the chat says so instead of answering
        for try await _ in vault.chat("When does my bike insurance expire?") {}
    }

    await vault.signOut()
    #expect(session.load() == nil)
    await #expect(throws: VaultError.signedOut) { try await vault.documents() }
}

@Test func typedAddressesBecomeTheVaultsAddress() {
    #expect(RemoteVault.address("192.168.1.20") == "https://192.168.1.20:8443")
    #expect(RemoteVault.address(" my-mac.tail1234.ts.net ") == "https://my-mac.tail1234.ts.net:8443")
    #expect(RemoteVault.address("https://10.0.0.5:9000/") == "https://10.0.0.5:9000")
    #expect(RemoteVault.securityCode("ab12cd34ef567890aaaabbbbccccdddd") == "AB12 CD34 EF56 7890")
}

@Test(.enabled(if: FileManager.default.isExecutableFile(atPath: repo.appendingPathComponent("venv/bin/python").path),
               "needs the project's Python (venv) with its packages"),
      .timeLimit(.minutes(3)))
func aMacPairsWithTheCentreComputerAndUsesItsVault() async throws {
    let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("vaultkit-centre-\(UUID().uuidString)")
    try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    defer { try? FileManager.default.removeItem(at: tmp) }
    let centre = try EnginePipe(  // the centre computer, with device access on a test port
        python: repo.appendingPathComponent("venv/bin/python"), folder: repo,
        arguments: ["--index-dir", tmp.appendingPathComponent("index").path,
                    "--database-url", "sqlite:///\(tmp.appendingPathComponent("vault.db").path)",
                    "--no-llm", "--phone-port", "8473", "--phone-address", "127.0.0.1", "--owner", "abhinav"],
        environment: ["VAULT_KEYCHAIN": "off", "VAULT_AI_AUTO_DOWNLOAD": "0", "VAULT_KEY_DIR": tmp.appendingPathComponent("keys").path,
                      "VAULT_LLM_DIR": tmp.appendingPathComponent("llm").path, "VAULT_FACTS_READER": "rules"],
        log: tmp.appendingPathComponent("centre.log"))
    defer { centre.stop() }
    try await centre.waitUntilReady()
    let host = VaultClient(transport: centre, session: MemorySessionStore())
    _ = try await host.setUp(name: "abhinav", password: "a long test password")
    let shown = try await host.startPairing(typed: true)
    try await Task.sleep(for: .seconds(1.5))  // the HTTPS listener starts just after the vault opens

    let seen = try await RemoteVault.probe("127.0.0.1:8473")
    #expect(seen == shown.fingerprint)
    let connection = try await RemoteVault.pair("127.0.0.1:8473", code: shown.code.lowercased(), fingerprint: seen, deviceName: "Test Mac")
    let laptop = VaultClient(transport: RemoteVault(connection), session: MemorySessionStore())
    #expect(try await laptop.authStatus().user?.name == "abhinav")
    let file = tmp.appendingPathComponent("Bike Insurance 2026.txt")
    try "Two-wheeler policy TW-778812. Period: 15 March 2026 to 14 March 2027.".write(to: file, atomically: true, encoding: .utf8)
    #expect(try await laptop.upload([file]).first?.status == "added")
    let docs = try await laptop.documents()
    #expect(docs.map(\.filename) == ["Bike Insurance 2026.txt"])
    #expect(try await laptop.original(docs[0].docId) == (try Data(contentsOf: file)))
    #expect(try await host.documents().count == 1)  // the same vault: the centre sees it too

    let impostor = VaultClient(transport: RemoteVault(Connection(urls: connection.urls, fingerprint: String(repeating: "0", count: 64),
                                                                  token: connection.token, name: "impostor")),
                               session: MemorySessionStore())
    await #expect(throws: VaultError.self) { try await impostor.info() }
}
