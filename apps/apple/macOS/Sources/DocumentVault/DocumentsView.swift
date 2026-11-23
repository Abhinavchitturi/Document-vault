import SwiftUI
import UniformTypeIdentifiers
import VaultKit

/// Every document (or one type): a calm list, newest first. Click one to open it; the trash button (on hover) or the
/// right-click menu removes it. Adding documents works from anywhere (AddDocuments.swift).
struct DocumentsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    let category: Category?
    @State private var search = ""
    @State private var toDelete: VaultDocument?

    private var shown: [VaultDocument] {
        model.documents.filter(matches).sorted { $0.addedAt > $1.addedAt }
    }

    private func matches(_ document: VaultDocument) -> Bool {
        if let category, document.category != category { return false }
        if search.isEmpty { return true }
        let fields: [String] = [document.filename, document.reference ?? "", document.issuer ?? ""]
        return fields.contains { $0.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 8) {
                ForEach(shown) { document in
                    DocumentCard(document: document,
                                 open: { openWindow(id: "document", value: document.docId) },
                                 remove: { toDelete = document })
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 18)
            .frame(maxWidth: 900)
            .frame(maxWidth: .infinity)
        }
        .searchable(text: $search, prompt: "Name, policy or reference number")
        .navigationTitle(category?.label ?? "All Documents")
        .navigationSubtitle("\(shown.count) document\(shown.count == 1 ? "" : "s")")
        .overlay {
            if model.documents.isEmpty {
                ContentUnavailableView {
                    Label("No documents yet", systemImage: "tray")
                } description: {
                    Text("Drop PDFs, Word files or photos here, or click Add Documents.")
                } actions: {
                    Button("Add Documents") { model.choosingFiles = true }.buttonStyle(.borderedProminent)
                }
            } else if shown.isEmpty {
                ContentUnavailableView.search(text: search)
            }
        }
        .confirmationDialog("Remove \(toDelete?.filename ?? "") from the vault?",
                            isPresented: Binding(get: { toDelete != nil }, set: { if !$0 { toDelete = nil } })) {
            Button("Remove", role: .destructive) {
                if let document = toDelete { Task { await model.delete(document) } }
            }
        } message: {
            Text("The vault's encrypted copy and its search data are deleted, on every computer that keeps this vault. Your original file, if you still have it, isn't touched.")
        }
    }
}

/// One document: its type's icon, its name, and one quiet line of details. The trash button shows on hover.
struct DocumentCard: View {
    @Environment(AppModel.self) private var model
    let document: VaultDocument
    let open: () -> Void
    let remove: () -> Void
    @State private var hovering = false

    private var details: String {
        var parts = [document.category.label]
        if let key = Format.keyFact(document) { parts.append(key.text) } else { parts.append("Added \(Format.date(document.addedAt))") }
        parts.append(Format.size(document.sizeBytes))
        return parts.joined(separator: "  ·  ")
    }

    var body: some View {
        HStack(spacing: 14) {
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(document.category.color.opacity(0.15))
                .frame(width: 38, height: 38)
                .overlay(Image(systemName: document.category.symbol).foregroundStyle(document.category.color))
            VStack(alignment: .leading, spacing: 3) {
                Text(document.filename).font(.body.weight(.medium)).lineLimit(1)
                Text(details).font(.caption)
                    .foregroundStyle(Format.keyFact(document)?.urgent == true ? Color.orange : Color.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 12)
            if document.mine {
                Button(action: remove) { Image(systemName: "trash") }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                    .help("Remove from vault")
                    .opacity(hovering ? 1 : 0)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(hovering ? Color.primary.opacity(0.07) : Color.primary.opacity(0.035)))
        .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .onHover { hovering = $0 }
        .onTapGesture(perform: open)
        .contextMenu {
            Button("Open", action: open)
            if document.mine {
                Menu("Change Type") {
                    ForEach(Category.allCases, id: \.self) { category in
                        Button(category.label) { Task { await model.setCategory(document, category) } }
                            .disabled(category == document.category)
                    }
                }
                Divider()
                Button("Remove from Vault…", role: .destructive, action: remove)
            }
        }
    }
}

/// What happened to each added file.
struct UploadResults: View {
    @Environment(\.dismiss) private var dismiss
    let results: [UploadResult]
    let busy: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(busy ? "Adding documents…" : "Added to your vault").font(.headline)
            if busy {
                ProgressView("Reading and filing them. Scans and photos take a little longer.").controlSize(.small)
            }
            ForEach(Array(results.enumerated()), id: \.offset) { _, result in
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: result.status == "added" ? "checkmark.circle.fill"
                          : result.status == "duplicate" ? "equal.circle.fill" : "exclamationmark.triangle.fill")
                        .foregroundStyle(result.status == "added" ? .green : result.status == "duplicate" ? .orange : .red)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(result.filename).fontWeight(.medium)
                        Text(result.status == "added" ? "Filed as \(result.document?.category.label ?? "a document")."
                             : result.status == "duplicate" ? "Already in the vault." : (result.error ?? "Couldn't be added."))
                            .font(.callout).foregroundStyle(.secondary)
                    }
                }
            }
            HStack {
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction).disabled(busy)
            }
        }
        .padding(22)
        .frame(width: 440)
    }
}
