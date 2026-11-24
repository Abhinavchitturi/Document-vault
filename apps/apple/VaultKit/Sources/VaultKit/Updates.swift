import CryptoKit
import Foundation

/// A newer version of the app on the releases page: its DMG (or installer), and the SHA-256 GitHub publishes for it.
public struct AppUpdate: Sendable, Equatable {
    public let version: String
    public let url: URL
    public let sha256: String
    public let size: Int64
}

/// Checks the project's releases for a newer app, downloads it and checks it against GitHub's SHA-256 (over HTTPS from
/// GitHub's API). The documents and the engine are untouched; a new app that needs a new engine downloads it itself.
public enum Updates {
    public static let releases = URL(string: "https://api.github.com/repos/Abhinavchitturi/Document-vault/releases?per_page=20")!

    private struct Release: Decodable {
        let tag_name: String
        let draft: Bool
        let assets: [Asset]
    }

    private struct Asset: Decodable {
        let name: String
        let size: Int64
        let browser_download_url: URL
        let digest: String?
    }

    /// Newer than `current`, from GitHub's releases list (drafts left out), with a checkable file named `asset`.
    public static func newest(in json: Data, current: String, asset: String) -> AppUpdate? {
        guard let releases = try? JSONDecoder().decode([Release].self, from: json) else { return nil }
        var best: AppUpdate?
        for release in releases where !release.draft {
            let version = release.tag_name.hasPrefix("v") ? String(release.tag_name.dropFirst()) : release.tag_name
            guard compare(version, current) > 0, best.map({ compare(version, $0.version) > 0 }) ?? true,
                  let file = release.assets.first(where: { $0.name == asset }),
                  let digest = file.digest, digest.hasPrefix("sha256:") else { continue }  // nothing to check it against
            best = AppUpdate(version: version, url: file.browser_download_url, sha256: String(digest.dropFirst(7)), size: file.size)
        }
        return best
    }

    /// 0.9.1 > 0.9.0 > 0.9.0-beta: numbers first; a version without a suffix is newer than the same with one.
    public static func compare(_ a: String, _ b: String) -> Int {
        func parse(_ v: String) -> ([Int], String) {
            let parts = v.split(separator: "-", maxSplits: 1).map(String.init)
            return (parts[0].split(separator: ".").map { Int($0) ?? 0 }, parts.count > 1 ? parts[1] : "")
        }
        let (x, xs) = parse(a), (y, ys) = parse(b)
        for i in 0..<max(x.count, y.count) {
            let l = i < x.count ? x[i] : 0, r = i < y.count ? y[i] : 0
            if l != r { return l < r ? -1 : 1 }
        }
        if xs == ys { return 0 }
        if xs.isEmpty { return 1 }
        if ys.isEmpty { return -1 }
        return xs < ys ? -1 : 1
    }

    /// Ask GitHub (or $DOCUMENT_VAULT_UPDATES_URL, for tests) whether there is a newer version. Nil when there isn't,
    /// or when GitHub can't be reached (checked again later).
    public static func check(current: String, asset: String) async -> AppUpdate? {
        let url = ProcessInfo.processInfo.environment["DOCUMENT_VAULT_UPDATES_URL"].flatMap(URL.init(string:)) ?? releases
        var request = URLRequest(url: url, timeoutInterval: 20)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              (response as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return newest(in: data, current: current, asset: asset)
    }

    /// How many computers have the app: each one downloads this one-byte file from GitHub once, ever, the first time the
    /// app opens, and GitHub counts the file's downloads. Nothing about the person or their documents goes with it.
    public static let newComputer = URL(string: "https://github.com/Abhinavchitturi/Document-vault/releases/download/counter/new-computer.txt")!

    /// Counts this computer, once (remembered in its preferences, which a reinstall keeps). Not reached: next start.
    public static func countThisComputerOnce(defaults: UserDefaults = .standard) async {
        let key = "countedThisComputer"
        guard !defaults.bool(forKey: key) else { return }
        guard let (_, response) = try? await URLSession.shared.data(for: URLRequest(url: newComputer, timeoutInterval: 20)),
              (response as? HTTPURLResponse)?.statusCode == 200 else { return }
        defaults.set(true, forKey: key)
    }

    /// Download the update to `destination` and check it against GitHub's SHA-256; a file that doesn't match is deleted.
    public static func download(_ update: AppUpdate, to destination: URL) async throws {
        let (temporary, response) = try await URLSession.shared.download(from: update.url)
        guard (response as? HTTPURLResponse).map({ (200..<300).contains($0.statusCode) }) ?? true else {
            throw VaultError.engine("Couldn't download the update.")
        }
        try? FileManager.default.removeItem(at: destination)
        try FileManager.default.moveItem(at: temporary, to: destination)
        let handle = try FileHandle(forReadingFrom: destination)
        defer { try? handle.close() }
        var hash = SHA256()
        while let chunk = try handle.read(upToCount: 4 << 20), !chunk.isEmpty { hash.update(data: chunk) }
        guard hash.finalize().map({ String(format: "%02x", $0) }).joined() == update.sha256.lowercased() else {
            try? FileManager.default.removeItem(at: destination)
            throw VaultError.engine("The downloaded update doesn't match the one published on GitHub, so it wasn't installed.")
        }
    }
}
