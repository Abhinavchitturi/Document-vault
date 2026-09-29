// The app must talk only to the vault whose certificate fingerprint came in the QR code.
// A real HTTPS server here, with the self-signed certificate in test/fixtures/ (made like the
// Mac's, by backend/api/lan.py).

import 'dart:convert';
import 'dart:io';

import 'package:document_vault/src/pairing.dart';
import 'package:document_vault/src/vault_client.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  late HttpServer server;
  late String fingerprint;
  late String url;

  setUpAll(() async {
    final dir = Directory('test/fixtures');
    fingerprint = File('${dir.path}/fingerprint.txt').readAsStringSync().trim();
    final context = SecurityContext()
      ..useCertificateChain('${dir.path}/cert.pem')
      ..usePrivateKey('${dir.path}/key.pem');
    server = await HttpServer.bindSecure(InternetAddress.loopbackIPv4, 0, context);
    url = 'https://127.0.0.1:${server.port}';
    server.listen((req) async {
      final body = await utf8.decoder.bind(req).join();
      req.response.headers.contentType = ContentType.json;
      if (req.uri.path == '/api/mobile/pair' && (jsonDecode(body) as Map)['code'] == 'good-code') {
        req.response.write(jsonEncode({
          'token': 'device-key',
          'user': {'id': 1, 'name': 'abhinav', 'is_admin': true},
          'device': {'id': 3, 'name': 'iPhone'},
        }));
      } else if (req.uri.path == '/api/mobile/addresses' && req.headers.value('authorization') == 'Bearer device-key') {
        req.response.write(jsonEncode({'urls': [url, 'https://100.101.102.103:8443']}));
      } else if (req.uri.path == '/api/info' && req.headers.value('authorization') == 'Bearer device-key') {
        req.response.write(jsonEncode({'owner': 'abhinav', 'is_admin': true, 'num_documents': 2,
          'llm_status': {'ready': true, 'message': 'ok'}, 'ocr_available': true}));
      } else {
        req.response.statusCode = 401;
        req.response.write(jsonEncode({'detail': "This phone isn't paired (or was removed). Pair it again."}));
      }
      await req.response.close();
    });
  });

  tearDownAll(() => server.close(force: true));

  test('pairs with the pinned vault and then uses its access key', () async {
    final pairing = await VaultClient.pair(PairingCode(urls: [url], fingerprint: fingerprint, code: 'good-code'), 'iPhone');
    expect(pairing.token, 'device-key');
    expect(pairing.deviceId, 3);
    final client = VaultClient(pairing);
    expect((await client.info()).owner, 'abhinav');
    client.close();
  });

  test('away from home: an unreachable Wi-Fi address is skipped for the next one, and remembered', () async {
    final dead = 'https://127.0.0.1:1'; // nothing listens here, like the home Wi-Fi address on mobile data
    final pairing = await VaultClient.pair(PairingCode(urls: [dead, url], fingerprint: fingerprint, code: 'good-code'), 'Phone');
    expect(pairing.urls, [url, dead]); // the one that worked comes first
    final saved = <Pairing>[];
    final client = VaultClient(pairing.withUrls([dead, url]), onAddressesChanged: saved.add);
    expect((await client.info()).owner, 'abhinav');
    await Future<void>.delayed(const Duration(milliseconds: 300)); // the address refresh runs in the background
    expect(client.pairing.url, url);
    expect(client.pairing.urls, contains('https://100.101.102.103:8443')); // learned the tunnel address
    expect(saved, isNotEmpty);
    client.close();
  });

  test('refuses a server whose certificate is not the pinned one', () async {
    final wrong = 'cd' * 32;
    await expectLater(
      VaultClient.pair(PairingCode(urls: [url], fingerprint: wrong, code: 'good-code'), 'iPhone'),
      throwsA(isA<VaultException>().having((e) => e.message, 'message', contains("certificate doesn't match"))),
    );
  });

  test('a wrong pairing code is reported, and a revoked key means pair again', () async {
    await expectLater(
      VaultClient.pair(PairingCode(urls: [url], fingerprint: fingerprint, code: 'old'), 'iPhone'),
      throwsA(isA<VaultException>()),
    );
    final revoked = VaultClient(Pairing(urls: [url], fingerprint: fingerprint, token: 'revoked', deviceId: 1, userName: 'x'));
    await expectLater(revoked.info(), throwsA(isA<VaultException>().having((e) => e.unpaired, 'unpaired', isTrue)));
    revoked.close();
  });
}
