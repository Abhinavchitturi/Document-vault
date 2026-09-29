# Document Vault: phone app (Phase 7)

Flutter app for iPhone and Android. It talks only to the family's vault on the Mac, over the
home Wi-Fi. Documents, search and answers all stay on the Mac.

| Tab | What it does |
|---|---|
| **Chat** | Ask about your documents. Answers stream in with numbered sources; tap one to see the page with the answer highlighted |
| **Documents** | Browse and search by name or type; open a document's page or the original file; remove one |
| **Scan** | Photograph a bill, policy or warranty card (or pick a photo, PDF or Word file). The Mac reads photos with OCR and says if one was hard to read |
| **Settings** | Face ID / fingerprint lock, which vault it's paired with, unpair |

## How it connects (and why it's safe on the Wi-Fi)
1. Start the vault on the Mac with phone access:
   ```bash
   python -m backend.api.app --phones
   ```
   Besides http://localhost:8000 for the Mac's browser, it now serves
   `https://<Mac's Wi-Fi address>:8443`, for paired phones only (no web pages, no passwords).
2. On the Mac: **Settings → Phones → Pair a phone** shows a QR code (one use, 10 minutes).
3. In the app: **Scan pairing code**.

The QR code carries the vault's address, the SHA-256 fingerprint of its certificate (made on
the Mac, in `data/tls/`), and a one-time code. The app then:
- trusts **only** that certificate (no certificate authority at all), so another device on the
  Wi-Fi can't pose as the vault or read the traffic;
- swaps the code for its own random access key, kept in the iOS Keychain / Android Keystore.

Remove a phone any time in the Mac's **Settings → Phones**; resetting someone's password also
unpairs their phones. If the Mac's address changes (new router), pair again.

The app locks with Face ID / Touch ID / fingerprint (the phone's own lock, with its passcode as
fallback) when opened and after 2 minutes in the background.

## Run it on your phone
You need Flutter (https://docs.flutter.dev/get-started/install) on the Mac.

**iPhone** (needs Xcode; a free Apple ID is enough for your own phone):
```bash
cd mobile
flutter pub get
open ios/Runner.xcworkspace
```
In Xcode: Runner → Signing & Capabilities → pick your Team (your Apple ID). Plug in the
iPhone, then:
```bash
flutter run --release
```
(With a free Apple ID the app must be reinstalled every 7 days.)

**Android** (needs Android Studio for the Android SDK):
```bash
cd mobile
flutter build apk --release
```
Copy `build/app/outputs/flutter-apk/app-release.apk` to the phone and open it (allow "install
unknown apps" for your file manager), or plug the phone in and run `flutter run --release`.

## Develop
```bash
cd mobile
flutter analyze
flutter test
```
The tests use an in-memory vault (`test/fake_vault.dart`), and a real HTTPS server for the
certificate pinning.

| File | What |
|---|---|
| `lib/src/vault_client.dart` | HTTPS client pinned to the vault's certificate; every API call; chat stream parsing |
| `lib/src/pairing.dart` | Reading the QR code; keeping the pairing in the Keychain / Keystore |
| `lib/src/app.dart` | Pairing → lock → tabs; returns to pairing if the Mac removed this phone |
| `lib/src/screens/` | Pair, Lock, Chat, Documents, Source (highlighted page), Scan, Settings |
| `lib/src/widgets/answer_text.dart` | Answers with **bold** facts and tappable [1] citations |
