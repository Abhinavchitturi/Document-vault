// Talking to the vault on the Mac, over HTTPS pinned to the certificate from the pairing QR code.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import 'models.dart';
import 'pairing.dart';

/// Something the person can act on; [message] is safe to show.
class VaultException implements Exception {
  VaultException(this.message, {this.unpaired = false});

  final String message;

  /// The vault no longer knows this phone (removed in Settings, or the password was reset): pair again.
  final bool unpaired;

  @override
  String toString() => message;
}

/// An HTTP client that accepts exactly one server certificate: the one whose SHA-256 is [fingerprint].
///
/// No certificate authorities are trusted at all, so every certificate goes to
/// badCertificateCallback, which compares fingerprints. A look-alike server on the Wi-Fi,
/// or one with a "valid" certificate from some CA, is refused just the same.
http.Client pinnedClient(String fingerprint) {
  final io = HttpClient(context: SecurityContext(withTrustedRoots: false))
    ..connectionTimeout = const Duration(seconds: 8)
    ..idleTimeout = const Duration(seconds: 30)
    ..badCertificateCallback = (X509Certificate cert, String host, int port) =>
        sha256.convert(cert.der).toString() == fingerprint.toLowerCase();
  return IOClient(io);
}

/// Everything the screens need from the vault (a fake implements it in the widget tests).
abstract class VaultApi {
  Future<Info> info();
  Future<List<VaultDocument>> documents();
  Future<void> deleteDocument(String id);
  Stream<ChatEvent> chat(String question, List<Map<String, String>> history);
  Future<Highlights> highlights(String docId, int page, {String? chunkId, String? answer});
  Future<Uint8List> pageImage(String docId, int page);
  Future<Uint8List> file(String docId);
  Future<List<UploadResult>> upload(List<({String path, String name})> files, String category);
  Future<void> unpair(int deviceId);
}

class VaultClient implements VaultApi {
  VaultClient(this.pairing, {http.Client? client}) : _http = client ?? pinnedClient(pairing.fingerprint);

  final Pairing pairing;
  final http.Client _http;

  Uri _u(String path, [Map<String, String>? query]) => Uri.parse(pairing.url).replace(path: path, queryParameters: query);

  Map<String, String> get _auth => {'Authorization': 'Bearer ${pairing.token}'};

  /// Pair a new phone: swap the QR code's one-time code for this phone's access key.
  static Future<Pairing> pair(PairingCode code, String deviceName, {http.Client? client}) async {
    final http_ = client ?? pinnedClient(code.fingerprint);
    try {
      final res = await http_
          .post(Uri.parse(code.url).replace(path: '/api/mobile/pair'),
              headers: {'Content-Type': 'application/json'},
              body: jsonEncode({'code': code.code, 'device_name': deviceName}))
          .timeout(const Duration(seconds: 15));
      final body = _decode(res);
      return Pairing(
          url: code.url,
          fingerprint: code.fingerprint,
          token: body['token'] as String,
          deviceId: (body['device'] as Map)['id'] as int,
          userName: (body['user'] as Map)['name'] as String);
    } on VaultException {
      rethrow;
    } on HandshakeException {
      throw VaultException("The Mac's certificate doesn't match the pairing code. Show a new QR code and try again.");
    } catch (e) {
      throw VaultException(_offline(code.url));
    } finally {
      if (client == null) http_.close();
    }
  }

  static String _offline(String url) =>
      "Can't reach the vault at $url. Is the phone on the same Wi-Fi as the Mac, and is the vault running with --phones?";

  static dynamic _decode(http.Response res) {
    dynamic body;
    try {
      body = jsonDecode(utf8.decode(res.bodyBytes));
    } catch (_) {
      body = null;
    }
    if (res.statusCode >= 200 && res.statusCode < 300) return body;
    final detail = body is Map && body['detail'] is String ? body['detail'] as String : 'Request failed (${res.statusCode})';
    throw VaultException(detail, unpaired: res.statusCode == 401);
  }

  Future<dynamic> _get(String path, [Map<String, String>? query]) async {
    try {
      return _decode(await _http.get(_u(path, query), headers: _auth).timeout(const Duration(seconds: 30)));
    } on VaultException {
      rethrow;
    } on HandshakeException {
      throw VaultException("The vault's certificate changed (new network address?). Pair this phone again.", unpaired: true);
    } catch (_) {
      throw VaultException(_offline(pairing.url));
    }
  }

