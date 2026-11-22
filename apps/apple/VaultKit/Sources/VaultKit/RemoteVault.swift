import CryptoKit
import Foundation

/// The vault on another computer (the centre), reached over the network (home Wi-Fi, or Tailscale from anywhere) the
/// way a paired phone reaches it: HTTPS pinned to that vault's own certificate, and this device's access key.
public struct Connection: Codable, Equatable, Sendable {
    public var urls: [String]
    public let fingerprint: String
    public let token: String
    public let name: String
}

public final class RemoteVault: VaultTransport, @unchecked Sendable {
    public static let port = 8443
    private let lock = NSLock()
    private var current: Connection
    private let session: URLSession

    public init(_ connection: Connection) {
        current = connection
        session = Self.session(trusting: connection.fingerprint)
    }

    public var connection: Connection { lock.withLock { current } }

    /// "192.168.1.20", "my-mac.tailnet.ts.net" or a full "https://…:8443" → the vault's address.
    public static func address(_ typed: String) -> String {
        var text = typed.trimmingCharacters(in: .whitespacesAndNewlines)
        while text.hasSuffix("/") { text.removeLast() }
        if !text.contains("://") { text = "https://" + text }
        guard var parts = URLComponents(string: text) else { return text }
        parts.scheme = "https"
        if parts.port == nil { parts.port = port }
        parts.path = ""
        return parts.string ?? text
    }

    /// The short security code people compare on both screens: the first 16 characters of the fingerprint.
    public static func securityCode(_ fingerprint: String) -> String {
        stride(from: 0, to: 16, by: 4).map { i in
            let start = fingerprint.index(fingerprint.startIndex, offsetBy: i)
            return fingerprint[start..<fingerprint.index(start, offsetBy: 4)].uppercased()
        }.joined(separator: " ")
    }

    /// Step 1 of pairing: reach the address and read its certificate, without trusting it yet. Returns the
    /// fingerprint, so the person can check it matches the security code on the centre computer's screen.
    public static func probe(_ typed: String) async throws -> String {
        let pin = Pinning(expected: nil)
        let session = URLSession(configuration: .ephemeral, delegate: pin, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        var request = URLRequest(url: URL(string: address(typed) + "/api/mobile/hello")!)
        request.timeoutInterval = 10
        do {
            let (data, _) = try await session.data(for: request)
            guard ((try? JSONSerialization.jsonObject(with: data)) as? [String: Any])?["app"] as? String == "document-vault" else {
                throw VaultError.http(status: 0, detail: "That address answered, but it isn't a Document Vault.")
            }
        } catch let error as VaultError {
            throw error
        } catch {
            throw VaultError.engine("Couldn't reach a vault at \(address(typed)). Is Document Vault open on that computer, and are "
                                    + "both on the same Wi-Fi (or Tailscale)? (\(error.localizedDescription))")
        }
        guard let seen = pin.seen else { throw VaultError.engine("The vault didn't show a certificate.") }
        return seen
    }

    /// Step 2: pair with the typed code, trusting only the certificate the person confirmed.
    public static func pair(_ typed: String, code: String, fingerprint: String, deviceName: String) async throws -> Connection {
        let base = address(typed)
        let session = Self.session(trusting: fingerprint)
        defer { session.finishTasksAndInvalidate() }
        var request = URLRequest(url: URL(string: base + "/api/mobile/pair")!)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["code": code, "device_name": deviceName])
        let (data, response) = try await session.data(for: request)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200, let token = ((try? JSONSerialization.jsonObject(with: data)) as? [String: Any])?["token"] as? String else {
            throw EnginePipe.error(status: status == 401 ? 400 : status, body: data)
        }
        let paired = RemoteVault(Connection(urls: [base], fingerprint: fingerprint, token: token, name: base))
        let answer = try await paired.send(VaultRequest("GET", "/api/mobile/addresses"))
        let urls = ((try? JSONSerialization.jsonObject(with: answer.body)) as? [String: Any])?["urls"] as? [String] ?? []
        return Connection(urls: [base] + urls.filter { $0 != base }, fingerprint: fingerprint, token: token, name: base)
    }

    // MARK: - VaultTransport

