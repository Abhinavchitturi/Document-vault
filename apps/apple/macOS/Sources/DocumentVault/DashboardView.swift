import SwiftUI
import VaultKit

/// What the vault holds, what is coming up, and what was added last.
struct DashboardView: View {
    @Environment(AppModel.self) private var model
    @Binding var pane: Pane?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                if model.documents.isEmpty { FirstDocuments() }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 12)], spacing: 12) {
                    StatCard(title: "Documents", value: model.documents.count, symbol: "doc.on.doc", tint: .indigo, filled: true) {
                        pane = .documents
                    }
                    ForEach(Category.allCases.filter { c in c != .other && model.documents.contains { $0.category == c } }, id: \.self) { category in
                        StatCard(title: category.label, value: model.documents.filter { $0.category == category }.count,
                                 symbol: category.symbol, tint: category.color) { pane = .category(category) }
                    }
                }

                HStack(alignment: .top, spacing: 20) {
                    GroupBox {
                        if model.upcoming.isEmpty {
                            Text("Nothing expires or falls due in the next 90 days.").foregroundStyle(.secondary)
                                .frame(maxWidth: .infinity, alignment: .leading).padding(.vertical, 6)
                        } else {
                            VStack(spacing: 0) {
                                ForEach(model.upcoming) { item in
                                    UpcomingRow(item: item)
                                    if item.id != model.upcoming.last?.id { Divider() }
                                }
                            }
                        }
                    } label: {
                        Label("Coming up", systemImage: "calendar").font(.headline)
                    }
                    .frame(maxWidth: .infinity, alignment: .top)

                    GroupBox {
                        VStack(spacing: 0) {
                            ForEach(model.documents.sorted { $0.addedAt > $1.addedAt }.prefix(6)) { document in
                                DocumentRow(document: document)
                                Divider().opacity(0.5)
                            }
                        }
                    } label: {
                        Label("Recently added", systemImage: "clock").font(.headline)
                    }
                    .frame(maxWidth: .infinity, alignment: .top)
                }
            }
            .padding(24)
        }
        .navigationTitle("Dashboard")
        .toolbar {
            ToolbarItem { Button("Ask", systemImage: "bubble.left") { pane = .chat } }
        }
        .refreshable { await model.reload() }
    }
}

struct StatCard: View {
    let title: String
    let value: Int
    let symbol: String
    let tint: Color
    var filled = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 6) {
                Label(title, systemImage: symbol).font(.callout.weight(.medium))
                    .foregroundStyle(filled ? .white.opacity(0.85) : .secondary)
                Text("\(value)").font(.system(size: 30, weight: .bold, design: .rounded)).monospacedDigit()
                    .foregroundStyle(filled ? .white : .primary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .background(filled ? AnyShapeStyle(tint.gradient) : AnyShapeStyle(.background.secondary),
                        in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

struct UpcomingRow: View {
    let item: Upcoming

    var body: some View {
        HStack(spacing: 12) {
            Image(systemName: item.category.symbol).foregroundStyle(item.category.color).frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.filename).lineLimit(1)
                Text("\(item.kind == "due" ? "Due" : "Expires") \(Format.date(item.date))")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Spacer()
            Text(Format.daysLeft(item.daysLeft)).font(.caption.weight(.semibold)).monospacedDigit()
                .foregroundStyle(item.daysLeft < 0 ? .red : item.daysLeft <= 30 ? .orange : .secondary)
        }
        .padding(.vertical, 8)
    }
}

struct DocumentRow: View {
    @Environment(\.openWindow) private var openWindow
    let document: VaultDocument

    var body: some View {
        Button {
            openWindow(id: "document", value: document.docId)
        } label: {
            HStack(spacing: 12) {
                Image(systemName: document.category.symbol).foregroundStyle(document.category.color).frame(width: 20)
                VStack(alignment: .leading, spacing: 2) {
                    Text(document.filename).lineLimit(1)
                    Text("\(document.category.label) · \(Format.date(document.addedAt))").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
            .contentShape(Rectangle())
            .padding(.vertical, 8)
        }
        .buttonStyle(.plain)
    }
}

/// An empty vault: the one thing to do first.
struct FirstDocuments: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 12) {
            Image(systemName: "doc.badge.plus").font(.system(size: 40)).foregroundStyle(Color.vault)
            Text("Add your first documents").font(.title2.weight(.semibold))
            Text("Insurance policies, warranties, bills, property papers, ID cards: PDFs, Word files or photos. "
                 + "Each one is read, filed by type and encrypted on this computer.")
                .multilineTextAlignment(.center).foregroundStyle(.secondary).frame(maxWidth: 460)
            Button { model.choosingFiles = true } label: { Label("Add Documents", systemImage: "plus").padding(.horizontal, 6) }
                .buttonStyle(.borderedProminent).controlSize(.large)
            Text("or drop files anywhere on this window").font(.caption).foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity)
        .padding(28)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}
