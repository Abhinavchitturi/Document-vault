import SwiftUI
import VaultKit

/// 1.0: this Mac also keeps a vault another computer keeps: the same documents on both, kept up to date, so either one
/// can be off. Its address and the code it shows, a check that the security code matches, then a copy of the vault.
struct JoinVaultView: View {
    @Environment(AppModel.self) private var model
    @State private var address = ""
    @State private var code = ""
    @State private var probe: SyncProbe?
    @State private var busy = false
    @State private var problem: String?
    @State private var copying: SyncJoining?

    var body: some View {
        VStack(spacing: 20) {
            AppIcon(size: 64)
            if let copying {
                Text("Copying the vault").font(.title.weight(.semibold))
                if copying.total > 0 {
                    ProgressView(value: Double(copying.done), total: Double(copying.total)) {
                        Text("Copying from \(copying.with ?? "the other computer"): \(copying.done) of \(copying.total) documents")
                    }
                    .frame(width: 380)
                } else {
                    ProgressView { Text("Joining the vault on \(copying.with ?? "the other computer")…") }.progressViewStyle(.linear).frame(width: 380)
                }
                Text("Documents arrive with their search data, so nothing is read again here.").font(.caption).foregroundStyle(.secondary)
            } else {
                VStack(spacing: 6) {
                    Text("Join a vault kept on another computer").font(.title.weight(.semibold))
                    Text("This Mac will keep the same vault as the other computer: the same documents, family members and passwords, kept up to date on both. Either one can be off; laptops and phones use the one that's on.")
                        .foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 460)
                    Text("On the other computer, open Settings → Computers that keep this vault → Add a computer.")
                        .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 460)
                }
                Form {
                    TextField("Address", text: $address, prompt: Text("for example 192.168.1.20"))
                    TextField("Code", text: $code, prompt: Text("for example K7QF-2M9X"))
                }
                .formStyle(.grouped).frame(width: 400).scrollDisabled(true).fixedSize(horizontal: false, vertical: true)
                .onSubmit(check)
                if let problem {
                    Label(problem, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red).font(.callout).frame(maxWidth: 440)
                }
                Button(action: check) { Text(busy ? "Joining…" : "Join").frame(width: 200) }
                    .controlSize(.large).buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(busy || address.trimmingCharacters(in: .whitespaces).isEmpty || code.count < 8)
                Button("Back") { Task { await model.checkSignIn() } }.buttonStyle(.link)
            }
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .alert("Check the security code", isPresented: Binding(get: { probe != nil }, set: { if !$0 { probe = nil } })) {
            Button("It Matches") { if let probe { join(probe) } }
            Button("Cancel", role: .cancel) { busy = false }
        } message: {
            Text("On the other computer, Settings shows a security code next to the code you typed. Does it match this one?\n\n"
                 + RemoteVault.securityCode(probe?.fingerprint ?? String(repeating: "0", count: 16))
                 + "\n\nIf it doesn't match, choose Cancel: something else answered at that address.")
        }
    }

    private func check() {
        busy = true
        problem = nil
        Task {
            do { probe = try await model.client?.syncProbe(address.trimmingCharacters(in: .whitespaces)) }
            catch { problem = error.localizedDescription; busy = false }
        }
    }

    private func join(_ probe: SyncProbe) {
        Task {
            do {
                try await model.client?.joinVault(address: probe.address, code: code.trimmingCharacters(in: .whitespaces),
                                                  fingerprint: probe.fingerprint)
                copying = SyncJoining(state: "copying", done: 0, total: 0, error: nil, with: nil)
                while let now = try await model.client?.syncJoining(), now.state == "copying" {
                    copying = now
                    try await Task.sleep(for: .milliseconds(800))
                }
                await model.checkSignIn()  // the vault is set up now: sign in with the same name and password
            } catch {
                problem = error.localizedDescription
                copying = nil
            }
            busy = false
        }
    }
}

/// 1.0, in Settings: the computers that keep this vault, how up to date they are, and adding another.
struct ComputersSettings: View {
    @Environment(AppModel.self) private var model
    @State private var status: SyncStatus?
    @State private var invite: SyncInvite?
    @State private var problem: String?

    var body: some View {
        Section {
            if let status {
                LabeledContent(status.name, value: "This Mac")
                ForEach(status.peers) { peer in
                    LabeledContent(peer.name) {
                        Text(peer.problem.map { "Not reached lately: \($0)" } ?? (peer.syncedAt == nil ? "Catching up…" : "Up to date"))
                            .foregroundStyle(peer.problem == nil ? Color.secondary : Color.orange)
                    }
                }
                if status.peers.isEmpty {
                    Text("Only this Mac keeps the vault. Add another computer, so either one can be off.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if model.user?.isAdmin == true {
                Button("Add a Computer That Also Keeps This Vault") {
                    Task {
                        do { invite = try await model.client?.syncInvite() } catch { problem = error.localizedDescription }
                    }
                }
            }
            if let invite {
                VStack(alignment: .leading, spacing: 6) {
                    Text("On the other computer, install Document Vault, choose “Keep the vault on this computer”, then “Join a vault kept on another computer instead”, and type:")
                        .font(.callout)
                    LabeledContent("Code", value: invite.code).font(.title3.monospaced())
                    LabeledContent("Address", value: invite.urls.compactMap { URL(string: $0)?.host() }.joined(separator: "  or  "))
                    LabeledContent("Security code", value: RemoteVault.securityCode(invite.fingerprint)).font(.body.monospaced())
                    Text("The code works once, for 10 minutes.").font(.caption).foregroundStyle(.secondary)
                }
            }
            if let problem { Text(problem).font(.caption).foregroundStyle(.red) }
        } header: {
            Text("Computers That Keep This Vault")
        } footer: {
            Text("Each one has every document, kept up to date with the others within seconds when they can reach each other (the same Wi-Fi, or Tailscale). Laptops and phones use whichever one is on.")
                .font(.caption).foregroundStyle(.secondary)
        }
        .task {
            do { status = try await model.client?.syncStatus() } catch { problem = error.localizedDescription }
        }
    }
}
