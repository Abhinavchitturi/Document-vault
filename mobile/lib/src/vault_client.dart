// Talking to the vault on the Mac, over HTTPS pinned to the certificate from the pairing QR code.

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:http/http.dart' as http;
import 'package:http/io_client.dart';

import 'models.dart';
import 'phone_reminders.dart';
import 'pairing.dart';

/// Something the person can act on; [message] is safe to show.
class VaultException implements Exception {
  VaultException(this.message, {this.unpaired = false, this.offline = false});

  final String message;

  /// The Mac didn't answer (off, asleep, vault not running, or no network): worth trying again later.
  final bool offline;

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
    ..connectionTimeout = const Duration(seconds: 4) // an address from another network fails fast
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
  Future<List<UploadResult>> upload(List<({String path, String name})> files, String category, {String? password});
  Future<void> unpair(int deviceId);

  /// Every upcoming reminder for this person, to schedule on the phone (Phase 10).
  Future<List<ScheduledReminder>> reminderSchedule();
}

class VaultClient implements VaultApi {
  VaultClient(Pairing pairing, {http.Client? client, this.onAddressesChanged})
      : _pairing = pairing,
        _http = client ?? pinnedClient(pairing.fingerprint);

  Pairing _pairing;
  final http.Client _http;

  /// Called with the updated pairing when the vault's addresses change or another one worked,
  /// so the app can save it (the Mac's Wi-Fi address can change; the tunnel one rarely does).
  final void Function(Pairing)? onAddressesChanged;
  bool _refreshed = false;

  Pairing get pairing => _pairing;

  Map<String, String> get _auth => {'Authorization': 'Bearer ${_pairing.token}'};

  /// Pair a new phone: swap the QR code's one-time code for this phone's access key.
  static Future<Pairing> pair(PairingCode code, String deviceName, {http.Client? client}) async {
    final http_ = client ?? pinnedClient(code.fingerprint);
    try {
      final (res, url) = await _firstThatAnswers(code.urls, (base) {
        return http.Request('POST', base.replace(path: '/api/mobile/pair'))
          ..headers['Content-Type'] = 'application/json'
          ..body = jsonEncode({'code': code.code, 'device_name': deviceName});
      }, http_, const Duration(seconds: 15));
      final body = _decode(await http.Response.fromStream(res));
      return Pairing(
          urls: [url, ...code.urls.where((u) => u != url)],
          fingerprint: code.fingerprint,
          token: body['token'] as String,
          deviceId: (body['device'] as Map)['id'] as int,
          userName: (body['user'] as Map)['name'] as String);
    } on _WrongCertificate {
      throw VaultException("The Mac's certificate doesn't match the pairing code. Show a new QR code and try again.");
    } on _Unreachable {
      throw VaultException(_offline);
    } finally {
      if (client == null) http_.close();
    }
  }

  static const _offline = "Can't reach the vault. Is the vault running on the Mac with --phones, and is the phone "
      'on the same Wi-Fi, or connected to Tailscale when away from home?';

  /// Send to each address in turn until one answers (a Wi-Fi address times out quickly when the
  /// phone is away from home, then the tunnel address is tried).
  static Future<(http.StreamedResponse, String)> _firstThatAnswers(List<String> urls,
      http.BaseRequest Function(Uri base) build, http.Client client, Duration timeout) async {
    var wrongCertificate = false;
    for (final url in urls) {
      try {
        final res = await client.send(build(Uri.parse(url))).timeout(timeout);
        return (res, url);
      } on HandshakeException {
        wrongCertificate = true; // some other device answered at this address: not our vault
      } on TlsException {
        wrongCertificate = true;
      } catch (_) {
        // not reachable from this network: try the next address
      }
    }
    throw wrongCertificate ? _WrongCertificate() : _Unreachable();
  }

  Future<http.StreamedResponse> _request(http.BaseRequest Function(Uri base) build, Duration timeout) async {
    try {
      final (res, url) = await _firstThatAnswers(_pairing.urls, build, _http, timeout);
      if (url != _pairing.url) {
        _pairing = _pairing.withUrls([url, ..._pairing.urls.where((u) => u != url)]);
        onAddressesChanged?.call(_pairing);
      }
      if (!_refreshed && res.statusCode == 200) {
        _refreshed = true;
        unawaited(_refreshAddresses());
      }
      return res;
    } on _WrongCertificate {
      throw VaultException("Reached a server that isn't your vault (the certificate doesn't match). "
          'If you replaced the Mac\'s certificate, pair this phone again.');
    } on _Unreachable {
      throw VaultException(_offline, offline: true);
    }
  }

