import 'dart:io';

import 'package:document_vault/src/app.dart';
import 'package:document_vault/src/pairing.dart';
import 'package:document_vault/src/phone_reminders.dart';
import 'package:document_vault/src/vault_client.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'fake_vault.dart';

Future<PairingStore> paired() async {
  final store = PairingStore(MemoryStorage());
  await store.save(testPairing);
  await store.setLockEnabled(false);
  return store;
}

void main() {
  test('the schedule from the vault is read with local times', () {
    final r = ScheduledReminder.fromJson(
        {'id': 42, 'at': '2027-03-07T09:00', 'title': 'Bike Insurance 2026 expires in 7 days', 'body': '14 Mar 2027'});
    expect((r.id, r.at, r.title), (42, DateTime(2027, 3, 7, 9), 'Bike Insurance 2026 expires in 7 days'));
  });

  testWidgets('opening the app schedules the vault\'s reminders on the phone', (tester) async {
    final scheduler = FakeScheduler();
    await tester.pumpWidget(VaultApp(store: await paired(), connect: (_) => FakeVault(), reminders: scheduler,
        storageDir: Directory.systemTemp.createTempSync('r')));
    await tester.pumpAndSettle();
    expect(scheduler.scheduled.single.title, 'Bike Insurance 2026 expires in 7 days');
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(find.textContaining('1 scheduled'), findsOneWidget);
  });

  testWidgets('with the Mac off, the reminders already scheduled are kept', (tester) async {
    final scheduler = FakeScheduler()..scheduled = [ScheduledReminder(id: 9, at: DateTime(2030), title: 'kept', body: '')];
    final vault = FakeVault()..failWith = VaultException("Can't reach the vault.", offline: true);
    await tester.pumpWidget(VaultApp(store: await paired(), connect: (_) => vault, reminders: scheduler,
        storageDir: Directory.systemTemp.createTempSync('r')));
    await tester.pump(const Duration(seconds: 1)); // spinners keep turning while the Mac is unreachable
    await tester.pump(const Duration(seconds: 5));
    expect(scheduler.scheduled.single.title, 'kept');
  });

  testWidgets('unpairing cancels the phone\'s reminders', (tester) async {
    tester.view.physicalSize = const Size(1080, 2600);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.reset);
    final scheduler = FakeScheduler();
    await tester.pumpWidget(VaultApp(store: await paired(), connect: (_) => FakeVault(), reminders: scheduler,
        storageDir: Directory.systemTemp.createTempSync('r')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Unpair this phone'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Unpair'));
    await tester.pumpAndSettle();
    expect(scheduler.cleared, isTrue);
  });
}
