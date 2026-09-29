import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';

/// An answer from the vault: **bold** facts, and [1] citations as small tappable numbers.
class AnswerText extends StatelessWidget {
  const AnswerText(this.text, {super.key, this.onCite, this.style});

  final String text;
  final void Function(int n)? onCite;
  final TextStyle? style;

  static final _token = RegExp(r'\*\*(.+?)\*\*|\[(\d+)\]');

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final base = style ?? DefaultTextStyle.of(context).style.copyWith(fontSize: 16, height: 1.45);
    final spans = <InlineSpan>[];
    var at = 0;
    for (final m in _token.allMatches(text)) {
      if (m.start > at) spans.add(TextSpan(text: text.substring(at, m.start)));
      if (m.group(1) != null) {
        spans.add(TextSpan(text: m.group(1), style: const TextStyle(fontWeight: FontWeight.w700)));
      } else {
        final n = int.parse(m.group(2)!);
        spans.add(TextSpan(
          text: ' $n ',
          style: TextStyle(
              fontSize: (base.fontSize ?? 16) * 0.72,
              fontWeight: FontWeight.w700,
              color: scheme.primary,
              backgroundColor: scheme.primaryContainer),
          recognizer: onCite == null ? null : (TapGestureRecognizer()..onTap = () => onCite!(n)),
        ));
      }
      at = m.end;
    }
    if (at < text.length) spans.add(TextSpan(text: text.substring(at)));
    return Text.rich(TextSpan(style: base, children: spans));
  }
}
