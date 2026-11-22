import CryptoKit
import Foundation

/// The vault on another computer (the centre), reached over the network (home Wi-Fi, or Tailscale from anywhere) the
/// way a paired phone reaches it: HTTPS pinned to that vault's own certificate, and this device's access key.
public struct Connection: Codable, Equatable, Sendable {
    public var urls: [String]
    public let fingerprint: String
    public let token: String
    public let name: String
    /// 1.0: the other computers that keep the same vault, used when the paired one is off.
    public var others: [Centre]?
}

/// 1.0: another computer that keeps the same vault, and its certificate.
public struct Centre: Codable, Equatable, Sendable {
    public let name: String
    public let urls: [String]
    public let fingerprint: String
}

public final class RemoteVault: VaultTransport, @unchecked Sendable {
    public static let port = 8443
    private let lock = NSLock()
    private var current: Connection
    private var preferred: String?  // the address that answered last (maybe another computer that keeps the vault)
    private var session: URLSession!

    public init(_ connection: Connection) {
        current = connection
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        // the paired computer, and the others that keep the same vault
        session = URLSession(configuration: configuration, delegate: Pinning(trusted: { [weak self] in self?.trusts($0) ?? false }),
                             delegateQueue: nil)
    }

    private func trusts(_ fingerprint: String) -> Bool {
        lock.withLock { current.fingerprint == fingerprint || (current.others ?? []).contains { $0.fingerprint == fingerprint } }
    }

    /// The paired computer's addresses first, then the other computers that keep the vault; the one that answered
    /// last before all of them.
    private var candidates: [String] {
        lock.withLock {
            var all: [String] = []
            for url in current.urls + (current.others ?? []).flatMap(\.urls) where !all.contains(url) { all.append(url) }
            if let preferred, let at = all.firstIndex(of: preferred) { all.insert(all.remove(at: at), at: 0) }
            return all
        }
    }

    /// Learn which computers keep this vault now (the paired one tells), to use another when it is off. Returns the
    /// updated connection, to save.
    public func refreshCentres() async throws -> Connection {
        struct Answer: Decodable { let centres: [Centre]? }
        let body = try await send(VaultRequest("GET", "/api/mobile/addresses")).body
        guard let centres = try JSONDecoder().decode(Answer.self, from: body).centres else { return connection }  // before 1.0
        return lock.withLock {
            current.others = centres.filter { $0.fingerprint.count == 64 && $0.fingerprint != current.fingerprint }
            return current
        }
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
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 15
        let session = URLSession(configuration: configuration, delegate: Pinning(expected: fingerprint), delegateQueue: nil)
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
        let paired = RemoteVault(Connection(urls: [base], fingerprint: fingerprint, token: token, name: base, others: nil))
        let answer = try await paired.send(VaultRequest("GET", "/api/mobile/addresses"))
        let urls = ((try? JSONSerialization.jsonObject(with: answer.body)) as? [String: Any])?["urls"] as? [String] ?? []
        return Connection(urls: [base] + urls.filter { $0 != base }, fingerprint: fingerprint, token: token, name: base, others: nil)
    }

    // MARK: - VaultTransport

    public func send(_ request: VaultRequest) async throws -> VaultResponse {
        var last: Error?
        for url in candidates {  // the address that answers: home Wi-Fi, Tailscale, another computer that keeps the vault
            do {
                let (data, response) = try await session.data(for: try build(url, request))
                let http = response as? HTTPURLResponse
                var headers: [String: String] = [:]
                http?.allHeaderFields.forEach { if let k = $0.key as? String, let v = $0.value as? String { headers[k.lowercased()] = v } }
                remember(url)
                return VaultResponse(status: http?.statusCode ?? 0, headers: headers, body: data)
            } catch let error as URLError where error.code == .timedOut && request.method == "GET" {
                last = error  // only reading: another address (another computer that keeps the vault) may answer
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
                    let (bytes, response) = try await session.bytes(for: try build(candidates[0], request))
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
        lock.withLock { preferred = url }
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

}

/// Checks the vault's certificate by its SHA-256 fingerprint; with no expected one (pairing), it only records it.
final class Pinning: NSObject, URLSessionDelegate, @unchecked Sendable {
    let expected: String?
    let trusted: ((String) -> Bool)?
    private(set) var seen: String?

    init(expected: String?) { self.expected = expected; trusted = nil }
    init(trusted: @escaping (String) -> Bool) { expected = nil; self.trusted = trusted }

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
        let ok = trusted.map { $0(fingerprint) } ?? (expected == nil || expected == fingerprint)
        if ok {
            completionHandler(.useCredential, URLCredential(trust: trust))
        } else {
            completionHandler(.cancelAuthenticationChallenge, nil)
        }
    }
}
