import AppKit
import SwiftUI
import VaultKit

/// "Document Vault 0.9.2 is available": across the top of the window when the releases page has a newer version
/// (checked at start and every hour). The person chooses: Restart & Update (download, swap, reopen), Update When
/// I Quit (download now, swap in when the app quits) or Later. The DMG is checked against the SHA-256 GitHub publishes;
/// the new app takes this one's place (same name, same folder, this Mac's answer kept, documents untouched), and the
/// DMG is deleted.
struct UpdateBanner: View {
    @Environment(AppModel.self) private var model
    @State private var update: AppUpdate?
    @State private var hidden = false
    @State private var working: String?
    @State private var waiting = false  // downloaded: goes in when the app quits
    @State private var problem: String?

    /// This app's version, or nil in development (no checking then).
    static var version: String? {
        AppModel.release == nil ? nil : Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
    }

    var body: some View {
        VStack(spacing: 0) {  // a real view even when empty: an empty Group never ran its .task, so nothing was checked
            Color.clear.frame(height: 0)  // always there, so the checks below always run
            if let update, !hidden {
                HStack(spacing: 12) {
                    Image(systemName: "arrow.down.app.fill").foregroundStyle(Color.vault).font(.title3)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(working ?? (waiting ? "Document Vault \(update.version) will be installed when you quit."
                                                 : "Document Vault \(update.version) is available."))
                            .font(.callout.weight(.semibold))
                        if let problem { Text(problem).font(.caption).foregroundStyle(.red) }
                    }
                    Spacer()
                    if working != nil {
                        ProgressView().controlSize(.small)
                    } else if waiting {
                        Button("Restart & Update Now") { Task { await get(update, now: true) } }.buttonStyle(.borderedProminent)
                    } else {
                        Button("Later") { hidden = true }
                        Button("Update When I Quit") { Task { await get(update, now: false) } }
                        Button("Restart & Update") { Task { await get(update, now: true) } }.buttonStyle(.borderedProminent)
                    }
                }
                .padding(.horizontal, 20).padding(.vertical, 10)
                .background(.bar)
                .overlay(alignment: .bottom) { Divider() }
            }
        }
        .task {
            SelfUpdate.cleanUp()
            guard let current = Self.version else { return }
            await Updates.countThisComputerOnce()  // one more computer with the app (once per computer, ever)
            while !Task.isCancelled {
                if update == nil { update = await Updates.check(current: current, asset: "DocumentVault-macOS.dmg") }
                try? await Task.sleep(for: .seconds(3600))  // every hour
            }
        }
    }

    private func get(_ update: AppUpdate, now: Bool) async {
        problem = nil
        do {
            if SelfUpdate.staged == nil {
                working = "Downloading Document Vault \(update.version)…"
                let dmg = FileManager.default.temporaryDirectory.appending(path: "DocumentVault-\(update.version).dmg")
                try await Updates.download(update, to: dmg)
                working = "Getting Document Vault \(update.version) ready…"
                try await Task.detached { try SelfUpdate.stage(from: dmg) }.value  // and the DMG is deleted
            }
            working = nil
            if !now {
                waiting = true  // AppModel.quit swaps it in
                return
            }
            working = "Installing Document Vault \(update.version)…"
            try SelfUpdate.swapAfterQuit(reopen: true)
            NSApp.terminate(nil)  // the new app opens when this one has quit
        } catch {
            working = nil
            problem = "The update didn't work: \(error.localizedDescription)"
        }
    }
}

enum SelfUpdate {
    /// The new app, copied out of the DMG next to this one, waiting to take its place.
    nonisolated(unsafe) static var staged: URL?

    /// What an earlier update left behind (an interrupted download or copy): removed.
    static func cleanUp() {
        guard staged == nil else { return }
        let files = FileManager.default
        try? files.removeItem(at: Bundle.main.bundleURL.deletingLastPathComponent().appending(path: ".Document Vault (update).app"))
        let temp = files.temporaryDirectory
        for name in (try? files.contentsOfDirectory(atPath: temp.path)) ?? []
        where name.hasPrefix("DocumentVault-") && name.hasSuffix(".dmg") {
            try? files.removeItem(at: temp.appending(path: name))
        }
    }

    /// Copy the new app out of the DMG next to this one (hidden), then delete the DMG.
    static func stage(from dmg: URL) throws {
        defer { try? FileManager.default.removeItem(at: dmg) }
        let folder = Bundle.main.bundleURL.deletingLastPathComponent()
        guard FileManager.default.isWritableFile(atPath: folder.path) else {
            throw VaultError.engine("This Mac doesn't let Document Vault replace itself in \(folder.path). Download the new version "
                                    + "from github.com/Abhinavchitturi/Document-vault/releases and drag it to Applications.")
        }
        let mount = FileManager.default.temporaryDirectory.appending(path: "DocumentVault-update-\(UUID().uuidString)")
        try run("/usr/bin/hdiutil", ["attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mount.path, dmg.path])
        defer { try? run("/usr/bin/hdiutil", ["detach", mount.path, "-force"]) }
        let copy = folder.appending(path: ".Document Vault (update).app")
        try? FileManager.default.removeItem(at: copy)
        try run("/usr/bin/ditto", [mount.appending(path: "Document Vault.app").path, copy.path])
        staged = copy
    }

    /// Once this app has quit: the new app takes its place (and opens, for Restart & Update). The updated app keeps
    /// this Mac's answer to "keep the vault here, or connect?".
    static func swapAfterQuit(reopen: Bool) throws {
        guard let staged else { return }
        let app = Bundle.main.bundleURL
        let pid = ProcessInfo.processInfo.processIdentifier
        let script = """
            while kill -0 \(pid) 2>/dev/null; do sleep 0.3; done
            rm -rf "\(app.path)" && mv "\(staged.path)" "\(app.path)"\(reopen ? " && open \"\(app.path)\"" : "")
            """
        let swap = Process()
        swap.executableURL = URL(fileURLWithPath: "/bin/sh")
        swap.arguments = ["-c", script]
        try swap.run()  // keeps running after this app quits
        UserDefaults.standard.set(true, forKey: "keepAnswerAcrossUpdate")
        Self.staged = nil
    }

    private static func run(_ tool: String, _ arguments: [String]) throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: tool)
        process.arguments = arguments
        process.standardOutput = FileHandle.nullDevice
        let errors = Pipe()
        process.standardError = errors
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            let why = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            throw VaultError.engine("\(URL(fileURLWithPath: tool).lastPathComponent) failed: \(why.trimmingCharacters(in: .whitespacesAndNewlines))")
        }
    }
}
