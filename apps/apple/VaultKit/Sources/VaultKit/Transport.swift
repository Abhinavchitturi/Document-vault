import Foundation

/// One request to the vault, in the vault's own terms (an API path), whatever carries it.
public struct VaultRequest: Sendable {
    public var method: String
    public var path: String
    public var json: Data?  // a JSON body, already encoded
    public var files: [URL] = []  // an upload: the files, sent by path (the engine reads them; nothing is copied)
    public var form: [String: String] = [:]
    public var headers: [String: String] = [:]

    public init(_ method: String, _ path: String, json: Data? = nil, files: [URL] = [], form: [String: String] = [:]) {
        self.method = method
        self.path = path
        self.json = json
        self.files = files
        self.form = form
    }
}

public struct VaultResponse: Sendable {
    public let status: Int
    public let headers: [String: String]
    public let body: Data
}

public enum VaultError: Error, LocalizedError, Equatable {
    case signedOut  // 401: sign in again
    case http(status: Int, detail: String)
    case engine(String)  // the engine couldn't start, or stopped

    public var errorDescription: String? {
        switch self {
        case .signedOut: "You're signed out. Sign in again."
        case .http(_, let detail): detail
        case .engine(let message): message
        }
    }
}

/// What carries requests to the vault: the private pipe to the engine on a Mac (EnginePipe); the network on a phone.
public protocol VaultTransport: Sendable {
    func send(_ request: VaultRequest) async throws -> VaultResponse
    /// A streamed answer, one line at a time (the chat). Throws VaultError for a non-200 answer.
    func lines(_ request: VaultRequest) -> AsyncThrowingStream<String, Error>
}
