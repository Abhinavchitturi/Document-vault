import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../app.dart';
import '../models.dart';

/// Add documents from the phone: photograph a bill or warranty card, or pick a PDF.
/// Photos are read on the Mac (OCR, Phase 4): straightened, cleaned up, checked for blur.
class ScanScreen extends StatefulWidget {
  const ScanScreen({super.key, this.onUploaded, this.picker});

  final VoidCallback? onUploaded;
  final ImagePicker? picker;

  @override
  State<ScanScreen> createState() => _ScanScreenState();
}

class _ScanScreenState extends State<ScanScreen> {
  String _category = 'auto';
  bool _busy = false;
  List<UploadResult> _results = [];

  Future<void> _upload(List<({String path, String name})> files) async {
    if (files.isEmpty) return;
    setState(() => _busy = true);
    try {
      final results = await VaultScope.of(context).api.upload(files, _category);
      setState(() => _results = results);
      if (results.any((r) => r.status == 'added')) widget.onUploaded?.call();
    } catch (e) {
      if (mounted) VaultScope.handle(context, e);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
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
              Text('Lay it flat, in good light, and fill the frame.',
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
        for (final r in _results) ...[const SizedBox(height: 12), _ResultCard(r)],
      ]),
    );
  }
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
