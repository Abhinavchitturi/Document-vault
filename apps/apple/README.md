# Document Vault: native Apple apps

| Folder | What it is | Status |
|---|---|---|
| `VaultKit/` | Shared Swift package: talks to the vault, keeps the sign-in in the Keychain, models the vault's data | Working, tested |
| `macOS/` | The Mac app (SwiftUI) | Next |
| `iOS/` | The iPhone app (SwiftUI) | Later |

**How the Mac app reaches the vault:** it starts the vault's engine (`python -m backend.engine`, see
`backend/engine/bridge.py`) as a hidden helper and talks to it over a private pipe: one JSON object per line on the
engine's stdin and stdout. Nothing listens on the network for the app. The iPhone app will use the same `VaultClient`
over the network (Wi-Fi or Tailscale), because a phone can only reach the computer that way.

## Tests

```bash
cd apps/apple/VaultKit
swift test
```

With only Apple's Command Line Tools (no Xcode) installed, add
`-Xswiftc -plugin-path -Xswiftc /Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing`.
One test starts the real engine from the project's `venv`, against a throwaway vault, and goes through sign-up, upload,
listing, downloading the original, the chat and sign-out over the pipe.

The SwiftUI apps themselves need Xcode.
