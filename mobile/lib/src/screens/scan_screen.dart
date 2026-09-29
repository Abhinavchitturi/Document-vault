import 'dart:async';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../app.dart';
import '../models.dart';
import '../upload_queue.dart';
import '../vault_client.dart';

/// Add documents from the phone: photograph a bill or warranty card, or pick a PDF.
/// Photos are read on the Mac (OCR, Phase 4): straightened, cleaned up, checked for blur.
class ScanScreen extends StatefulWidget {
  const ScanScreen({super.key, this.onUploaded, this.picker});

  final VoidCallback? onUploaded;
  final ImagePicker? picker;

  @override
  State<ScanScreen> createState() => ScanScreenState();
}

class ScanScreenState extends State<ScanScreen> with WidgetsBindingObserver {
  String _category = 'auto';
  bool _busy = false;
  List<UploadResult> _results = [];
  List<QueuedUpload> _waiting = [];
  String? _queuedNote;
  Timer? _retry;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    // While the app is open, keep trying to send waiting scans (the Mac may have just woken up).
    _retry = Timer.periodic(const Duration(seconds: 45), (_) => _sendWaiting());
    WidgetsBinding.instance.addPostFrameCallback((_) => _sendWaiting());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _retry?.cancel();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _sendWaiting();
  }

  Future<void> _refreshWaiting() async {
    final waiting = await VaultScope.of(context).queue.list();
    if (mounted) setState(() => _waiting = waiting);
  }

  Future<void> _sendWaiting({bool manual = false}) async {
    if (!mounted) return;
    final scope = VaultScope.of(context);
    await _refreshWaiting();
    if (_waiting.isEmpty || !mounted) return;
    final sent = await scope.queue.send(scope.api);
    await _refreshWaiting();
    if (!mounted) return;
    if (sent.isNotEmpty) {
      setState(() {
        _results = sent;
        _queuedNote = null;
      });
      if (sent.any((r) => r.status == 'added')) widget.onUploaded?.call();
    } else if (manual) {
      ScaffoldMessenger.of(context).showSnackBar(const SnackBar(content: Text('The vault is still not reachable.')));
    }
  }

  /// Files shared from another app ("Send to → Document Vault"): uploaded like a picked file,
  /// with the type detected automatically.
  Future<void> uploadShared(List<({String path, String name})> files) async {
    if (_busy) return;
    setState(() => _category = 'auto');
    await _upload(files);
  }

  Future<void> _upload(List<({String path, String name})> files) async {
    if (files.isEmpty) return;
    final scope = VaultScope.of(context);
    setState(() {
      _busy = true;
      _queuedNote = null;
    });
    try {
      var results = await scope.api.upload(files, _category);
      // A locked PDF (e-Aadhaar, e-PAN): ask for its password and try that file again.
      for (var i = 0; i < results.length; i++) {
        while (results[i].code != null && mounted) {
          final password = await _askPassword(results[i]);
          if (password == null) break;
          final file = files.firstWhere((f) => f.name == results[i].filename, orElse: () => files[i]);
          results = [...results]..[i] = (await scope.api.upload([file], _category, password: password)).single;
        }
      }
      setState(() => _results = results);
      if (results.any((r) => r.status == 'added')) widget.onUploaded?.call();
    } on VaultException catch (e) {
      if (e.offline) {
        // The Mac is off or unreachable: keep the scan on the phone and send it later.
        await scope.queue.add(files, _category);
        await _refreshWaiting();
        setState(() {
          _results = [];
          _queuedNote = files.length == 1
              ? "Saved on the phone. It'll be sent to the vault when your Mac is on."
              : "${files.length} files saved on the phone. They'll be sent when your Mac is on.";
        });
      } else if (mounted) {
        VaultScope.handle(context, e);
      }
    } catch (e) {
      if (mounted) VaultScope.handle(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<String?> _askPassword(UploadResult r) {
    final controller = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(r.code == 'wrong_password' ? 'Wrong password, try again' : 'This PDF is locked'),
        content: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(r.filename, style: const TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          const Text('e-Aadhaar: first 4 letters of your name in capitals + year of birth.\n'
              'e-PAN: date of birth as DDMMYYYY.'),
          const SizedBox(height: 12),
          TextField(controller: controller, obscureText: true, autofocus: true,
              decoration: const InputDecoration(hintText: 'PDF password')),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Skip')),
          FilledButton(onPressed: () => Navigator.pop(context, controller.text), child: const Text('Unlock')),
        ],
      ),
    );
  }

  Future<void> _photo(ImageSource source) async {
    final picker = widget.picker ?? ImagePicker();
    // Big enough for small print, small enough to send quickly over Wi-Fi.
    final photo = await picker.pickImage(source: source, maxWidth: 3000, imageQuality: 88);
    if (photo == null) return;
    final stamp = DateTime.now().toIso8601String().substring(0, 16).replaceAll(':', '.');
    await _upload([(path: photo.path, name: 'Photo $stamp.jpg')]);
  }

  Future<void> _files() async {
    final picked = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['pdf', 'docx', 'txt', 'md', 'png', 'jpg', 'jpeg', 'webp', 'tif', 'tiff'],
    );
    await _upload([for (final f in picked) if (f.path != null) (path: f.path!, name: f.name)]);
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Add documents')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(18),
            child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
              Icon(Icons.document_scanner_outlined, size: 44, color: scheme.primary),
              const SizedBox(height: 10),
              Text('Photograph a bill, policy or warranty card', textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.titleMedium),
              const SizedBox(height: 4),
              Text('Lay it flat, in good light, and fill the frame. From WhatsApp or Files: Share → Document Vault.',
                  textAlign: TextAlign.center, style: TextStyle(color: scheme.onSurfaceVariant)),
              const SizedBox(height: 16),
              FilledButton.icon(
                onPressed: _busy ? null : () => _photo(ImageSource.camera),
                icon: const Icon(Icons.photo_camera_outlined),
                label: const Text('Take a photo'),
                style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(52)),
              ),
              const SizedBox(height: 8),
              Row(children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _busy ? null : () => _photo(ImageSource.gallery),
                    icon: const Icon(Icons.photo_library_outlined),
                    label: const Text('From photos'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _busy ? null : _files,
                    icon: const Icon(Icons.attach_file),
                    label: const Text('PDF or file'),
                  ),
                ),
              ]),
              const SizedBox(height: 14),
              DropdownButtonFormField<String>(
                initialValue: _category,
                decoration: const InputDecoration(labelText: 'Type'),
                items: [
                  const DropdownMenuItem(value: 'auto', child: Text('Detect automatically')),
                  for (final c in categories) DropdownMenuItem(value: c, child: Text(categoryLabels[c]!)),
                ],
                onChanged: _busy ? null : (v) => setState(() => _category = v ?? 'auto'),
              ),
            ]),
          ),
        ),
        if (_busy) ...[
          const SizedBox(height: 20),
          const Center(child: CircularProgressIndicator()),
          const SizedBox(height: 10),
          Text('Sending to the vault and reading it…', textAlign: TextAlign.center, style: TextStyle(color: scheme.onSurfaceVariant)),
        ],
        if (_queuedNote != null) ...[
          const SizedBox(height: 12),
          Card(
            color: scheme.secondaryContainer,
            child: ListTile(
              leading: const Icon(Icons.schedule_send_outlined),
              title: Text(_queuedNote!),
            ),
          ),
        ],
        for (final r in _results) ...[const SizedBox(height: 12), _ResultCard(r)],
        if (_waiting.isNotEmpty) ...[
          const SizedBox(height: 18),
          Row(children: [
            Expanded(child: Text('Waiting to send (${_waiting.length})', style: Theme.of(context).textTheme.titleSmall)),
            TextButton.icon(
              onPressed: _busy ? null : () => _sendWaiting(manual: true),
              icon: const Icon(Icons.send_outlined, size: 18),
              label: const Text('Send now'),
            ),
          ]),
          Card(
            child: Column(children: [
              for (final w in _waiting)
                ListTile(
                  leading: const Icon(Icons.schedule),
                  title: Text(w.name, maxLines: 1, overflow: TextOverflow.ellipsis),
                  subtitle: Text('Taken ${_ago(w.queuedAt)} · sent automatically when the Mac is on'),
                  trailing: IconButton(
                    tooltip: 'Delete from phone',
                    icon: const Icon(Icons.close),
                    onPressed: () async {
                      await VaultScope.of(context).queue.remove(w.id);
                      await _refreshWaiting();
                    },
                  ),
                ),
            ]),
          ),
        ],
      ]),
    );
  }
}