    public func send(_ request: VaultRequest) async throws -> VaultResponse {
        var last: Error?
        for url in connection.urls {  // the address that answers: home Wi-Fi, else Tailscale
            do {
                let (data, response) = try await session.data(for: try build(url, request))
                let http = response as? HTTPURLResponse
                var headers: [String: String] = [:]
                http?.allHeaderFields.forEach { if let k = $0.key as? String, let v = $0.value as? String { headers[k.lowercased()] = v } }
                remember(url)
                return VaultResponse(status: http?.statusCode ?? 0, headers: headers, body: data)
            } catch let error as URLError where error.code == .timedOut {
                // it reached the centre but no answer came in time: never send it again to another address (an
                // upload would be added twice)
                throw VaultError.engine("The vault on \(connection.name) didn't answer in time. It may still be busy; "
                                        + "check in a minute whether it was done.")
            } catch let error as URLError {
                last = error
            }
        }
        throw VaultError.engine("Can't reach the vault on \(connection.name). Is that computer on, with Document Vault open, and on "
                                + "the same Wi-Fi or Tailscale? (\(last?.localizedDescription ?? "no address"))")
    }

    public func lines(_ request: VaultRequest) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, response) = try await session.bytes(for: try build(connection.urls[0], request))
                    let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                    if status != 200 {
                        var body = Data()
                        for try await byte in bytes { body.append(byte) }
                        throw EnginePipe.error(status: status, body: body)
                    }
                    for try await line in bytes.lines where !line.isEmpty { continuation.yield(line) }
                    continuation.finish()
                } catch let error as URLError {
                    continuation.finish(throwing: VaultError.engine("Can't reach the vault on \(connection.name): \(error.localizedDescription)"))
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    // MARK: - Plumbing

    private func remember(_ url: String) {
        lock.withLock { if current.urls.first != url { current.urls = [url] + current.urls.filter { $0 != url } } }
    }

    private func build(_ base: String, _ request: VaultRequest) throws -> URLRequest {
        guard let url = URL(string: base + request.path) else { throw URLError(.badURL) }
        var http = URLRequest(url: url)
        http.httpMethod = request.method
        // uploads may wait while the centre reads big scans (or, just after it opened, loads its search model)
        http.timeoutInterval = request.files.isEmpty ? 300 : 1800
        for (key, value) in request.headers where key.lowercased() != "cookie" { http.setValue(value, forHTTPHeaderField: key) }
        http.setValue("Bearer \(connection.token)", forHTTPHeaderField: "Authorization")
        if !request.files.isEmpty {
            let boundary = "vault-\(UUID().uuidString)"
            var body = Data()
            func add(_ text: String) { body.append(Data(text.utf8)) }
            for (key, value) in request.form {
                add("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(key)\"\r\n\r\n\(value)\r\n")
            }
            for file in request.files {
                add("--\(boundary)\r\nContent-Disposition: form-data; name=\"files\"; filename=\"\(file.lastPathComponent)\"\r\n")
                add("Content-Type: application/octet-stream\r\n\r\n")
                body.append(try Data(contentsOf: file))
                add("\r\n")
            }
            add("--\(boundary)--\r\n")
            http.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            http.httpBody = body
        } else if let json = request.json {
            http.setValue("application/json", forHTTPHeaderField: "Content-Type")
            http.httpBody = json
        }
        return http
    }

    /// A session that trusts exactly one certificate: the vault's own (no certificate authority at all).
    private static func session(trusting fingerprint: String) -> URLSession {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        return URLSession(configuration: configuration, delegate: Pinning(expected: fingerprint), delegateQueue: nil)
    }
}

/// Checks the vault's certificate by its SHA-256 fingerprint; with no expected one (pairing), it only records it.
final class Pinning: NSObject, URLSessionDelegate, @unchecked Sendable {
    let expected: String?
    private(set) var seen: String?

    init(expected: String?) { self.expected = expected }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              let leaf = (SecTrustCopyCertificateChain(trust) as? [SecCertificate])?.first else {
            completionHandler(.performDefaultHandling, nil)
            return
        }
        let fingerprint = SHA256.hash(data: SecCertificateCopyData(leaf) as Data).map { String(format: "%02x", $0) }.joined()
        seen = fingerprint
        if expected == nil || expected == fingerprint {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }
}
