import SwiftUI
import VaultKit

/// Across the top of the window while the search model downloads (the first start only) and loads: a progress bar,
/// so it's clear why searching, asking and adding documents wait. It goes away by itself when search is ready.
struct SearchReadyBanner: View {
    @Environment(AppModel.self) private var model
    @State private var status: SearchStatus?

    var body: some View {
        Group {
            if let status, status.state != "ready" {
                VStack(alignment: .leading, spacing: 6) {
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
                            ProgressView { Text("Getting search ready…").font(.callout.weight(.semibold)) }
                                .progressViewStyle(.linear)
                        }
                        Text("You can look around meanwhile; searching, asking and adding documents start when it's ready.")
                            .font(.caption).foregroundStyle(.secondary)
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

    private func follow() async {
        while !Task.isCancelled {
            guard let next = try? await model.client?.searchStatus() else { status = nil; return }  // older vault
            withAnimation { status = next }
            if next.state == "ready" || next.state == "failed" { return }
            try? await Task.sleep(for: .milliseconds(700))
        }
    }
}