String _ago(DateTime t) {
  final d = DateTime.now().difference(t);
  if (d.inMinutes < 1) return 'just now';
  if (d.inHours < 1) return '${d.inMinutes} min ago';
  if (d.inDays < 1) return '${d.inHours} h ago';
  return '${d.inDays} days ago';
}

class _ResultCard extends StatelessWidget {
  const _ResultCard(this.r);

  final UploadResult r;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final d = r.document;
    final (icon, color, text) = switch (r.status) {
      'added' when d != null && d.hardToRead => (
          Icons.warning_amber_rounded,
          scheme.error,
          'Added as ${categoryLabels[d.category]}, but it was hard to read (${d.ocrConfidence}% OCR confidence). '
              'Answers from it may be wrong: a sharper, well-lit photo will help.'
        ),
      'added' when d != null && d.needsReview => (
          Icons.help_outline,
          scheme.tertiary,
          'Added as ${categoryLabels[d.category]}, but we weren\'t sure about the type. Check it in Documents on the Mac.'
        ),
      'added' when d != null => (
          Icons.check_circle_outline,
          Colors.green.shade700,
          'Added as ${categoryLabels[d.category]}, ${d.pages} ${d.pages == 1 ? 'page' : 'pages'}'
              '${d.ocrPages > 0 ? ' (read with OCR)' : ''}.'
        ),
      'duplicate' => (Icons.info_outline, scheme.primary, 'Already in the vault.'),
      _ => (Icons.error_outline, scheme.error, r.error ?? 'Could not add this file.'),
    };
    return Card(
      child: ListTile(
        leading: Icon(icon, color: color),
        title: Text(r.filename, maxLines: 1, overflow: TextOverflow.ellipsis),
        subtitle: Text(text),
      ),
    );
  }
}
