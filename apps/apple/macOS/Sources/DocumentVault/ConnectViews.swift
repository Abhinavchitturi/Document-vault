import SwiftUI
import VaultKit

/// The first start: is this Mac the centre (the vault lives here), or does it connect to the vault on another computer?
struct ChoiceView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 22) {
            AppIcon(size: 64)
            VStack(spacing: 6) {
                Text("How will you use Document Vault here?").font(.title.weight(.semibold))
                Text("You can change this later in Settings.").foregroundStyle(.secondary)
            }
            VStack(spacing: 12) {
                option("house.fill", "Keep the vault on this Mac",
                       "This Mac is the centre: your documents and the AI stay here. Other computers and phones can connect to it.") {
                    model.mode = "here"
                    Task { await model.start() }
                }
                option("laptopcomputer.and.arrow.down", "Connect to the vault on another computer",
                       "Another computer at home is the centre. This Mac uses its documents over Wi-Fi, or from anywhere with Tailscale. Nothing to download here.") {
                    model.mode = "remote"
                    model.phase = .connecting
                }
            }
            .frame(width: 520)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func option(_ symbol: String, _ title: String, _ detail: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 16) {
                Image(systemName: symbol).font(.system(size: 26)).foregroundStyle(Color.vault).frame(width: 36)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.headline)
                    Text(detail).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            .padding(18)
            .background(.background.secondary, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

/// Connect this Mac to the vault on another computer: its address, the code it shows, and a check that the security code
/// matches, so this Mac trusts that vault and nothing pretending to be it.
struct ConnectView: View {
    @Environment(AppModel.self) private var model
    @State private var address = ""
    @State private var code = ""
    @State private var busy = false
    @State private var problem: String?
    @State private var fingerprint: String?

    var body: some View {
        VStack(spacing: 20) {
            AppIcon(size: 64)
            VStack(spacing: 6) {
                Text("Connect to your vault").font(.title.weight(.semibold))
                Text("On the computer that keeps the vault, open Document Vault → Settings → Connect another computer or phone → Show a Code. Then type what it shows here.")
                    .foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 440)
            }
            Form {
                TextField("Address", text: $address, prompt: Text("for example 192.168.1.20"))
                TextField("Code", text: $code, prompt: Text("for example K7QF-2M9X"))
            }
            .formStyle(.grouped)
            .frame(width: 400)
            .scrollDisabled(true)
            .fixedSize(horizontal: false, vertical: true)
            .onSubmit(check)
            if let problem {
                Label(problem, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red).font(.callout)
                    .frame(maxWidth: 440)
            }
            Button(action: check) { Text(busy ? "Connecting…" : "Connect").frame(width: 200) }
                .controlSize(.large).buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                .disabled(busy || address.trimmingCharacters(in: .whitespaces).isEmpty || code.count < 8)
            Button("Use this Mac as the centre instead") {
                model.mode = "here"
                Task { await model.start() }
            }
            .buttonStyle(.link)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .alert("Check the security code", isPresented: Binding(get: { fingerprint != nil }, set: { if !$0 { fingerprint = nil } })) {
            Button("It Matches") { if let fingerprint { pair(fingerprint) } }
            Button("Cancel", role: .cancel) { busy = false }
        } message: {
            Text("On the other computer, Document Vault shows a security code. Does it match this one?\n\n"
                 + RemoteVault.securityCode(fingerprint ?? String(repeating: "0", count: 16))
                 + "\n\nIf it doesn't match, choose Cancel: something on the network may be pretending to be your vault.")
        }
    }

    private func check() {
        busy = true
        problem = nil
        Task {
            do { fingerprint = try await RemoteVault.probe(address) } catch { problem = error.localizedDescription; busy = false }
        }
    }

    private func pair(_ fingerprint: String) {
        Task {
            do { try await model.connect(address: address, code: code, fingerprint: fingerprint) }
            catch { problem = error.localizedDescription }
            busy = false
        }
    }
}

/// On the centre: a code for another computer or a phone.
struct ConnectAnotherDevice: View {
    @Environment(AppModel.self) private var model
    @State private var pairing: Pairing?
    @State private var problem: String?

    var body: some View {
        Section("Connect another computer or phone") {
            Text("This Mac is the centre. Another laptop or a phone can use this vault over your Wi-Fi, or from anywhere with Tailscale. Document Vault has to be open here for them to connect.")
                .font(.caption).foregroundStyle(.secondary)
            if let pairing {
                LabeledContent("Code (works once, for 10 minutes)") {
                    Text(pairing.code).font(.title2.monospaced().weight(.semibold)).textSelection(.enabled)
                }
                LabeledContent("Address") {
                    Text(pairing.urls.compactMap { URL(string: $0)?.host() }.joined(separator: "  or  ")).textSelection(.enabled)
                }
                LabeledContent("Security code") {
                    Text(RemoteVault.securityCode(pairing.fingerprint)).font(.body.monospaced()).textSelection(.enabled)
                }
            }
            if let problem { Text(problem).font(.caption).foregroundStyle(.red) }
            HStack {
                Button(pairing == nil ? "Show a Code" : "Show a New Code") {
                    Task {
                        do { pairing = try await model.client?.startPairing(typed: true); problem = nil }
                        catch { problem = error.localizedDescription }
                    }
                }
                Spacer()
                Button("Use Another Computer's Vault Instead…") { model.useAnotherComputer() }
            }
        }
    }
}
