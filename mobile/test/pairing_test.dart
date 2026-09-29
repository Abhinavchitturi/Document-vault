import 'dart:convert';

import 'package:document_vault/src/models.dart';
import 'package:document_vault/src/pairing.dart';
import 'package:document_vault/src/vault_client.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_vault.dart';

void main() {
  final fp = 'ab' * 32;

  group('PairingCode.parse', () {
    test('reads the QR code from Settings -> Phones', () {
      final code = PairingCode.parse(jsonEncode({'v': 1, 'url': 'https://192.168.1.20:8443', 'fp': fp, 'code': 'abc'}));
      expect(code.urls, ['https://192.168.1.20:8443']);
      expect(code.fingerprint, fp);
      expect(code.code, 'abc');
    });

    test('keeps every address, Wi-Fi first then the tunnel, dropping insecure ones', () {
      final code = PairingCode.parse(jsonEncode({
        'v': 1, 'url': 'https://192.168.1.20:8443', 'fp': fp, 'code': 'abc',
        'urls': ['https://192.168.1.20:8443', 'https://100.101.102.103:8443', 'http://10.0.0.2:8443'],
      }));
      expect(code.urls, ['https://192.168.1.20:8443', 'https://100.101.102.103:8443']);
    });

    test('refuses anything else', () {
      for (final raw in [
        'https://example.com', // some other QR code
        jsonEncode({'v': 2, 'url': 'https://x', 'fp': fp, 'code': 'c'}),
        jsonEncode({'v': 1, 'url': 'http://192.168.1.20:8443', 'fp': fp, 'code': 'c'}), // not encrypted
        jsonEncode({'v': 1, 'url': 'https://192.168.1.20:8443', 'fp': 'short', 'code': 'c'}),
        jsonEncode({'v': 1, 'url': 'https://192.168.1.20:8443', 'fp': fp}),
      ]) {
        expect(() => PairingCode.parse(raw), throwsFormatException, reason: raw);
      }
    });
  });

  test('the pairing is kept in secure storage, and the lock is on by default', () async {
    final store = PairingStore(MemoryStorage());
    expect(await store.load(), isNull);
    expect(await store.lockEnabled(), isTrue);
    await store.save(testPairing);
    final back = await store.load();
    expect(back!.token, 'secret-token');
    expect(back.deviceId, 7);
    await store.setLockEnabled(false);
    expect(await store.lockEnabled(), isFalse);
    await store.clear();
    expect(await store.load(), isNull);
  });

  test('chat stream is split into events even when lines arrive in pieces', () async {
    final lines = [
      '{"type":"sources","sources":[{"rank":1,"score":0.1,"chunk_id":"c","doc_id":"d","filename":"f.pdf",'
          '"category":"bill","page":2,"text":"t"}]}\n{"type":"tok',
      'en","text":"Rs. "}\n{"type":"token","text":"2,486"}\n',
      '{"type":"done","citations":[1],"found":true,"llm":"x"}\n',
    ];
    final events = await parseNdjson(Stream.fromIterable(lines.map(utf8.encode))).toList();
    expect(events.length, 4);
    expect((events[0] as SourcesEvent).sources.single.page, 2);
    expect(events.whereType<TokenEvent>().map((e) => e.text).join(), 'Rs. 2,486');
    expect(events.last, isA<DoneEvent>());
  });

  test('documents flag hard-to-read scans', () {
    final d = VaultDocument.fromJson({
      'doc_id': 'x', 'filename': 'a.jpg', 'category': 'bill', 'pages': 1, 'ocr_pages': 1, 'ocr_confidence': 40,
      'size_bytes': 1, 'added_at': '2026-09-01T10:00:00+00:00',
    });
    expect(d.hardToRead, isTrue);
  });
}
