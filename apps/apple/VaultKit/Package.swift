// swift-tools-version:5.9
// VaultKit: everything the native Apple apps share that isn't a screen. It talks to the vault (over a private pipe to
// the engine on the Mac; over the network on the iPhone), keeps the sign-in in the Keychain, and models the vault's data.
import PackageDescription

let package = Package(
    name: "VaultKit",
    platforms: [.macOS(.v14), .iOS(.v17)],
    products: [.library(name: "VaultKit", targets: ["VaultKit"])],
    targets: [
        .target(name: "VaultKit"),
        .testTarget(name: "VaultKitTests", dependencies: ["VaultKit"]),
    ]
)