  /// Learn the vault's current addresses (e.g. the Mac got a new Wi-Fi address).
  Future<void> _refreshAddresses() async {
    try {
      final res = await _http.get(Uri.parse(_pairing.url).replace(path: '/api/mobile/addresses'), headers: _auth)
          .timeout(const Duration(seconds: 10));
      if (res.statusCode != 200) return;
      final fresh = ((jsonDecode(res.body) as Map)['urls'] as List).cast<String>();
      final merged = <String>{_pairing.url, ...fresh, ..._pairing.urls}.toList();
      if (merged.join() != _pairing.urls.join()) {
        _pairing = _pairing.withUrls(merged);
        onAddressesChanged?.call(_pairing);
      }
    } catch (_) {
      // best effort
    }
  }

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

  http.Request Function(Uri) _simple(String method, String path, {Object? json, Map<String, String>? query}) =>
      (base) {
        final req = http.Request(method, base.replace(path: path, queryParameters: query))..headers.addAll(_auth);
        if (json != null) {
          req.headers['Content-Type'] = 'application/json';
          req.body = jsonEncode(json);
        }
        return req;
      };

  Future<dynamic> _call(String method, String path, {Object? json, Duration timeout = const Duration(seconds: 30)}) async =>
      _decode(await http.Response.fromStream(await _request(_simple(method, path, json: json), timeout)));

  Future<Uint8List> _bytes(String path) async {
    final res = await http.Response.fromStream(await _request(_simple('GET', path), const Duration(seconds: 60)));
    if (res.statusCode != 200) _decode(res);
    return res.bodyBytes;
  }

  @override
  Future<Info> info() async => Info.fromJson(await _call('GET', '/api/info') as Map<String, dynamic>);

  @override
  Future<List<VaultDocument>> documents() async => ((await _call('GET', '/api/documents')) as List)
      .map((d) => VaultDocument.fromJson(d as Map<String, dynamic>))
      .toList();

  @override
  Future<void> deleteDocument(String id) => _call('DELETE', '/api/documents/${Uri.encodeComponent(id)}');

  @override
  Stream<ChatEvent> chat(String question, List<Map<String, String>> history) async* {
    final body = {'question': question, 'history': history.length > 6 ? history.sublist(history.length - 6) : history};
    http.StreamedResponse res;
    try {
      res = await _request(_simple('POST', '/api/chat', json: body), const Duration(seconds: 30));
    } on VaultException catch (e) {
      yield ErrorEvent(e.message);
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
  Future<Highlights> highlights(String docId, int page, {String? chunkId, String? answer}) async => Highlights.fromJson(
      await _call('POST', '/api/documents/${Uri.encodeComponent(docId)}/pages/$page/highlights', json: {
        if (chunkId != null && chunkId.isNotEmpty) 'chunk_id': chunkId,
        if (answer != null && answer.isNotEmpty) 'answer': answer,
      }) as Map<String, dynamic>);

  @override
  Future<Uint8List> pageImage(String docId, int page) => _bytes('/api/documents/${Uri.encodeComponent(docId)}/pages/$page/image');

  @override
  Future<Uint8List> file(String docId) => _bytes('/api/documents/${Uri.encodeComponent(docId)}/file');

  @override
  Future<List<UploadResult>> upload(List<({String path, String name})> files, String category, {String? password}) async {
    final parts = [for (final f in files) (f, await File(f.path).readAsBytes())];
    // OCR of a photo can take a little while on the Mac.
    final res = await http.Response.fromStream(await _request((base) {
      final req = http.MultipartRequest('POST', base.replace(path: '/api/documents'))
        ..headers.addAll(_auth)
        ..fields['category'] = category;
      if (password != null && password.isNotEmpty) req.fields['password'] = password;
      for (final (f, bytes) in parts) {
        req.files.add(http.MultipartFile.fromBytes('files', bytes, filename: f.name));
      }
      return req;
    }, const Duration(minutes: 3)));
    return (_decode(res) as List).map((r) => UploadResult.fromJson(r as Map<String, dynamic>)).toList();
  }

  @override
  Future<void> unpair(int deviceId) => _call('DELETE', '/api/devices/$deviceId');

  @override
  Future<List<ScheduledReminder>> reminderSchedule() async =>
      (((await _call('GET', '/api/reminders/schedule')) as Map)['reminders'] as List)
          .map((r) => ScheduledReminder.fromJson(r as Map<String, dynamic>))
          .toList();

  void close() => _http.close();
}

class _WrongCertificate implements Exception {}

class _Unreachable implements Exception {}

/// Split the chat stream into events: one JSON object per line (NDJSON), arriving in pieces.
Stream<ChatEvent> parseNdjson(Stream<List<int>> bytes) =>
    bytes.transform(utf8.decoder).transform(const LineSplitter()).where((l) => l.trim().isNotEmpty).map(
        (l) => ChatEvent.fromJson(jsonDecode(l) as Map<String, dynamic>));
