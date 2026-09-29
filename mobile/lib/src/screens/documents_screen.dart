import 'package:flutter/material.dart';

import '../app.dart';
import '../models.dart';
import 'source_screen.dart';

class DocumentsScreen extends StatefulWidget {
  const DocumentsScreen({super.key});

  @override
  State<DocumentsScreen> createState() => DocumentsScreenState();
}

class DocumentsScreenState extends State<DocumentsScreen> {
  List<VaultDocument>? _docs;
  String _filter = '';
  String _query = '';

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_docs == null) reload();
  }

  Future<void> reload() async {
    try {
      final docs = await VaultScope.of(context).api.documents();
      if (mounted) setState(() => _docs = docs);
    } catch (e) {
      if (mounted) VaultScope.handle(context, e);
    }
  }

  Future<void> _delete(VaultDocument d) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Remove from the vault?'),
        content: Text('${d.filename} and its search data will be deleted from the Mac.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Remove')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await VaultScope.of(context).api.deleteDocument(d.id);
      await reload();
    } catch (e) {
      if (mounted) VaultScope.handle(context, e);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final docs = _docs;
    final shown = (docs ?? [])
        .where((d) => _filter.isEmpty || d.category == _filter)
        .where((d) => _query.isEmpty || d.filename.toLowerCase().contains(_query.toLowerCase()))
        .toList();
    return Scaffold(
      appBar: AppBar(title: const Text('Documents')),
      body: RefreshIndicator(
        onRefresh: reload,
        child: ListView(padding: const EdgeInsets.fromLTRB(14, 4, 14, 24), children: [
          TextField(
            decoration: const InputDecoration(prefixIcon: Icon(Icons.search), hintText: 'Find by file name…'),
            onChanged: (v) => setState(() => _query = v),
          ),
          const SizedBox(height: 10),
          SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(children: [
              for (final c in ['', ...categories])
                Padding(
                  padding: const EdgeInsets.only(right: 6),
                  child: ChoiceChip(
                    label: Text(c.isEmpty ? 'All' : categoryLabels[c]!),
                    selected: _filter == c,
                    onSelected: (_) => setState(() => _filter = c),
                  ),
                ),
            ]),
          ),
          const SizedBox(height: 10),
          if (docs == null)
            const Padding(padding: EdgeInsets.all(40), child: Center(child: CircularProgressIndicator()))
          else if (shown.isEmpty)
            Padding(
              padding: const EdgeInsets.all(40),
              child: Text(docs.isEmpty ? 'No documents yet. Use Scan to add one.' : 'Nothing matches.',
                  textAlign: TextAlign.center, style: TextStyle(color: scheme.onSurfaceVariant)),
            )
          else
            Card(
              child: Column(children: [
                for (final d in shown)
                  ListTile(
                    leading: CircleAvatar(
                      backgroundColor: scheme.primaryContainer,
                      child: Icon(_icon(d.category), color: scheme.onPrimaryContainer, size: 20),
                    ),
                    title: Text(d.filename, maxLines: 2, overflow: TextOverflow.ellipsis),
                    subtitle: Text([
                      categoryLabels[d.category] ?? d.category,
                      '${d.pages} ${d.pages == 1 ? 'page' : 'pages'}',
                      if (d.ocrPages > 0) 'scanned',
                      if (d.needsReview) '⚠ check type (on the Mac)',
                      if (d.hardToRead) '⚠ hard to read',
                    ].join(' · ')),
                    onTap: () => Navigator.of(context)
                        .push(MaterialPageRoute(builder: (_) => SourceScreen(source: Source.firstPage(d)))),
                    trailing: IconButton(
                      tooltip: 'Remove',
                      icon: const Icon(Icons.delete_outline),
                      onPressed: () => _delete(d),
                    ),
                  ),
              ]),
            ),
        ]),
      ),
    );
  }

  IconData _icon(String category) => switch (category) {
        'insurance' => Icons.shield_outlined,
        'warranty' => Icons.verified_outlined,
        'bill' => Icons.receipt_long_outlined,
        'property' => Icons.home_outlined,
        _ => Icons.description_outlined,
      };
}
