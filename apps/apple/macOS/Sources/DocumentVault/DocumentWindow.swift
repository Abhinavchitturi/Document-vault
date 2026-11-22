import AppKit
import PDFKit
import SwiftUI
import VaultKit

/// An original document in its own window, decrypted in memory only: nothing is written to disk.
struct DocumentWindow: View {
    @Environment(AppModel.self) private var model
    let docId: String
    @State private var data: Data?
    @State private var problem: String?
    @State private var confirming = false
    @Environment(\.dismiss) private var dismiss

    private var document: VaultDocument? { model.documents.first { $0.docId == docId } }

    var body: some View {
        Group {
            if let problem {
                ContentUnavailableView("Couldn't open it", systemImage: "exclamationmark.triangle", description: Text(problem))
            } else if let data {
                if let pdf = PDFDocument(data: data) {
                    PDFViewer(document: pdf)
                } else if let image = NSImage(data: data) {
                    ScrollView([.horizontal, .vertical]) { Image(nsImage: image).resizable().scaledToFit().padding() }
                } else if let text = String(data: data, encoding: .utf8) {
                    ScrollView { Text(text).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading).padding(24) }
                } else {
                    ContentUnavailableView("No preview", systemImage: "doc",
                                           description: Text("This kind of file can't be shown here."))
                }
            } else {
                ProgressView()
            }
        }
        .navigationTitle(document?.filename ?? "Document")
        .navigationSubtitle(document.map { "\($0.category.label) · \($0.pages) page\($0.pages == 1 ? "" : "s")" } ?? "")
        .frame(minWidth: 480, minHeight: 520)
        .toolbar {
            if document?.mine == true {
                ToolbarItem {
                    Button("Remove from Vault", systemImage: "trash") { confirming = true }.help("Remove this document from the vault")
                }
            }
        }
        .confirmationDialog("Remove \(document?.filename ?? "this document") from the vault?", isPresented: $confirming) {
            Button("Remove", role: .destructive) {
                if let document {
                    Task {
                        await model.delete(document)
                        dismiss()
                    }
                }
            }
        } message: {
            Text("The vault's encrypted copy and its search data are deleted, on every computer that keeps this vault. Your original file, if you still have it, isn't touched.")
        }
        .task {
            do {
                data = try await model.client?.original(docId)
            } catch {
                problem = error.localizedDescription
            }
        }
    }
}

/// Apple's PDF viewer (the one in Preview), with selectable text and search.
struct PDFViewer: NSViewRepresentable {
    let document: PDFDocument

    func makeNSView(context: Context) -> PDFView {
        let view = PDFView()
        view.autoScales = true
        view.displayMode = .singlePageContinuous
        view.backgroundColor = .windowBackgroundColor
        view.document = document
        return view
    }

    func updateNSView(_ view: PDFView, context: Context) {
        if view.document !== document { view.document = document }
    }
}
