import SwiftUI
import VaultKit

/// Sign in, or (the first time the vault opens) choose the admin's name and password.
struct SignInView: View {
    @Environment(AppModel.self) private var model
    let status: AuthStatus
    @State private var name = ""
    @State private var password = ""
    @State private var again = ""
    @State private var busy = false
    @State private var problem: String?

    private var firstTime: Bool { status.setupNeeded }
    private var ready: Bool {
        !name.trimmingCharacters(in: .whitespaces).isEmpty && password.count >= 8 && (!firstTime || password == again)
    }

    var body: some View {
        VStack(spacing: 22) {
            AppIcon(size: 72)
            VStack(spacing: 6) {
                Text(firstTime ? "Welcome to Document Vault" : "Document Vault").font(.title.weight(.semibold))
                Text(firstTime ? "Choose the name and password for this vault's admin. Your documents never leave this Mac."
                               : "Your family's papers, answered privately on this Mac.")
                    .foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            Form {
                TextField("Name", text: $name).textContentType(.username)
                SecureField("Password", text: $password, prompt: Text(firstTime ? "8 or more characters" : ""))
                    .textContentType(firstTime ? .newPassword : .password)
                if firstTime {
                    SecureField("Password again", text: $again).textContentType(.newPassword)
                }
            }
            .formStyle(.grouped)
            .frame(width: 380)
            .scrollDisabled(true)
            .fixedSize(horizontal: false, vertical: true)
            .onSubmit(submit)
            if let problem {
                Label(problem, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.red).font(.callout)
            }
            Button(action: submit) {
                Text(busy ? "Signing in…" : firstTime ? "Create Vault" : "Sign In").frame(width: 200)
            }
            .controlSize(.large)
            .buttonStyle(.borderedProminent)
            .keyboardShortcut(.defaultAction)
            .disabled(!ready || busy)
            if firstTime && model.remote == nil {  // 1.0: or keep a vault another computer keeps
                Button("Join a vault kept on another computer instead") { model.phase = .joining }.buttonStyle(.link)
            }
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear { name = status.suggestedName ?? "" }
    }

    private func submit() {
        guard ready, !busy else { return }
        busy = true
        problem = nil
        Task {
            do {
                try await model.signIn(name: name.trimmingCharacters(in: .whitespaces), password: password, firstTime: firstTime)
            } catch {
                problem = error.localizedDescription
                password = ""
                again = ""
            }
            busy = false
        }
    }
}
