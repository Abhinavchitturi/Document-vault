import 'dart:io';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path_provider/path_provider.dart';

import '../app.dart';
import '../models.dart';

/// The page an answer came from, with the passage (light) and the answer's facts (strong)
/// highlighted, the same way as the viewer on the Mac.
class SourceScreen extends StatefulWidget {
  const SourceScreen({super.key, required this.source, this.answer = ''});

  final Source source;
  final String answer;

  @override
  State<SourceScreen> createState() => _SourceScreenState();
}

const _passageColor = Color(0x55FFDD57);
const _factColor = Color(0x88FFAA00);

class _SourceScreenState extends State<SourceScreen> {
  late int _page = widget.source.page;
  Highlights? _h;
  Uint8List? _image;
  String? _error;

  bool get _onSourcePage => _page == widget.source.page;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_h == null && _error == null) _load();
  }

  Future<void> _load() async {
    final api = VaultScope.of(context).api;
    setState(() {
      _h = null;
      _image = null;
      _error = null;
    });
    try {
      final h = await api.highlights(widget.source.docId, _page,
          chunkId: _onSourcePage ? widget.source.chunkId : null, answer: _onSourcePage ? widget.answer : null);
      Uint8List? image;
      if (h.kind == 'pdf' || h.kind == 'scan') image = await api.pageImage(widget.source.docId, _page);
      if (h.kind == 'image') image = await api.file(widget.source.docId);
      if (mounted) {
        setState(() {
          _h = h;
          _image = image;
        });
      }
    } catch (e) {
      if (!mounted) return;
      setState(() => _error = e.toString());
      VaultScope.handle(context, e);
    }
  }

  Future<void> _openOriginal() async {
    try {
      final bytes = await VaultScope.of(context).api.file(widget.source.docId);
      final dir = await getTemporaryDirectory();
      final file = File('${dir.path}/${widget.source.filename.replaceAll(RegExp(r'[/\\]'), '_')}');
      await file.writeAsBytes(bytes, flush: true); // temporary folder: the phone clears it
      await OpenFilex.open(file.path);
    } catch (e) {
      if (mounted) VaultScope.handle(context, e);
    }
  }

  void _go(int page) {
    setState(() => _page = page);
    _load();
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final h = _h;
    return Scaffold(
      appBar: AppBar(
        title: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(widget.source.filename, overflow: TextOverflow.ellipsis, style: const TextStyle(fontSize: 16)),
          Text('${categoryLabels[widget.source.category] ?? widget.source.category} · page $_page',
              style: TextStyle(fontSize: 12.5, color: scheme.onSurfaceVariant)),
        ]),
        actions: [IconButton(tooltip: 'Open original', onPressed: _openOriginal, icon: const Icon(Icons.open_in_new))],
      ),
      body: Column(children: [
        if (h != null) _note(h),
        Expanded(
          child: _error != null
              ? Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(_error!, textAlign: TextAlign.center)))
              : h == null
                  ? const Center(child: CircularProgressIndicator())
                  : h.kind == 'text'
                      ? _textPage(h)
                      : _imagePage(h),
        ),
        if (h != null && h.pages > 1)
          SafeArea(
            top: false,
            child: Row(mainAxisAlignment: MainAxisAlignment.center, children: [
              IconButton(onPressed: _page > 1 ? () => _go(_page - 1) : null, icon: const Icon(Icons.chevron_left)),
              Text('$_page / ${h.pages}'),
              IconButton(onPressed: _page < h.pages ? () => _go(_page + 1) : null, icon: const Icon(Icons.chevron_right)),
            ]),
          ),
      ]),
    );
  }

  Widget _note(Highlights h) {
    final scheme = Theme.of(context).colorScheme;
    String text;
    var warn = false;
    if (h.kind == 'image') {
      text = 'A scan or photo. The Mac needs Tesseract to read and highlight it.';
    } else if (h.kind == 'scan' && h.confidence != null && h.confidence! < lowConfidence) {
      text = 'Hard to read (${h.confidence}% OCR confidence). Check the numbers against the page.';
      warn = true;
    } else if (!_onSourcePage) {
      text = 'The answer came from page ${widget.source.page}.';
    } else if (h.factsFound.isNotEmpty) {
      text = 'Answer: ${h.factsFound.join(' · ')}';
    } else if (h.passageFound) {
      text = 'Highlighted: the passage the answer used.';
    } else {
      text = 'Pinch to zoom.';
    }
    return Container(
      width: double.infinity,
      color: warn ? scheme.errorContainer : scheme.surfaceContainer,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Text(text, style: TextStyle(color: warn ? scheme.onErrorContainer : scheme.onSurfaceVariant, fontSize: 13.5)),
    );
  }

  Widget _imagePage(Highlights h) {
    final image = _image;
    if (image == null) return const Center(child: CircularProgressIndicator());
    final hasBoxes = h.width > 0 && h.height > 0 && (h.kind == 'pdf' || h.kind == 'scan');
    return InteractiveViewer(
      maxScale: 5,
      child: Center(
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: hasBoxes
              ? AspectRatio(
                  aspectRatio: h.width / h.height,
                  child: LayoutBuilder(builder: (context, size) {
                    final sx = size.maxWidth / h.width, sy = size.maxHeight / h.height;
                    Widget box(List<double> r, Color c) => Positioned(
                          left: r[0] * sx,
                          top: r[1] * sy,
                          width: r[2] * sx,
                          height: r[3] * sy,
                          child: DecoratedBox(decoration: BoxDecoration(color: c, borderRadius: BorderRadius.circular(3))),
                        );
                    return Stack(fit: StackFit.expand, children: [
                      Image.memory(image, fit: BoxFit.fill, gaplessPlayback: true),
                      for (final r in h.passage) box(r, _passageColor),
                      for (final r in h.facts) box(r, _factColor),
                    ]);
                  }),
                )
              : Image.memory(image),
        ),
      ),
    );
  }

  Widget _textPage(Highlights h) {
    final text = h.text ?? '';
    final level = List<int>.filled(text.length, 0);
    for (final s in h.passage) {
      for (var i = s[0].toInt(); i < s[1].toInt() && i < text.length; i++) {
        level[i] = 1;
      }
    }
    for (final s in h.facts) {
      for (var i = s[0].toInt(); i < s[1].toInt() && i < text.length; i++) {
        level[i] = 2;
      }
    }
    final spans = <TextSpan>[];
    for (var i = 0; i < text.length;) {
      var j = i;
      while (j < text.length && level[j] == level[i]) {
        j++;
      }
      spans.add(TextSpan(
        text: text.substring(i, j),
        style: level[i] == 0
            ? null
            : TextStyle(backgroundColor: level[i] == 2 ? _factColor : _passageColor, fontWeight: level[i] == 2 ? FontWeight.w700 : null),
      ));
      i = j;
    }
    return SingleChildScrollView(
      padding: const EdgeInsets.all(18),
      child: SelectableText.rich(TextSpan(style: const TextStyle(fontSize: 15, height: 1.5), children: spans)),
    );
  }
}
