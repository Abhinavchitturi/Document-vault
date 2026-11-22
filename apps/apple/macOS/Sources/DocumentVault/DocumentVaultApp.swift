import AppKit
import SwiftUI
import VaultKit

@main
struct DocumentVaultApp: App {
    @State private var model = AppModel()

    var body: some Scene {
        WindowGroup("Document Vault", id: "main") {
            RootView()
                .environment(model)
                .tint(.vault)
                .frame(minWidth: 860, minHeight: 560)
                .task { await model.start() }
                .onReceive(NotificationCenter.default.publisher(for: NSApplication.willTerminateNotification)) { _ in
                    model.stop()  // the engine (and llama.cpp with it) stops with the app
                    try? SelfUpdate.swapAfterQuit(reopen: false)  // "Update When I Quit": the new app goes in now
                }
        }
        .defaultSize(width: 1180, height: 780)
        .commands {
            CommandGroup(replacing: .newItem) {}  // one vault, one window
        }

        // An original document, in its own window, straight from memory.
        WindowGroup("Document", id: "document", for: String.self) { $docId in
            if let docId {
                DocumentWindow(docId: docId).environment(model)
            }
        }
        .defaultSize(width: 820, height: 1000)

        SwiftUI.Settings {  // (VaultKit has a Settings type too)
            SettingsView().environment(model)
        }
    }
}

struct RootView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Group {
            switch model.phase {
            case .choosing:
                ChoiceView()
            case .connecting:
                ConnectView()
            case .starting:
                StatusScreen(title: "Opening your vault…", detail: "The first time after installing takes a little longer.", busy: true)
            case .settingUpEngine:
                EngineSetupView()
            case .notInstalled(let folder):
                StatusScreen(title: "Document Vault isn't set up on this Mac yet",
                             detail: "Download DocumentVault-macOS.dmg from github.com/Abhinavchitturi/Document-vault/releases and drag the app to Applications again. Looked for the engine in \(folder).",
                             busy: false)
            case .failed(let why):
                StatusScreen(title: "Document Vault couldn't open", detail: why, busy: false) {
                    HStack {
                        Button("Show Log") { model.showLog() }
                        Button("Try Again") { Task { await model.start() } }.keyboardShortcut(.defaultAction)
                    }
                }
            case .signIn(let status):
                SignInView(status: status)
            case .joining:
                JoinVaultView()
            case .ready:
                MainView()
            }
        }
        .alert("Something went wrong", isPresented: Binding(get: { model.problem != nil }, set: { if !$0 { model.problem = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.problem ?? "")
        }
    }
}

/// A full-window message: starting up, or why the vault can't open.
struct StatusScreen<Actions: View>: View {
    let title: String
    let detail: String?
    let busy: Bool
    @ViewBuilder var actions: Actions

    init(title: String, detail: String?, busy: Bool, @ViewBuilder actions: () -> Actions) {
        self.title = title
        self.detail = detail
        self.busy = busy
        self.actions = actions()
    }

    var body: some View {
        VStack(spacing: 16) {
            AppIcon(size: 72)
            Text(title).font(.title2.weight(.semibold))
            if let detail {
                Text(detail).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 460)
                    .textSelection(.enabled)
            }
            if busy { ProgressView().controlSize(.small) }
            actions
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

extension StatusScreen where Actions == EmptyView {
    init(title: String, detail: String?, busy: Bool) {
        self.init(title: title, detail: detail, busy: busy) { EmptyView() }
    }
}

extension Color {
    /// The vault's indigo (the app icon's colour), used instead of the Mac's own accent colour.
    static let vault = Color(red: 0.345, green: 0.337, blue: 0.839)
}

/// The app's mark: a document with a lock, on the vault's indigo.
struct AppIcon: View {
    var size: CGFloat = 28

    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.23, style: .continuous)
            .fill(LinearGradient(colors: [Color(red: 0.37, green: 0.36, blue: 0.90), Color(red: 0.31, green: 0.27, blue: 0.90)],
                                 startPoint: .top, endPoint: .bottom))
            .frame(width: size, height: size)
            .overlay {
                Image(systemName: "lock.doc.fill").font(.system(size: size * 0.5, weight: .medium)).foregroundStyle(.white)
            }
    }
}
