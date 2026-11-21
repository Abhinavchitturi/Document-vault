import SwiftUI
import VaultKit

struct ChatMessage: Identifiable {
    let id = UUID()
    let fromUser: Bool
    var text: String
    var sources: [Source] = []
    var citations: [Int] = []
    var finished = false
    var problem: String?
}

/// Ask in plain English; the answer streams in, citing the pages it came from.
struct ChatView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @State private var messages: [ChatMessage] = []
    @State private var question = ""
    @State private var answering: Task<Void, Never>?
    @FocusState private var focused: Bool

    private let suggestions = ["When does my bike insurance expire?", "What is the electricity bill for August?",
                               "Find the washing machine warranty", "What is the carpet area of our flat?"]

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { scroller in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 18) {
                        if messages.isEmpty { welcome }
                        ForEach(messages) { message in
                            MessageView(message: message) { source in openWindow(id: "document", value: source.docId) }
                                .id(message.id)
                        }
                    }
                    .padding(24)
                    .frame(maxWidth: 760)
                    .frame(maxWidth: .infinity)
                }
                .onChange(of: messages.last?.text) { _, _ in
                    if let last = messages.last { scroller.scrollTo(last.id, anchor: .bottom) }
                }
            }
            composer
        }
        .navigationTitle("Ask")
        .toolbar {
            ToolbarItem {
                Button("New Chat", systemImage: "square.and.pencil") { answering?.cancel(); messages = [] }
                    .disabled(messages.isEmpty)
            }
        }
        .onAppear { focused = true }
    }

    private var welcome: some View {
        VStack(spacing: 14) {
            AppIcon(size: 56)
            Text("What would you like to know?").font(.title2.weight(.semibold))
            Text("Ask about your \(model.documents.count) documents: expiry dates, amounts, policy numbers, warranties.")
                .foregroundStyle(.secondary)
            if model.info?.llmStatus.ready == false {
                Label(model.info?.llmStatus.message ?? "", systemImage: "exclamationmark.triangle")
                    .font(.callout).foregroundStyle(.orange).padding(.top, 4)
            }
            LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], spacing: 10) {
                ForEach(suggestions, id: \.self) { suggestion in
                    Button { ask(suggestion) } label: {
                        Text(suggestion).frame(maxWidth: .infinity, alignment: .leading).padding(12)
                            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.top, 10)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 40)
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 10) {
            TextField("Ask about your documents…", text: $question, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...6)
                .focused($focused)
                .onSubmit { ask(question) }
            Button {
                if answering != nil { answering?.cancel() } else { ask(question) }
            } label: {
                Image(systemName: answering != nil ? "stop.circle.fill" : "arrow.up.circle.fill").font(.system(size: 26))
            }
            .buttonStyle(.plain)
            .foregroundStyle(question.isEmpty && answering == nil ? Color.secondary : Color.vault)
            .disabled(question.trimmingCharacters(in: .whitespaces).isEmpty && answering == nil)
            .keyboardShortcut(.return, modifiers: .command)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 20, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(.separator))
        .padding(.horizontal, 24)
        .padding(.bottom, 18)
        .frame(maxWidth: 808)
    }

    private func ask(_ text: String) {
        let text = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, answering == nil, let client = model.client else { return }
        let history = messages.filter { $0.finished || $0.fromUser }.suffix(6)
            .map { ChatTurn(role: $0.fromUser ? "user" : "assistant", content: $0.text) }
        messages.append(ChatMessage(fromUser: true, text: text, finished: true))
        messages.append(ChatMessage(fromUser: false, text: ""))
        question = ""
        let index = messages.count - 1
        answering = Task {
            do {
                for try await event in client.chat(text, history: Array(history)) {
                    switch event {
                    case .sources(let sources): messages[index].sources = sources
                    case .token(let piece): messages[index].text += piece
                    case .done(let citations, _, _): messages[index].citations = citations
                    case .error(let message, _): messages[index].problem = message
                    }
                }
            } catch is CancellationError {
            } catch {
                messages[index].problem = error.localizedDescription
                if case VaultError.signedOut = error { model.report(error) }
            }
            messages[index].finished = true
            answering = nil
        }
    }
}

struct MessageView: View {
    let message: ChatMessage
    let open: (Source) -> Void

    var body: some View {
        if message.fromUser {
            HStack {
                Spacer(minLength: 80)
                Text(message.text)
                    .padding(.horizontal, 14).padding(.vertical, 9)
                    .foregroundStyle(.white)
                    .background(Color.vault, in: UnevenRoundedRectangle(topLeadingRadius: 18, bottomLeadingRadius: 18,
                                                                               bottomTrailingRadius: 5, topTrailingRadius: 18))
                    .textSelection(.enabled)
            }
        } else {
            HStack(alignment: .top, spacing: 12) {
                AppIcon(size: 28)
                VStack(alignment: .leading, spacing: 10) {
                    if message.text.isEmpty && message.problem == nil {
                        ProgressView().controlSize(.small)
                    } else {
                        Text(message.text).font(.body).lineSpacing(3).textSelection(.enabled)
                    }
                    if let problem = message.problem {
                        Label(problem, systemImage: "exclamationmark.triangle").font(.callout).foregroundStyle(.orange)
                        if message.citations.isEmpty && !message.sources.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {  // what the search found, without an answer
                                ForEach(message.sources.prefix(3)) { source in
                                    Button { open(source) } label: {
                                        VStack(alignment: .leading, spacing: 3) {
                                            Label("\(source.filename) · p. \(source.page)", systemImage: source.category.symbol)
                                                .font(.caption.weight(.semibold))
                                            Text(source.text.prefix(220) + (source.text.count > 220 ? "…" : ""))
                                                .font(.callout).foregroundStyle(.secondary).lineLimit(3)
                                        }
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        .padding(10)
                                        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                        }
                    }
                    let cited = message.citations.compactMap { n in message.sources.indices.contains(n - 1) ? message.sources[n - 1] : nil }
                    if !cited.isEmpty {
                        FlowRow(cited) { source in
                            Button { open(source) } label: {
                                Label("\(source.filename) · p. \(source.page)", systemImage: source.category.symbol)
                                    .font(.caption).lineLimit(1)
                                    .padding(.horizontal, 10).padding(.vertical, 5)
                                    .background(.background.secondary, in: Capsule())
                            }
                            .buttonStyle(.plain)
                            .help("Open \(source.filename)")
                        }
                    }
                }
                Spacer(minLength: 40)
            }
        }
    }
}

/// Source chips side by side, wrapping onto the next line when there's no room.
struct FlowRow<Item: Identifiable, Content: View>: View {
    let items: [Item]
    let content: (Item) -> Content

    init(_ items: [Item], @ViewBuilder content: @escaping (Item) -> Content) {
        self.items = items
        self.content = content
    }

    var body: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 6) { ForEach(items) { content($0) } }
            VStack(alignment: .leading, spacing: 6) { ForEach(items) { content($0) } }
        }
    }
}
