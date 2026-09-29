import 'dart:io';

import 'package:document_vault/src/app.dart';
import 'package:document_vault/src/pairing.dart';
import 'package:document_vault/src/upload_queue.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_vault.dart';

void main() {
  late Directory tmp;
  late File photo;

  setUp(() {
    tmp = Directory.systemTemp.createTempSync('queue');
    photo = File('${tmp.path}/camera.jpg')..writeAsBytesSync([1, 2, 3]);
  });

  test('a scan waits on the phone while the Mac is off, then is sent and deleted', () async {
    final queue = UploadQueue(tmp);
    final vault = FakeVault()..macOff = true;
    await queue.add([(path: photo.path, name: 'Gas bill.jpg')], 'bill');
    photo.deleteSync(); // the camera's temporary file can vanish: the queue has its own copy
    expect((await queue.list()).single.name, 'Gas bill.jpg');

    expect(await queue.send(vault), isEmpty); // still off: kept
    expect(await queue.list(), hasLength(1));

    vault.macOff = false;
    final sent = await queue.send(vault);
    expect(sent.single.status, 'added');
    expect(vault.uploads.single.single.name, 'Gas bill.jpg');
    expect(await queue.list(), isEmpty); // nothing left on the phone
  });

  testWidgets('scans saved while the Mac was off are sent when the app is open again', (tester) async {
    final queue = UploadQueue(tmp);
    await tester.runAsync(() => queue.add([(path: photo.path, name: 'Photo.jpg')], 'auto'));
    final vault = FakeVault(); // the Mac is back on
    final store = PairingStore(MemoryStorage());
    await tester.runAsync(() async {
      await store.save(testPairing);
      await store.setLockEnabled(false);
    });
    await tester.pumpWidget(VaultApp(store: store, connect: (_) => vault, storageDir: tmp));
    for (var i = 0; i < 60 && vault.uploads.isEmpty; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20))); // real file reads, a step at a time
      await tester.pump();
    }
    expect(vault.uploads.single.single.name, 'Photo.jpg');
    var left = await tester.runAsync(queue.list);
    for (var i = 0; i < 60 && left!.isNotEmpty; i++) {
      await tester.pump();
      left = await tester.runAsync(queue.list); // deleting the phone's copy is real file work too
    }
    expect(left, isEmpty);
    await tester.tap(find.text('Scan'));
    for (var i = 0; i < 60 && find.textContaining('Added as').evaluate().isEmpty; i++) {
      await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 20)));
      await tester.pump();
    }
    expect(find.textContaining('Added as'), findsOneWidget); // the result is shown on the Scan tab
  });
}
