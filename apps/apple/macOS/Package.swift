// swift-tools-version:5.9
// The Mac app (SwiftUI). Build it with Xcode's toolchain: `swift build`, or open this folder in Xcode.
// build_app.sh wraps the result into "Document Vault.app".
import PackageDescription

let package = Package(
    name: "DocumentVaultMac",
    platforms: [.macOS(.v14)],
    dependencies: [.package(path: "../VaultKit")],
    targets: [.executableTarget(name: "DocumentVault", dependencies: ["VaultKit"])]
)
