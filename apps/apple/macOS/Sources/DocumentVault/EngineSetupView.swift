import SwiftUI
import VaultKit

/// This Mac keeps the vault: download the vault's engine (once), with a progress bar. A Mac that only connects to
/// another computer never sees this.
struct EngineSetupView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 16) {
            AppIcon(size: 72)
            Text("Setting up this Mac as the vault").font(.title.weight(.semibold))
            Text("Downloading the vault's engine\(size): what reads, searches and keeps your documents on this Mac. Your documents never leave it.")
                .multilineTextAlignment(.center).foregroundStyle(.secondary)
            if let problem = model.engineProblem {
                Label(problem, systemImage: "exclamationmark.triangle").foregroundStyle(.red).multilineTextAlignment(.center)
                HStack {
                    Button("Use the Vault on Another Computer Instead") { model.useAnotherComputer() }
                    Button("Try Again") { Task { await model.setUpEngine() } }.keyboardShortcut(.defaultAction)
                }
            } else if let step = model.engineProgress, step.step == "downloading", step.total > 0 {
                ProgressView(value: Double(step.done), total: Double(step.total))
                Text("Downloading: \(step.done / 1_000_000) of \(step.total / 1_000_000) MB").font(.callout).foregroundStyle(.secondary)
            } else {
                ProgressView().progressViewStyle(.linear)
                Text(model.engineProgress?.step == "unpacking" ? "Unpacking…" : "Starting the download…")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: 480)
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var size: String {
        guard let bytes = AppModel.release?.size, bytes > 0 else { return "" }
        return " (\(bytes / 1_000_000) MB, once)"
    }
}
