import 'dart:io';

import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

typedef SharedFile = ({String path, String name});

/// Files sent to the vault with Android's "Send to → Document Vault" (from WhatsApp, Files, Gmail…).
///
/// Android copies them into the app's private cache (MainActivity.kt) and they wait here until
/// the app is paired and unlocked; then the Scan tab takes them and uploads them like a scan.
class ShareInbox extends ChangeNotifier {
  ShareInbox();

  /// The inbox fed by Android. Elsewhere (tests, other platforms) it just stays empty.
  factory ShareInbox.platform() {
    final inbox = ShareInbox();
    if (!kIsWeb && Platform.isAndroid) {
      const channel = MethodChannel('app.documentvault/share');
      Future<void> fetch() async {
        try {
          final files = await channel.invokeListMethod<Map>('takeShared') ?? [];
          inbox.add([for (final f in files) (path: f['path'] as String, name: f['name'] as String)]);
        } on PlatformException {
          // nothing shared, or an older build without the channel
        } on MissingPluginException {
          // same
        }
      }

      channel.setMethodCallHandler((call) async {
        if (call.method == 'shared') await fetch(); // shared while the app was already open
      });
      fetch(); // the app was opened by sharing
    }
    return inbox;
  }

  final _pending = <SharedFile>[];

  bool get hasFiles => _pending.isNotEmpty;

  void add(List<SharedFile> files) {
    if (files.isEmpty) return;
    _pending.addAll(files);
    notifyListeners();
  }

  /// Everything waiting, removed from the inbox.
  List<SharedFile> take() {
    final files = List.of(_pending);
    _pending.clear();
    return files;
  }

  /// The cache copies are only needed until they're uploaded (or queued, which makes its own copy).
  static Future<void> discard(List<SharedFile> files) async {
    for (final f in files) {
      try {
        await File(f.path).delete();
      } catch (_) {
        // already gone
      }
    }
  }
}
