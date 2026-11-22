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

    var body: some View {
        NavigationSplitView {
            List(selection: $pane) {
                Section {
                    Label("Dashboard", systemImage: "house").tag(Pane.dashboard)
                    Label("Ask", systemImage: "bubble.left.and.text.bubble.right").tag(Pane.chat)
                    Label("All Documents", systemImage: "doc.on.doc").tag(Pane.documents)
                        .badge(model.documents.count)
                }
                Section("Types") {
                    ForEach(Category.allCases, id: \.self) { category in
                        Label(category.label, systemImage: category.symbol)
                            .tag(Pane.category(category))
                            .badge(model.documents.filter { $0.category == category }.count)
                    }
                }
            }
            .navigationSplitViewColumnWidth(min: 200, ideal: 220)
            .safeAreaInset(edge: .bottom) { AccountBar() }
        } detail: {
            switch pane ?? .dashboard {
            case .dashboard: DashboardView(pane: $pane)
            case .chat: ChatView()
            case .documents: DocumentsView(category: nil)
            case .category(let category): DocumentsView(category: category)
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
