// Scans taken while the Mac is off wait here, then go to the vault when it's reachable again.
//
// Each waiting upload is a folder in the app's private storage (not the photo gallery):
//     <app documents>/upload_queue/<id>/meta.json   file name, type, when it was taken
//     <app documents>/upload_queue/<id>/<file>      the photo or file itself
// It is deleted from the phone as soon as the vault has it.

import 'dart:convert';
import 'dart:io';

import 'models.dart';
import 'vault_client.dart';

class QueuedUpload {
  QueuedUpload({required this.id, required this.name, required this.category, required this.queuedAt, required this.path});

  final String id;
  final String name;
  final String category;
  final DateTime queuedAt;
  final String path;
}

class UploadQueue {
  UploadQueue(Directory root) : dir = Directory('${root.path}/upload_queue');

  final Directory dir;
  bool _sending = false;

  Future<List<QueuedUpload>> list() async {
    if (!await dir.exists()) return [];
    final items = <QueuedUpload>[];
    await for (final entry in dir.list()) {
      if (entry is! Directory) continue;
      try {
        final meta = jsonDecode(await File('${entry.path}/meta.json').readAsString()) as Map<String, dynamic>;
        items.add(QueuedUpload(
          id: entry.uri.pathSegments.where((s) => s.isNotEmpty).last,
          name: meta['name'] as String,
          category: meta['category'] as String,
          queuedAt: DateTime.parse(meta['queued_at'] as String),
          path: '${entry.path}/${meta['file']}',
        ));
      } catch (_) {
        // half-written entry (the app was closed mid-save): ignore it
      }
    }
    items.sort((a, b) => a.queuedAt.compareTo(b.queuedAt));
    return items;
  }

  /// Keep copies of these files until the vault is reachable.
  Future<void> add(List<({String path, String name})> files, String category) async {
    for (final f in files) {
      final id = '${DateTime.now().microsecondsSinceEpoch}';
      final folder = await Directory('${dir.path}/$id').create(recursive: true);
      final fileName = f.name.replaceAll(RegExp(r'[/\\]'), '_');
      await File(f.path).copy('${folder.path}/$fileName');
      // meta.json last: an entry without it is incomplete and skipped
      await File('${folder.path}/meta.json').writeAsString(jsonEncode(
          {'name': f.name, 'file': fileName, 'category': category, 'queued_at': DateTime.now().toIso8601String()}));
    }
  }

  Future<void> remove(String id) async {
    final folder = Directory('${dir.path}/$id');
    if (await folder.exists()) await folder.delete(recursive: true);
  }

  /// Send everything waiting, oldest first. Stops at the first sign the vault is still
  /// unreachable. Returns the vault's answers for what was sent.
  Future<List<UploadResult>> send(VaultApi api) async {
    if (_sending) return [];
    _sending = true;
    final results = <UploadResult>[];
    try {
      for (final item in await list()) {
        try {
          final r = await api.upload([(path: item.path, name: item.name)], item.category);
          results.addAll(r);
          await remove(item.id); // the vault has it (or refused it for good, e.g. unreadable): don't retry forever
        } on VaultException catch (e) {
          if (e.offline || e.unpaired) break; // still off: keep everything for next time
          results.add(UploadResult(filename: item.name, status: 'error', error: e.message));
          await remove(item.id);
        }
      }
    } finally {
      _sending = false;
    }
    return results;
  }
}
