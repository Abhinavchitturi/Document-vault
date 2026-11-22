#if os(macOS)
import Foundation
import Testing
@testable import VaultKit

/// A tiny engine archive like the real one: python/bin/python3 (a link, as in the real Python) and the code.
private func makeEngine(in dir: URL, _ content: String) throws -> (archive: URL, sha: String, size: Int64) {
    let source = dir.appending(path: "source-\(content)")
    try FileManager.default.createDirectory(at: source.appending(path: "python/bin"), withIntermediateDirectories: true)
    try "#!/bin/sh\necho \(content)\n".write(to: source.appending(path: "python/bin/python3.11"), atomically: true, encoding: .utf8)
    try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: source.appending(path: "python/bin/python3.11").path)
    try FileManager.default.createSymbolicLink(atPath: source.appending(path: "python/bin/python3").path, withDestinationPath: "python3.11")
    try FileManager.default.createDirectory(at: source.appending(path: "backend"), withIntermediateDirectories: true)
    try "print('hello')".write(to: source.appending(path: "backend/engine.py"), atomically: true, encoding: .utf8)
    let archive = dir.appending(path: "engine-\(content).tar.xz")
    let tar = Process()
    tar.executableURL = URL(fileURLWithPath: "/usr/bin/tar")
    tar.arguments = ["-cJf", archive.path, "-C", source.path, "."]
    try tar.run()
    tar.waitUntilExit()
    let size = try FileManager.default.attributesOfItem(atPath: archive.path)[.size] as! Int64
    return (archive, try EngineInstaller.sha256(of: archive), size)
}

private final class Steps: @unchecked Sendable {
    private let lock = NSLock()
    private var items: [EngineProgress] = []
    func add(_ step: EngineProgress) { lock.lock(); items.append(step); lock.unlock() }
    var all: [EngineProgress] { lock.lock(); defer { lock.unlock() }; return items }
}

@Test func setsUpTheEngineOnceAndReplacesItForANewVersion() async throws {
    let dir = FileManager.default.temporaryDirectory.appending(path: "engine-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: dir) }
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let (archive, sha, size) = try makeEngine(in: dir, "v1")
    let release = EngineRelease(version: "1.0", url: "https://example.invalid/e.tar.xz", sha256: sha, size: size)
    let folder = dir.appending(path: "Engine")
    #expect(!EngineInstaller.isInstalled(folder, release))

    let steps = Steps()
    try await EngineInstaller.install(release, into: folder, archive: archive) { steps.add($0) }
    #expect(EngineInstaller.isInstalled(folder, release))
    #expect(try String(contentsOf: folder.appending(path: "backend/engine.py"), encoding: .utf8) == "print('hello')")
    #expect(steps.all.contains { $0.step == "downloading" && $0.done == size })
    #expect(steps.all.contains { $0.step == "unpacking" })

    let (archive2, sha2, size2) = try makeEngine(in: dir, "v2")
    let newer = EngineRelease(version: "1.1", url: release.url, sha256: sha2, size: size2)
    #expect(!EngineInstaller.isInstalled(folder, newer))
    try await EngineInstaller.install(newer, into: folder, archive: archive2) { _ in }
    #expect(EngineInstaller.isInstalled(folder, newer))
    #expect(try String(contentsOf: folder.appending(path: "python/bin/python3"), encoding: .utf8).contains("v2"))
}

@Test func refusesAnEngineThatDoesNotMatch() async throws {
    let dir = FileManager.default.temporaryDirectory.appending(path: "engine-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: dir) }
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let (archive, _, size) = try makeEngine(in: dir, "v1")
    let release = EngineRelease(version: "1.0", url: "https://example.invalid/e.tar.xz", sha256: String(repeating: "0", count: 64), size: size)
    let folder = dir.appending(path: "Engine")
    await #expect(throws: VaultError.self) { try await EngineInstaller.install(release, into: folder, archive: archive) { _ in } }
    #expect(!FileManager.default.fileExists(atPath: folder.path))
}

@Test func downloadsItWithProgress() async throws {
    let dir = FileManager.default.temporaryDirectory.appending(path: "engine-\(UUID().uuidString)")
    defer { try? FileManager.default.removeItem(at: dir) }
    try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    let (archive, sha, size) = try makeEngine(in: dir, "v1")
    // a small web server on this Mac, serving the archive
    let server = Process()
    server.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
    let port = Int.random(in: 20000...40000)
    server.arguments = ["-m", "http.server", "\(port)", "--bind", "127.0.0.1", "--directory", dir.path]
    server.standardError = FileHandle.nullDevice
    server.standardOutput = FileHandle.nullDevice
    try server.run()
    defer { server.terminate() }
    var up = false  // wait until it answers (slow on a busy build machine)
    for _ in 0..<60 where !up {
        try await Task.sleep(for: .milliseconds(500))
        up = (try? await URLSession.shared.data(from: URL(string: "http://127.0.0.1:\(port)/")!)) != nil
    }
    #expect(up)
    let release = EngineRelease(version: "1.0", url: "http://127.0.0.1:\(port)/\(archive.lastPathComponent)", sha256: sha, size: size)
    let folder = dir.appending(path: "Engine")
    let steps = Steps()
    try await EngineInstaller.install(release, into: folder) { steps.add($0) }
    #expect(EngineInstaller.isInstalled(folder, release))
    #expect(steps.all.contains { $0.step == "downloading" && $0.done == size })
}
#endif
