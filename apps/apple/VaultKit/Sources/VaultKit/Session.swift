import Foundation
import Security

/// Where the app keeps the sign-in (the vault's session cookie), like a browser keeps its cookies.
public protocol SessionStore: Sendable {
    func load() -> String?
    func save(_ cookie: String?)
}

/// The macOS / iOS Keychain: the sign-in survives quitting the app, and only this app can read it.
public struct KeychainSessionStore: SessionStore {
    let service: String
    let account: String

    public init(service: String = "Document Vault app", account: String = "session") {
        self.service = service
        self.account = account
    }

    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }

    public func load() -> String? {
        var q = query
        q[kSecReturnData as String] = true
        q[kSecMatchLimit as String] = kSecMatchLimitOne
        // Never ask: an item an earlier copy of the app saved makes macOS ask "wants to use your confidential
        // information" (and the app waits on it). Without asking, it's simply not found.
        q[kSecUseAuthenticationUI as String] = kSecUseAuthenticationUIFail
        var item: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &item) == errSecSuccess, let data = item as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public func save(_ cookie: String?) {
        SecItemDelete(query as CFDictionary)
        guard let cookie, let data = cookie.data(using: .utf8) else { return }
        var q = query
        q[kSecValueData as String] = data
        q[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(q as CFDictionary, nil)
    }
}

/// A file only this Mac user can read (in Application Support/Document Vault). The app isn't signed with an Apple
/// Developer ID, so each new copy is a different app to the Keychain, which then asks for the password and holds the
/// app until it's answered; a private file doesn't. What an earlier version kept in the Keychain is picked up once
/// (without asking) and moved here.
public struct FileSessionStore: SessionStore {
    let file: URL
    let earlier: KeychainSessionStore

    public init(name: String = "session") {
        let folder = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Application Support/Document Vault")
        file = folder.appending(path: name)
        earlier = KeychainSessionStore(service: "Document Vault app", account: name)
    }

    public func load() -> String? {
        if let data = try? Data(contentsOf: file) { return String(data: data, encoding: .utf8) }
        guard let moved = earlier.load() else { return nil }
        save(moved)
        return moved
    }

    public func save(_ value: String?) {
        guard let value else {
            try? FileManager.default.removeItem(at: file)
            return
        }
        let folder = file.deletingLastPathComponent()
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        FileManager.default.createFile(atPath: file.path, contents: Data(value.utf8), attributes: [.posixPermissions: 0o600])
    }
}

/// The connection to the centre computer (its addresses, certificate and this device's key), in a private file.
public struct ConnectionStore: Sendable {
    let store = FileSessionStore(name: "connection")
    public init() {}

    public func load() -> Connection? {
        store.load().flatMap { try? JSONDecoder().decode(Connection.self, from: Data($0.utf8)) }
    }

    public func save(_ connection: Connection?) {
        store.save(connection.flatMap { try? JSONEncoder().encode($0) }.flatMap { String(data: $0, encoding: .utf8) })
    }
}

/// For tests and previews.
public final class MemorySessionStore: SessionStore, @unchecked Sendable {
    private let lock = NSLock()
    private var cookie: String?
    public init(_ cookie: String? = nil) { self.cookie = cookie }
    public func load() -> String? { lock.withLock { cookie } }
    public func save(_ cookie: String?) { lock.withLock { self.cookie = cookie } }
}
