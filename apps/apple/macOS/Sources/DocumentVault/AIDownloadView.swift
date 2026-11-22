import SwiftUI
import VaultKit

/// The one-click "Download the AI" button, with its progress, wherever answers aren't available yet.
struct AIDownloadView: View {
    @Environment(AppModel.self) private var model
    @State private var status: AIDownloadStatus?
    @State private var problem: String?

    private var canDownload: Bool { model.info?.llmStatus.canDownload == true }
    private var downloading: Bool { status?.state == "downloading" }

    var body: some View {
        if canDownload || downloading {
            VStack(alignment: .leading, spacing: 8) {
                if downloading, let status {
                    let share = status.total > 0 ? Double(status.done) / Double(status.total) : 0
                    ProgressView(value: share) {
                        Text("Downloading \(status.step == "model" ? "the answer model" : "llama.cpp")… \(Int(share * 100))%")
                            .font(.callout)
                    } currentValueLabel: {
                        Text("You can keep using the vault; answers start by themselves when it's done.").font(.caption)
                    }
                } else if model.user?.isAdmin == true {
                    HStack(spacing: 10) {
                        Button { start() } label: { Label("Download the AI (about 5 GB)", systemImage: "arrow.down.circle.fill") }
                            .buttonStyle(.borderedProminent)
                        Text("Once. After that it works offline, on this Mac.").font(.caption).foregroundStyle(.secondary)
                    }
                } else {
                    Text("Ask the vault's admin to download the AI in Settings.").font(.caption).foregroundStyle(.secondary)
                }
                if let error = problem ?? (status?.state == "failed" ? status?.error : nil) {
                    Label(error, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(.red)
                }
            }
            .task(id: canDownload) { await follow() }
        }
    }

    private func start() {
        problem = nil
        Task {
            do {
                status = try await model.client?.startAIDownload()
                await follow()
            } catch {
                problem = error.localizedDescription
            }
        }
    }

    /// Check every second while it downloads; reload the vault's state when it's done.
    private func follow() async {
        while !Task.isCancelled {
            guard let next = try? await model.client?.aiDownloadStatus() else { return }
            status = next
            if next.state == "done" { await model.reload(); return }
            if next.state != "downloading" { return }
            try? await Task.sleep(for: .seconds(1))
        }
    }
}
