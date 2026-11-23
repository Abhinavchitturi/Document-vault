import SwiftUI
import VaultKit

/// The Settings window (⌘,), with Mac-style tabs.
struct SettingsView: View {
    var body: some View {
        TabView {
            AccountSettings().tabItem { Label("Account", systemImage: "person.crop.circle") }
            VaultSettings().tabItem { Label("Vault", systemImage: "lock.doc") }
        }
        .frame(width: 520)
        .padding(20)
    }
}

struct AccountSettings: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        Form {
            LabeledContent("Signed in as", value: model.user?.name.capitalized ?? "Not signed in")
            LabeledContent("Role", value: model.user?.isAdmin == true ? "Admin" : "Family member")
            Button("Sign Out") { Task { await model.signOut() } }.disabled(model.user == nil)
        }
        .formStyle(.grouped)
    }
}

struct VaultSettings: View {
    @Environment(AppModel.self) private var model
    @State private var settings: VaultKit.Settings?

    var body: some View {
        Form {
            Section("Assistant") {
                LabeledContent("Status", value: model.info?.llmStatus.ready == true ? "Ready" : "Offline")
                if let message = model.info?.llmStatus.message { Text(message).font(.caption).foregroundStyle(.secondary) }
                AIDownloadView()
                LabeledContent("Answer model", value: settings?.llmModel ?? "–")
                LabeledContent("Search", value: settings.map { "\($0.searchMethod.capitalized), \($0.embeddingModel)" } ?? "–")
            }
            if let remote = model.remote {
                Section("This Mac") {
                    Text("Connected to the vault on \(remote.connection.name). The documents and the AI stay on that computer; it has to be on, with Document Vault open.")
                        .font(.callout)
                    Button("Disconnect") { Task { await model.disconnect() } }
                }
            } else {
                ConnectAnotherDevice()
                ComputersSettings()
            }
            Section {
                Text("Laptops, phones and the other computers that keep this vault reach this Mac over your home Wi-Fi. To use the vault from anywhere (work, travelling), you can also use Tailscale: a free, private connection between your own devices. Install it on this Mac and on the other device, sign in to both with the same account, and they find each other wherever they are. It's optional: without it, everything works at home.")
                    .font(.callout)
                Link("Get Tailscale (tailscale.com/download)", destination: URL(string: "https://tailscale.com/download")!)
            } header: {
                Text("Use It From Anywhere (Optional)")
            }
            Section("Privacy") {
                LabeledContent("Encryption key", value: settings?.keyKeptIn ?? "–")
                LabeledContent("Vault folder", value: model.remote == nil ? model.folder.path : "On the other computer")
                Text("Everything stays on this Mac. Nothing listens on the network for this app; phones connect only if you pair them.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            Section {
                Button("Show Engine Log in Finder") { model.showLog() }
            }
        }
        .formStyle(.grouped)
        .task {
            do { settings = try await model.client?.settings() } catch { model.report(error) }
        }
    }
}
