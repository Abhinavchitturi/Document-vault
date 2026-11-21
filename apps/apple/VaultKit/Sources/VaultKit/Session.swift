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

/// For tests and previews.
public final class MemorySessionStore: SessionStore, @unchecked Sendable {
    private let lock = NSLock()
    private var cookie: String?
    public init(_ cookie: String? = nil) { self.cookie = cookie }
    public func load() -> String? { lock.withLock { cookie } }
    public func save(_ cookie: String?) { lock.withLock { self.cookie = cookie } }
}
