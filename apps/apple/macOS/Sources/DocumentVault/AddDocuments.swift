import SwiftUI
import UniformTypeIdentifiers
import VaultKit

/// Adding documents, from anywhere in the main window: the Add Documents button in the toolbar (on every page), files
/// dropped anywhere on the window, or the dashboard's "Add your first documents". Shows what happened to each file.
struct AddDocuments: ViewModifier {
    @Environment(AppModel.self) private var model
    @State private var dropping = false
    @State private var results: [UploadResult] = []
    @State private var adding = false

    func body(content: Content) -> some View {
        @Bindable var model = model
        content
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Button("Add Documents", systemImage: "plus") { model.choosingFiles = true }
                        .help("Add PDFs, Word files or photos (or drop them anywhere on the window)")
                        .keyboardShortcut("o")
                }
            }
            .overlay {
                if dropping {
                    RoundedRectangle(cornerRadius: 14).strokeBorder(Color.accentColor, style: StrokeStyle(lineWidth: 3, dash: [8]))
                        .padding(8).allowsHitTesting(false)
                }
            }
            .dropDestination(for: URL.self) { urls, _ in
                add(urls)
                return true
            } isTargeted: { dropping = $0 }
            .fileImporter(isPresented: $model.choosingFiles, allowedContentTypes: [.pdf, .image, .plainText, .item],
                          allowsMultipleSelection: true) { result in
                if case .success(let urls) = result { add(urls) }
            }
            .sheet(isPresented: Binding(get: { !results.isEmpty || adding }, set: { if !$0 { results = [] } })) {
                UploadResults(results: results, busy: adding)
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
