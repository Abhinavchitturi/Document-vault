import SwiftUI
import UniformTypeIdentifiers
import VaultKit

/// Every document (or one type), in a native table. Adding documents works from anywhere (AddDocuments.swift).
struct DocumentsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    let category: Category?
    @State private var search = ""
    @State private var selection = Set<VaultDocument.ID>()
    @State private var sortOrder = [KeyPathComparator(\VaultDocument.addedAt, order: .reverse)]
    @State private var toDelete: VaultDocument?

    private var shown: [VaultDocument] {
        let matching: [VaultDocument] = model.documents.filter(matches)
        return matching.sorted(using: sortOrder)
    }

    private func matches(_ document: VaultDocument) -> Bool {
        if let category, document.category != category { return false }
        if search.isEmpty { return true }
        let fields: [String] = [document.filename, document.reference ?? "", document.issuer ?? ""]
        return fields.contains { $0.localizedCaseInsensitiveContains(search) }
    }

    private func document(_ id: VaultDocument.ID) -> VaultDocument? {
        model.documents.first { $0.id == id }
    }

    var body: some View {
        Table(shown, selection: $selection, sortOrder: $sortOrder) {
            TableColumn("Name", value: \.filename) { document in
                HStack(spacing: 8) {
                    Image(systemName: document.category.symbol).foregroundStyle(document.category.color)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(document.filename)
                        if let key = Format.keyFact(document) {
                            Text(key.text).font(.caption).foregroundStyle(key.urgent ? .orange : .secondary)
                        }
                    }
                }
            }
            .width(min: 240, ideal: 380)
            TableColumn("Type", value: \.category.label) { Text($0.category.label) }.width(110)
            TableColumn("Added", value: \.addedAt) { Text(Format.date($0.addedAt)).monospacedDigit() }.width(110)
            TableColumn("Size", value: \.sizeBytes) { Text(Format.size($0.sizeBytes)).monospacedDigit().foregroundStyle(.secondary) }
                .width(80)
        }
        .contextMenu(forSelectionType: VaultDocument.ID.self) { ids in
            if let id = ids.first, let document = self.document(id) {
                Button("Open") { openWindow(id: "document", value: document.docId) }
                if document.mine {
                    Menu("Change Type") {
                        ForEach(Category.allCases, id: \.self) { category in
                            Button(category.label) { Task { await model.setCategory(document, category) } }
                                .disabled(category == document.category)
                        }
                    }
                    Divider()
                    Button("Remove from Vault…", role: .destructive) { toDelete = document }
                }
            }
        } primaryAction: { ids in
            ids.forEach { openWindow(id: "document", value: $0) }
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
            }
        }
        .confirmationDialog("Remove \(toDelete?.filename ?? "") from the vault?",
                            isPresented: Binding(get: { toDelete != nil }, set: { if !$0 { toDelete = nil } })) {
            Button("Remove", role: .destructive) {
                if let document = toDelete { Task { await model.delete(document) } }
            }
        } message: {
            Text("The vault's encrypted copy and its search data are deleted. Your original file, if you still have it, isn't touched.")
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
