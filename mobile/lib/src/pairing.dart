// What the phone remembers about its vault, from the pairing QR code (Settings -> Phones on the Mac).

import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class PairingCode {
  PairingCode({required this.url, required this.fingerprint, required this.code});

  /// https://<Mac's address on the Wi-Fi>:8443
  final String url;

  /// SHA-256 of the vault's certificate (hex). The app only talks to a server with exactly this certificate.
  final String fingerprint;

  /// One-time code, swapped for this phone's own access key.
  final String code;

  /// Parse a scanned QR code; throws FormatException with a readable message if it isn't one of ours.
  static PairingCode parse(String raw) {
    Map<String, dynamic> j;
    try {
      j = jsonDecode(raw.trim()) as Map<String, dynamic>;
    } catch (_) {
      throw const FormatException("That isn't a Document Vault pairing code.");
    }
    final url = j['url'], fp = j['fp'], code = j['code'];
    if (j['v'] != 1 || url is! String || fp is! String || code is! String) {
      throw const FormatException("That isn't a Document Vault pairing code.");
    }
    final uri = Uri.tryParse(url);
    if (uri == null || uri.scheme != 'https' || uri.host.isEmpty) {
      throw const FormatException('The pairing code has no secure (https) address.');
    }
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(fp)) {
      throw const FormatException('The pairing code has no valid certificate fingerprint.');
    }
    return PairingCode(url: url, fingerprint: fp, code: code);
  }
}

class Pairing {
  Pairing({required this.url, required this.fingerprint, required this.token, required this.deviceId,
      required this.userName});

  final String url;
  final String fingerprint;
  final String token; // this phone's access key: kept in the Keychain / Keystore only
  final int deviceId;
  final String userName;

  Map<String, dynamic> toJson() =>
      {'url': url, 'fp': fingerprint, 'token': token, 'device_id': deviceId, 'user': userName};

  factory Pairing.fromJson(Map<String, dynamic> j) => Pairing(
      url: j['url'] as String,
      fingerprint: j['fp'] as String,
      token: j['token'] as String,
      deviceId: j['device_id'] as int,
      userName: j['user'] as String);
}

/// Where the pairing and the app lock setting are kept: the iOS Keychain / Android Keystore.
class PairingStore {
  PairingStore([FlutterSecureStorage? storage]) : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;
  static const _pairingKey = 'pairing';
  static const _lockKey = 'app_lock';

  Future<Pairing?> load() async {
    final raw = await _storage.read(key: _pairingKey);
    if (raw == null) return null;
    try {
      return Pairing.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } catch (_) {
      return null;
    }
  }

  Future<void> save(Pairing p) => _storage.write(key: _pairingKey, value: jsonEncode(p.toJson()));

  Future<void> clear() => _storage.delete(key: _pairingKey);

  Future<bool> lockEnabled() async => (await _storage.read(key: _lockKey)) != 'off';

  Future<void> setLockEnabled(bool on) => _storage.write(key: _lockKey, value: on ? 'on' : 'off');
}