  Future<dynamic> _send(String method, String path, [Object? json]) async {
    final req = http.Request(method, _u(path))..headers.addAll(_auth);
    if (json != null) {
      req.headers['Content-Type'] = 'application/json';
      req.body = jsonEncode(json);
    }
    try {
      return _decode(await http.Response.fromStream(await _http.send(req).timeout(const Duration(seconds: 60))));
    } on VaultException {
      rethrow;
    } on HandshakeException {
      throw VaultException("The vault's certificate changed (new network address?). Pair this phone again.", unpaired: true);
    } catch (_) {
      throw VaultException(_offline(pairing.url));
    }
  }

  Future<Uint8List> _bytes(String path) async {
    try {
      final res = await _http.get(_u(path), headers: _auth).timeout(const Duration(seconds: 60));
      if (res.statusCode != 200) _decode(res);
      return res.bodyBytes;
    } on VaultException {
      rethrow;
    } catch (_) {
      throw VaultException(_offline(pairing.url));
    }
  }

  @override
  Future<Info> info() async => Info.fromJson(await _get('/api/info') as Map<String, dynamic>);

  @override
  Future<List<VaultDocument>> documents() async =>
      ((await _get('/api/documents')) as List).map((d) => VaultDocument.fromJson(d as Map<String, dynamic>)).toList();

  @override
  Future<void> deleteDocument(String id) => _send('DELETE', '/api/documents/${Uri.encodeComponent(id)}');

  @override
  Stream<ChatEvent> chat(String question, List<Map<String, String>> history) async* {
    final req = http.Request('POST', _u('/api/chat'))
      ..headers.addAll({..._auth, 'Content-Type': 'application/json'})
      ..body = jsonEncode({'question': question, 'history': history.length > 6 ? history.sublist(history.length - 6) : history});
    http.StreamedResponse res;
    try {
      res = await _http.send(req).timeout(const Duration(seconds: 30));
    } catch (_) {
      yield ErrorEvent(_offline(pairing.url));
      return;
    }
    if (res.statusCode != 200) {
      try {
        _decode(await http.Response.fromStream(res));
      } on VaultException catch (e) {
        if (e.unpaired) rethrow;
        yield ErrorEvent(e.message);
      }
      return;
    }
    yield* parseNdjson(res.stream);
  }

  @override
  Future<Highlights> highlights(String docId, int page, {String? chunkId, String? answer}) async =>
      Highlights.fromJson(await _send('POST', '/api/documents/${Uri.encodeComponent(docId)}/pages/$page/highlights',
          {if (chunkId != null && chunkId.isNotEmpty) 'chunk_id': chunkId, if (answer != null && answer.isNotEmpty) 'answer': answer})
          as Map<String, dynamic>);

  @override
  Future<Uint8List> pageImage(String docId, int page) => _bytes('/api/documents/${Uri.encodeComponent(docId)}/pages/$page/image');

  @override
  Future<Uint8List> file(String docId) => _bytes('/api/documents/${Uri.encodeComponent(docId)}/file');

  @override
  Future<List<UploadResult>> upload(List<({String path, String name})> files, String category) async {
    final req = http.MultipartRequest('POST', _u('/api/documents'))
      ..headers.addAll(_auth)
      ..fields['category'] = category;
    for (final f in files) {
      req.files.add(await http.MultipartFile.fromPath('files', f.path, filename: f.name));
    }
    try {
      // OCR of a photo can take a little while on the Mac.
      final res = await http.Response.fromStream(await _http.send(req).timeout(const Duration(minutes: 3)));
      return ((_decode(res)) as List).map((r) => UploadResult.fromJson(r as Map<String, dynamic>)).toList();
    } on VaultException {
      rethrow;
    } catch (_) {
      throw VaultException(_offline(pairing.url));
    }
  }

  @override
  Future<void> unpair(int deviceId) => _send('DELETE', '/api/devices/$deviceId');

  void close() => _http.close();
}

/// Split the chat stream into events: one JSON object per line (NDJSON), arriving in pieces.
Stream<ChatEvent> parseNdjson(Stream<List<int>> bytes) =>
    bytes.transform(utf8.decoder).transform(const LineSplitter()).where((l) => l.trim().isNotEmpty).map(
        (l) => ChatEvent.fromJson(jsonDecode(l) as Map<String, dynamic>));
