// What the phone remembers about its vault, from the pairing QR code (Settings -> Phones on the Mac).

import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class PairingCode {
  PairingCode({required this.urls, required this.fingerprint, required this.code});

  /// Where the vault can be reached, tried in order: `https://<Mac on the Wi-Fi>:8443`, then its
  /// private-tunnel (Tailscale) address for when the phone is away from home.
  final List<String> urls;

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
    final urls = <String>{
      for (final u in [url, ...((j['urls'] as List?) ?? const [])])
        if (u is String && _secure(u)) u,
    }.toList();
    if (urls.isEmpty) {
      throw const FormatException('The pairing code has no secure (https) address.');
    }
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(fp)) {
      throw const FormatException('The pairing code has no valid certificate fingerprint.');
    }
    return PairingCode(urls: urls, fingerprint: fp, code: code);
  }

  static bool _secure(String url) {
    final uri = Uri.tryParse(url);
    return uri != null && uri.scheme == 'https' && uri.host.isNotEmpty;
  }
}

class Pairing {
  Pairing({required this.urls, required this.fingerprint, required this.token, required this.deviceId,
      required this.userName});

  /// Addresses to try, the last one that worked first (Wi-Fi at home, the tunnel when away).
  final List<String> urls;

  String get url => urls.first;
  final String fingerprint;
  final String token; // this phone's access key: kept in the Keychain / Keystore only
  final int deviceId;
  final String userName;

  Pairing withUrls(List<String> urls) =>
      Pairing(urls: urls, fingerprint: fingerprint, token: token, deviceId: deviceId, userName: userName);

  Map<String, dynamic> toJson() =>
      {'urls': urls, 'fp': fingerprint, 'token': token, 'device_id': deviceId, 'user': userName};

  factory Pairing.fromJson(Map<String, dynamic> j) => Pairing(
      urls: [...((j['urls'] as List?)?.cast<String>() ?? const []), if (j['url'] is String) j['url'] as String],
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
