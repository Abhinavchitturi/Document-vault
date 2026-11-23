import SwiftUI
import VaultKit

enum Pane: Hashable {
    case dashboard, chat, documents
    case category(Category)
}

/// The main window: a sidebar like Mail or Notes, and the chosen pane beside it.
struct MainView: View {
    @Environment(AppModel.self) private var model
    @State private var pane: Pane? = .dashboard

    /// The types that have documents: the others appear once a document of that type is added.
    private var usedTypes: [Category] {
        Category.allCases.filter { category in model.documents.contains { $0.category == category } }
    }

    var body: some View {
        NavigationSplitView {
            List {  // rows are buttons: the list's own selection stopped taking clicks (1.0.x), buttons always do
                Section {
                    SidebarRow(title: "Dashboard", symbol: "house", pane: .dashboard, selection: $pane)
                    SidebarRow(title: "Ask", symbol: "bubble.left.and.text.bubble.right", pane: .chat, selection: $pane)
                    SidebarRow(title: "All Documents", symbol: "doc.on.doc", pane: .documents, selection: $pane,
                               count: model.documents.count)
                }
                if !usedTypes.isEmpty {
                    Section("Types") {
                        ForEach(usedTypes, id: \.self) { category in
                            SidebarRow(title: category.label, symbol: category.symbol, pane: .category(category), selection: $pane,
                                       count: model.documents.filter { $0.category == category }.count)
                        }
                    }
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 200, ideal: 220)
            .safeAreaInset(edge: .bottom) { AccountBar() }
        } detail: {
            Group {
                switch pane ?? .dashboard {
                case .dashboard: DashboardView(pane: $pane)
                case .chat: ChatView()
                case .documents: DocumentsView(category: nil)
                case .category(let category): DocumentsView(category: category)
                }
            }
            .modifier(AddDocuments())  // on the main area only: on the whole window it took the sidebar's clicks
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            VStack(spacing: 0) {
                UpdateBanner()
                SearchReadyBanner()
            }
        }
    }
}

/// Who is signed in, and whether the assistant is ready, at the bottom of the sidebar.
struct AccountBar: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "person.crop.circle.fill").font(.title2).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(model.user?.name.capitalized ?? "").font(.callout.weight(.medium))
                HStack(spacing: 5) {
                    Circle().fill(model.info?.llmStatus.ready == true ? Color.green : Color.orange).frame(width: 6, height: 6)
                    Text(model.info?.llmStatus.ready == true ? "Assistant ready" : "Assistant offline")
                        .font(.caption).foregroundStyle(.secondary)
                }
                .help(model.info?.llmStatus.message ?? "")
            }
            Spacer()
            SettingsLink { Image(systemName: "gearshape").font(.title3) }  // the same as Document Vault → Settings… (⌘,)
                .buttonStyle(.borderless)
                .help("Settings (⌘,)")
        }
        .padding(12)
    }
}

extension Category {
    var symbol: String {
        switch self {
        case .insurance: "shield.lefthalf.filled"
        case .warranty: "checkmark.seal"
        case .bill: "doc.text"
        case .property: "house.and.flag"
        case .identity: "person.text.rectangle"
        case .other: "folder"
        }
    }

    var color: Color {
        switch self {
        case .insurance: .blue
        case .warranty: .green
        case .bill: .orange
        case .property: .purple
        case .identity: .teal
        case .other: .gray
        }
    }
}

/// One row of the sidebar: opens its page when clicked, highlighted while it's the page shown.
struct SidebarRow: View {
    let title: String
    let symbol: String
    let pane: Pane
    @Binding var selection: Pane?
    var count: Int? = nil

    private var chosen: Bool { (selection ?? .dashboard) == pane }

    var body: some View {
        Button { selection = pane } label: {
            HStack {
                Label(title, systemImage: symbol)
                Spacer()
                if let count, count > 0 { Text("\(count)").font(.callout).foregroundStyle(.secondary).monospacedDigit() }
            }
            .padding(.vertical, 4)
            .padding(.horizontal, 6)
            .contentShape(Rectangle())  // the whole row, not just the words
        }
        .buttonStyle(.plain)
        .listRowBackground(
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .fill(chosen ? Color.primary.opacity(0.1) : .clear)
                .padding(.horizontal, 10)
        )
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }
}
