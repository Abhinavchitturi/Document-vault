import 'dart:io';

import 'package:document_vault/src/app.dart';
import 'package:document_vault/src/pairing.dart';
import 'package:document_vault/src/vault_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_vault.dart';

Future<PairingStore> pairedStore({bool lock = false}) async {
  final store = PairingStore(MemoryStorage());
  await store.save(testPairing);
  await store.setLockEnabled(lock);
  return store;
}

void main() {
  testWidgets('an unpaired phone shows how to pair', (tester) async {
    await tester.pumpWidget(VaultApp(reminders: FakeScheduler(), storageDir: Directory.systemTemp.createTempSync('vault'), store: PairingStore(MemoryStorage()), connect: (_) => FakeVault()));
    await tester.pumpAndSettle();
    expect(find.text('Scan pairing code'), findsOneWidget);
    expect(find.textContaining('Settings → Phones'), findsOneWidget);
  });

  testWidgets('the app stays locked until Face ID / fingerprint succeeds', (tester) async {
    var allow = false;
    await tester.pumpWidget(VaultApp(reminders: FakeScheduler(), storageDir: Directory.systemTemp.createTempSync('vault'), 
        store: await pairedStore(lock: true), connect: (_) => FakeVault(), authenticate: () async => allow));
    await tester.pumpAndSettle();
    expect(find.text('Not unlocked. Try again.'), findsOneWidget);
    expect(find.text('Chat'), findsNothing);

    allow = true;
    await tester.tap(find.text('Unlock'));
    await tester.pumpAndSettle();
    expect(find.text('Ask about your documents'), findsOneWidget);
  });

  testWidgets('chat streams an answer with its sources, and a citation opens the page', (tester) async {
    final vault = FakeVault();
    await tester.pumpWidget(VaultApp(reminders: FakeScheduler(), storageDir: Directory.systemTemp.createTempSync('vault'), store: await pairedStore(), connect: (_) => vault));
    await tester.pumpAndSettle();

    await tester.enterText(find.byType(TextField), 'How much is the electricity bill for August?');
    await tester.testTextInput.receiveAction(TextInputAction.send);
    await tester.pumpAndSettle();

    expect(vault.asked, ['How much is the electricity bill for August?']);
    expect(find.textContaining('Rs. 2,486', findRichText: true), findsWidgets);
    expect(find.textContaining('Electricity Bill 2026 08.pdf · p.1'), findsOneWidget);

    await tester.tap(find.textContaining('Electricity Bill 2026 08.pdf · p.1'));
    await tester.pumpAndSettle();
    expect(find.text('Answer: Rs. 2,486'), findsOneWidget); // the source page, with the answer highlighted
  });

  testWidgets('documents list flags hard-to-read scans and filters by type', (tester) async {
    await tester.pumpWidget(VaultApp(reminders: FakeScheduler(), storageDir: Directory.systemTemp.createTempSync('vault'), store: await pairedStore(), connect: (_) => FakeVault()));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Documents'));
    await tester.pumpAndSettle();
    expect(find.text('Electricity Bill 2026 08.pdf'), findsOneWidget);
    expect(find.textContaining('hard to read'), findsOneWidget);

    await tester.tap(find.widgetWithText(ChoiceChip, 'Bills'));
    await tester.pumpAndSettle();
    expect(find.text('Blurry Receipt.jpg'), findsNothing);
  });

  testWidgets('a phone removed on the Mac goes back to pairing', (tester) async {
    final vault = FakeVault()..failWith = VaultException('This phone isn\'t paired', unpaired: true);
    final store = await pairedStore();
    await tester.pumpWidget(VaultApp(reminders: FakeScheduler(), storageDir: Directory.systemTemp.createTempSync('vault'), store: store, connect: (_) => vault));
    await tester.pumpAndSettle(); // the first call to the vault is refused: this phone was removed on the Mac
    expect(find.text('Scan pairing code'), findsOneWidget);
    expect(await store.load(), isNull);
  });

  testWidgets('unpairing from Settings tells the Mac and forgets the vault', (tester) async {
    tester.view.physicalSize = const Size(1080, 2600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final vault = FakeVault();
    final store = await pairedStore();
    await tester.pumpWidget(VaultApp(reminders: FakeScheduler(), storageDir: Directory.systemTemp.createTempSync('vault'), store: store, connect: (_) => vault));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Home Wi-Fi: https://192.168.1.20:8443'), findsOneWidget);
    await tester.tap(find.text('Unpair this phone'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Unpair'));
    await tester.pumpAndSettle();
    expect(vault.unpairedCalled, isTrue);
    expect(find.text('Scan pairing code'), findsOneWidget);
  });
}

