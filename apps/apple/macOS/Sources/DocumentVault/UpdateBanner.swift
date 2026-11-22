import AppKit
import SwiftUI
import VaultKit

/// "Document Vault 0.9.2 is available": across the top of the window when the releases page has a newer version
/// (checked at start and every few hours). Update downloads the new DMG, checks it against the SHA-256 GitHub
/// publishes, puts the new app in place of this one (this Mac's answer kept, the documents untouched) and reopens.
struct UpdateBanner: View {
    @Environment(AppModel.self) private var model
    @State private var update: AppUpdate?
    @State private var hidden = false
    @State private var working: String?
    @State private var problem: String?

    /// This app's version, or nil in development (no checking then).
    static var version: String? {
        AppModel.release == nil ? nil : Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
    }

    var body: some View {
        Group {
            if let update, !hidden {
                HStack(spacing: 12) {
                    Image(systemName: "arrow.down.app.fill").foregroundStyle(Color.vault).font(.title3)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(working ?? "Document Vault \(update.version) is available.").font(.callout.weight(.semibold))
                        if let problem { Text(problem).font(.caption).foregroundStyle(.red) }
                    }
                    Spacer()
                    if working != nil {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Later") { hidden = true }
                        Button("Update") { Task { await install(update) } }.buttonStyle(.borderedProminent)
                    }
                }
                .padding(.horizontal, 20).padding(.vertical, 10)
                .background(.bar)
                .overlay(alignment: .bottom) { Divider() }
            }
        }
        .task {
            guard let current = Self.version else { return }
            while !Task.isCancelled {
                if update == nil { update = await Updates.check(current: current, asset: "DocumentVault-macOS.dmg") }
                try? await Task.sleep(for: .seconds(6 * 3600))
            }
        }
    }

    private func install(_ update: AppUpdate) async {
        problem = nil
        working = "Downloading Document Vault \(update.version)…"
        let dmg = FileManager.default.temporaryDirectory.appending(path: "DocumentVault-\(update.version).dmg")
        do {
            try await Updates.download(update, to: dmg)
            working = "Installing Document Vault \(update.version)…"
            try await Task.detached { try SelfUpdate.replaceApp(with: dmg) }.value
            model.stop()
            UserDefaults.standard.set(true, forKey: "keepAnswerAcrossUpdate")  // the updated app keeps this Mac's answer
            NSApp.terminate(nil)  // the new app opens when this one has quit
        } catch {
            working = nil
            problem = "The update didn't work: \(error.localizedDescription)"
        }
    }
}

enum SelfUpdate {
    /// Copy the new app out of the DMG next to this one, then, once this app has quit, swap them and open the new one.
    static func replaceApp(with dmg: URL) throws {
        let app = Bundle.main.bundleURL
        let folder = app.deletingLastPathComponent()
        guard FileManager.default.isWritableFile(atPath: folder.path) else {
            NSWorkspace.shared.open(dmg)
            throw VaultError.engine("This Mac doesn't let Document Vault replace itself in \(folder.path). The new version is open: drag it to Applications.")
        }
        let mount = FileManager.default.temporaryDirectory.appending(path: "DocumentVault-update-\(UUID().uuidString)")
        try run("/usr/bin/hdiutil", ["attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", mount.path, dmg.path])
        defer { try? run("/usr/bin/hdiutil", ["detach", mount.path, "-force"]) }
        let staged = folder.appending(path: ".Document Vault (update).app")
        try? FileManager.default.removeItem(at: staged)
        try run("/usr/bin/ditto", [mount.appending(path: "Document Vault.app").path, staged.path])
        let pid = ProcessInfo.processInfo.processIdentifier
        let script = """
            while kill -0 \(pid) 2>/dev/null; do sleep 0.3; done
            rm -rf "\(app.path)" && mv "\(staged.path)" "\(app.path)" && open "\(app.path)"
            """
        let swap = Process()
        swap.executableURL = URL(fileURLWithPath: "/bin/sh")
        swap.arguments = ["-c", script]
        try swap.run()  // keeps running after this app quits
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
