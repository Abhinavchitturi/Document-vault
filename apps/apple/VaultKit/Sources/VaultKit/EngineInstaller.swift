#if os(macOS)
import CryptoKit
import Foundation

/// The engine this app version needs (engine.json in the app, written when the DMG is built): where to download it
/// and its SHA-256. A Mac that only connects to another computer's vault never downloads it.
public struct EngineRelease: Codable, Sendable, Equatable {
    public let version: String
    public let url: String
    public let sha256: String
    public let size: Int64

    public init(version: String, url: String, sha256: String, size: Int64) {
        self.version = version
        self.url = url
        self.sha256 = sha256
        self.size = size
    }
}

/// How far setting up the engine is: "downloading" (done of total bytes) or "unpacking".
public struct EngineProgress: Sendable, Equatable {
    public let step: String
    public let done: Int64
    public let total: Int64
}

/// Downloads the vault's engine (a private Python with the vault's packages, its code, Tesseract), checks it against
/// the SHA-256 the app was built with, and unpacks it. Only when this Mac keeps the vault (it is the centre).
public enum EngineInstaller {
    static let marker = ".engine-sha256"

    public static func load(_ file: URL) -> EngineRelease? {
        guard let data = try? Data(contentsOf: file) else { return nil }
        return try? JSONDecoder().decode(EngineRelease.self, from: data)
    }

    /// The engine in `folder` is complete and is the one this app version needs.
    public static func isInstalled(_ folder: URL, _ release: EngineRelease) -> Bool {
        guard FileManager.default.isExecutableFile(atPath: folder.appending(path: "python/bin/python3").path),
              let installed = try? String(contentsOf: folder.appending(path: marker), encoding: .utf8) else { return false }
        return installed.trimmingCharacters(in: .whitespacesAndNewlines) == release.sha256
    }

    /// Download (or, for tests and CI, copy from `archive`), check, unpack into `folder`, replacing an older engine.
    /// The documents are never in this folder, so replacing it touches nothing of the person's.
    public static func install(_ release: EngineRelease, into folder: URL, archive: URL? = nil,
                               progress: @escaping @Sendable (EngineProgress) -> Void) async throws {
        let files = FileManager.default
        let parent = folder.deletingLastPathComponent()
        try files.createDirectory(at: parent, withIntermediateDirectories: true)
        let download = parent.appending(path: "engine-download.tar.xz")
        let unpacked = parent.appending(path: folder.lastPathComponent + ".new")
        defer {
            try? files.removeItem(at: download)
            try? files.removeItem(at: unpacked)
        }
        try? files.removeItem(at: download)
        if let archive {
            try files.copyItem(at: archive, to: download)
            let size = (try? files.attributesOfItem(atPath: download.path)[.size] as? Int64) ?? release.size
            progress(EngineProgress(step: "downloading", done: size, total: size))
        } else {
            guard let url = URL(string: release.url) else { throw VaultError.engine("The engine's address is wrong.") }
            try await Downloader.fetch(url, to: download, expected: release.size) { done, total in
                progress(EngineProgress(step: "downloading", done: done, total: total))
            }
        }
        guard try sha256(of: download) == release.sha256.lowercased() else {
            throw VaultError.engine("The downloaded engine doesn't match this version of the app, so it wasn't used. Try again, "
                                    + "or install the latest Document Vault from github.com/Abhinavchitturi/Document-vault/releases.")
        }
        progress(EngineProgress(step: "unpacking", done: 0, total: 0))
        try? files.removeItem(at: unpacked)
        try files.createDirectory(at: unpacked, withIntermediateDirectories: true)
        try await untar(download, into: unpacked)
        try release.sha256.write(to: unpacked.appending(path: marker), atomically: true, encoding: .utf8)
        try? files.removeItem(at: folder)
        try files.moveItem(at: unpacked, to: folder)
    }

    static func sha256(of file: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: file)
        defer { try? handle.close() }
        var hash = SHA256()
        while let chunk = try handle.read(upToCount: 4 << 20), !chunk.isEmpty { hash.update(data: chunk) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    /// macOS's own tar (it reads .tar.xz and keeps the links inside Python's folders).
    static func untar(_ archive: URL, into folder: URL) async throws {
        try await Task.detached {
            let tar = Process()
            tar.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
            tar.arguments = ["-xJf", archive.path, "-C", folder.path]
            let errors = Pipe()
            tar.standardError = errors
            try tar.run()
            tar.waitUntilExit()
            guard tar.terminationStatus == 0 else {
                let why = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                throw VaultError.engine("Couldn't unpack the vault's engine: \(why.trimmingCharacters(in: .whitespacesAndNewlines))")
            }
        }.value
    }
}

/// A download with progress, to a file.
private final class Downloader: NSObject, URLSessionDownloadDelegate, @unchecked Sendable {
    private let destination: URL
    private let expected: Int64
    private let progress: @Sendable (Int64, Int64) -> Void
    private var continuation: CheckedContinuation<Void, Error>?
    private var problem: Error?

    private init(destination: URL, expected: Int64, progress: @escaping @Sendable (Int64, Int64) -> Void) {
        self.destination = destination
        self.expected = expected
        self.progress = progress
    }

    static func fetch(_ url: URL, to destination: URL, expected: Int64,
                      progress: @escaping @Sendable (Int64, Int64) -> Void) async throws {
        let downloader = Downloader(destination: destination, expected: expected, progress: progress)
        let session = URLSession(configuration: .default, delegate: downloader, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                downloader.continuation = continuation
                session.downloadTask(with: url).resume()
            }
        } onCancel: {
            session.invalidateAndCancel()
        }
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didWriteData bytesWritten: Int64,
                    totalBytesWritten: Int64, totalBytesExpectedToWrite: Int64) {
        progress(totalBytesWritten, totalBytesExpectedToWrite > 0 ? totalBytesExpectedToWrite : expected)
    }

    func urlSession(_ session: URLSession, downloadTask: URLSessionDownloadTask, didFinishDownloadingTo location: URL) {
        if let http = downloadTask.response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            problem = VaultError.engine("Couldn't download the vault's engine (the server answered \(http.statusCode)).")
            return
        }
        do {
            try? FileManager.default.removeItem(at: destination)
            try FileManager.default.moveItem(at: location, to: destination)  // before this method returns
        } catch {
            problem = error
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        if let error {
            continuation?.resume(throwing: VaultError.engine(
                "Couldn't download the vault's engine (\(error.localizedDescription)). Check the internet connection and try again."))
        } else if let problem {
            continuation?.resume(throwing: problem)
        } else {
            continuation?.resume()
        }
        continuation = nil
    }
}
#endif
