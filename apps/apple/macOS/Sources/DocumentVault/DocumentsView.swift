import SwiftUI
import UniformTypeIdentifiers
import VaultKit

/// Every document (or one type), in a native table. Drop files here, or use Add, to put them in the vault.
struct DocumentsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    let category: Category?
    @State private var search = ""
    @State private var selection = Set<VaultDocument.ID>()
    @State private var sortOrder = [KeyPathComparator(\VaultDocument.addedAt, order: .reverse)]
    @State private var importing = false
    @State private var dropping = false
    @State private var results: [UploadResult] = []
    @State private var adding = false
    @State private var toDelete: VaultDocument?

    private var shown: [VaultDocument] {
        model.documents
            .filter { category == nil || $0.category == category }
            .filter { search.isEmpty || $0.filename.localizedCaseInsensitiveContains(search)
                || ($0.reference ?? "").localizedCaseInsensitiveContains(search) || ($0.issuer ?? "").localizedCaseInsensitiveContains(search) }
            .sorted(using: sortOrder)
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
            if let document = ids.first.flatMap({ id in model.documents.first { $0.id == id } }) {
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
        .toolbar {
            ToolbarItem {
                Button("Add Documents", systemImage: "plus") { importing = true }
                    .help("Add PDFs, Word files or photos (or drop them on the list)")
            }
        }
        .overlay {
            if model.documents.isEmpty {
                ContentUnavailableView("No documents yet", systemImage: "tray",
                                       description: Text("Drop PDFs, Word files or photos here, or click +."))
            } else if dropping {
                RoundedRectangle(cornerRadius: 14).strokeBorder(Color.accentColor, style: StrokeStyle(lineWidth: 3, dash: [8]))
                    .padding(8).allowsHitTesting(false)
            }
        }
        .dropDestination(for: URL.self) { urls, _ in
            add(urls)
            return true
        } isTargeted: { dropping = $0 }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.pdf, .image, .plainText, .item],
                      allowsMultipleSelection: true) { result in
            if case .success(let urls) = result { add(urls) }
        }
        .sheet(isPresented: Binding(get: { !results.isEmpty || adding }, set: { if !$0 { results = [] } })) {
            UploadResults(results: results, busy: adding)
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

    private func add(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        adding = true
        Task {
            results = await model.add(urls)
            adding = false
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
