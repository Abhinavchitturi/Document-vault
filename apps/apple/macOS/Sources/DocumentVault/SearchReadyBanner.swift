import SwiftUI
import VaultKit

/// Across the top of the window while the vault gets ready on its first start: the search model downloading and
/// loading, then the private AI downloading by itself (about 5 GB, once), each with a progress bar, so it's clear why
/// searching or answers wait. It goes away by itself when both are ready.
struct SearchReadyBanner: View {
    @Environment(AppModel.self) private var model
    @State private var search: SearchStatus?
    @State private var ai: AIDownloadStatus?

    private enum Shown { case search(SearchStatus), ai(AIDownloadStatus) }

    private var shown: Shown? {
        if let search, search.state != "ready" { return .search(search) }
        if let ai, ai.state == "downloading" || ai.state == "failed" { return .ai(ai) }
        return nil
    }

    var body: some View {
        Group {
            if let shown {
                VStack(alignment: .leading, spacing: 6) {
                    switch shown {
                    case .search(let status): searchView(status)
                    case .ai(let status): aiView(status)
                    }
                }
                .padding(.horizontal, 20).padding(.vertical, 12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.bar)
                .overlay(alignment: .bottom) { Divider() }
            }
        }
        .task { await follow() }
    }

    @ViewBuilder private func searchView(_ status: SearchStatus) -> some View {
        if status.state == "failed" {
            Label("Search couldn't get ready", systemImage: "exclamationmark.triangle").font(.callout.weight(.semibold))
            Text(status.error ?? "").font(.caption).foregroundStyle(.secondary)
        } else {
            if status.state == "downloading", status.total > 0 {
                ProgressView(value: Double(status.done), total: Double(status.total)) {
                    Text("Getting search ready: downloading the search model, \(status.done / 1_000_000) of \(status.total / 1_000_000) MB (once)")
                        .font(.callout.weight(.semibold))
                }
            } else {
                ProgressView { Text("Getting search ready…").font(.callout.weight(.semibold)) }.progressViewStyle(.linear)
            }
            Text("You can look around meanwhile; searching, asking and adding documents start when it's ready.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private func aiView(_ status: AIDownloadStatus) -> some View {
        if status.state == "failed" {
            Label("The private AI didn't finish downloading", systemImage: "exclamationmark.triangle").font(.callout.weight(.semibold))
            Text(status.error ?? "").font(.caption).foregroundStyle(.secondary)
            if model.user?.isAdmin == true {
                Button("Try Again") {
                    Task {
                        ai = try? await model.client?.startAIDownload()
                        await followAI()
                    }
                }
                .buttonStyle(.borderedProminent)
            }
        } else {
            let step = status.step == "model" ? "the private AI" : "the AI's program"
            if status.total > 0 {
                ProgressView(value: Double(status.done), total: Double(status.total)) {
                    Text("Downloading \(step): \(String(format: "%.1f", Double(status.done) / 1e9)) of \(String(format: "%.1f", Double(status.total) / 1e9)) GB (once)")
                        .font(.callout.weight(.semibold))
                }
            } else {
                ProgressView { Text("Downloading \(step)…").font(.callout.weight(.semibold)) }.progressViewStyle(.linear)
            }
            Text("Everything else works meanwhile; the assistant answers as soon as it's done.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }

    private func follow() async {
        while !Task.isCancelled {
            guard let next = try? await model.client?.searchStatus() else { search = nil; return }  // older vault
            withAnimation { search = next }
            if next.state == "failed" { return }
            if next.state == "ready" { break }
            try? await Task.sleep(for: .milliseconds(700))
        }
        await followAI()
    }

    /// The local AI, which the vault downloads by itself after the search model: answers start when it's done.
    private func followAI() async {
        var waited = 0
        while !Task.isCancelled {
            guard let next = try? await model.client?.aiDownloadStatus() else { ai = nil; return }  // nothing to download
            withAnimation { ai = next }
            switch next.state {
            case "done":
                await model.reload()  // the assistant is ready now
                return
            case "failed":
                return
            case "downloading":
                waited = 0
            default:  // about to start (it waits for the search model), or not starting by itself here
                if model.info?.llmStatus.ready == true || model.info?.llmStatus.canDownload != true { return }
                waited += 1
                if waited > 20 { return }
            }
            try? await Task.sleep(for: .seconds(1))
        }
    }
}
