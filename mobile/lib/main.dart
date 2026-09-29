// Document Vault for phones (Phase 7): chat with your documents, browse them, and scan new
// ones with the camera. Talks only to the family's vault on the Mac, over the home Wi-Fi.

import 'package:flutter/material.dart';

import 'src/app.dart';
import 'src/pairing.dart';
import 'src/vault_client.dart';

void main() {
  final store = PairingStore();
  runApp(VaultApp(store: store, connect: (p) => VaultClient(p, onAddressesChanged: store.save)));
}
