import 'dart:typed_data';

import 'package:document_vault/src/models.dart';
import 'package:document_vault/src/pairing.dart';
import 'package:document_vault/src/phone_reminders.dart';
import 'package:document_vault/src/vault_client.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

/// An in-memory vault for widget tests.
class FakeVault implements VaultApi {
  FakeVault({this.answer = 'Your electricity bill for August is **Rs. 2,486** [1].'});

  final String answer;
  final asked = <String>[];
  final uploads = <List<({String path, String name})>>[];
  bool unpairedCalled = false;
  Object? failWith;
  bool macOff = false; // uploads fail as if the Mac were switched off

  final docs = [
    VaultDocument(id: 'd1', filename: 'Electricity Bill 2026 08.pdf', category: 'bill', pages: 1, ocrPages: 0,
        ocrConfidence: null, sizeBytes: 20000, addedAt: DateTime(2026, 9, 1)),
    VaultDocument(id: 'd2', filename: 'Blurry Receipt.jpg', category: 'warranty', pages: 1, ocrPages: 1,
        ocrConfidence: 31, sizeBytes: 7000, addedAt: DateTime(2026, 9, 2)),
  ];

  @override
  Future<Info> info() async {
    if (failWith != null) throw failWith!;
    return Info(owner: 'abhinav', isAdmin: true, numDocuments: docs.length, assistantReady: true,
        assistantMessage: 'Answers ready (llama3.1:8b)', ocrAvailable: true);
  }

  @override
  Future<List<VaultDocument>> documents() async {
    if (failWith != null) throw failWith!;
    return docs;
  }

  @override
  Future<void> deleteDocument(String id) async => docs.removeWhere((d) => d.id == id);

  @override
  Stream<ChatEvent> chat(String question, List<Map<String, String>> history) async* {
    asked.add(question);
    yield SourcesEvent([
      Source(rank: 1, chunkId: 'd1-p1-0', docId: 'd1', filename: 'Electricity Bill 2026 08.pdf', category: 'bill',
          page: 1, text: 'Total amount due Rs. 2,486'),
    ]);
    for (var i = 0; i < answer.length; i += 6) {
      yield TokenEvent(answer.substring(i, i + 6 > answer.length ? answer.length : i + 6));
    }
    yield DoneEvent(found: true);
  }

  @override
  Future<Highlights> highlights(String docId, int page, {String? chunkId, String? answer}) async => Highlights(
      kind: 'text', page: page, pages: 1, width: 0, height: 0, passage: [[0, 10]], facts: [[20, 29]],
      factsFound: ['Rs. 2,486'], passageFound: true, scanned: false,
      text: 'Electricity bill. Due Rs. 2,486 on 18 September 2026.', confidence: null);

  @override
  Future<Uint8List> pageImage(String docId, int page) async => Uint8List(0);

  @override
  Future<Uint8List> file(String docId) async => Uint8List(0);

  @override
  Future<List<UploadResult>> upload(List<({String path, String name})> files, String category, {String? password}) async {
    if (macOff) throw VaultException("Can't reach the vault.", offline: true);
    uploads.add(files);
    return [UploadResult(filename: files.first.name, status: 'added', document: docs[1])];
  }

  @override
  Future<void> unpair(int deviceId) async => unpairedCalled = true;

  List<ScheduledReminder> schedule = [
    ScheduledReminder(id: 1, at: DateTime(2030, 3, 7, 9), title: 'Bike Insurance 2026 expires in 7 days', body: '14 Mar 2030'),
  ];

  @override
  Future<List<ScheduledReminder>> reminderSchedule() async {
    if (failWith != null) throw failWith!;
    return schedule;
  }
}

/// Keychain stand-in.
class MemoryStorage implements FlutterSecureStorage {
  final values = <String, String>{};

  @override
  Future<String?> read({required String key, AppleOptions? iOptions, AndroidOptions? aOptions, LinuxOptions? lOptions,
          WebOptions? webOptions, AppleOptions? mOptions, WindowsOptions? wOptions}) async =>
      values[key];

  @override
  Future<void> write({required String key, required String? value, AppleOptions? iOptions, AndroidOptions? aOptions,
      LinuxOptions? lOptions, WebOptions? webOptions, AppleOptions? mOptions, WindowsOptions? wOptions}) async {
    if (value == null) {
      values.remove(key);
    } else {
      values[key] = value;
    }
  }

  @override
  Future<void> delete({required String key, AppleOptions? iOptions, AndroidOptions? aOptions, LinuxOptions? lOptions,
          WebOptions? webOptions, AppleOptions? mOptions, WindowsOptions? wOptions}) async =>
      values.remove(key);

  @override
  dynamic noSuchMethod(Invocation invocation) => super.noSuchMethod(invocation);
}

final testPairing = Pairing(
    urls: ['https://192.168.1.20:8443'], fingerprint: 'ab' * 32, token: 'secret-token', deviceId: 7, userName: 'abhinav');

/// Records what would be scheduled on the phone.
class FakeScheduler implements ReminderScheduler {
  List<ScheduledReminder> scheduled = [];
  bool cleared = false;

  @override
  Future<int> replaceAll(List<ScheduledReminder> reminders) async {
    scheduled = reminders;
    return reminders.length;
  }

  @override
  Future<void> clear() async {
    cleared = true;
    scheduled = [];
  }
}
