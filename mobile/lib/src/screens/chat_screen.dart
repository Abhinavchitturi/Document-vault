import 'dart:async';

import 'package:flutter/material.dart';

import '../app.dart';
import '../models.dart';
import '../vault_client.dart';
import '../widgets/answer_text.dart';
import 'source_screen.dart';

class _Message {
  _Message.user(this.text) : fromUser = true;
  _Message.assistant() : fromUser = false, text = '';

  final bool fromUser;
  String text;
  List<Source> sources = [];
  bool done = false;
  String? error;
}

const _examples = [
  'How much is the electricity bill for August?',
  'When does my bike insurance expire?',
  'What is the serial number of the TV?',
];

class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final _messages = <_Message>[];
  final _input = TextEditingController();
  final _scroll = ScrollController();
  StreamSubscription? _answering;

  bool get _busy => _answering != null;

  @override
  void dispose() {
    _answering?.cancel();
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _toBottom() => WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
      });

  void _ask([String? preset]) {
    final question = (preset ?? _input.text).trim();
    if (question.isEmpty || _busy) return;
    final history = [
      for (final m in _messages.where((m) => m.fromUser || (m.done && m.error == null)))
        {'role': m.fromUser ? 'user' : 'assistant', 'content': m.text},
    ];
    final answer = _Message.assistant();
    setState(() {
      _messages.addAll([_Message.user(question), answer]);
      _input.clear();
    });
    _toBottom();
    final api = VaultScope.of(context).api;
    _answering = api.chat(question, history).listen(
      (event) {
        setState(() {
          switch (event) {
            case SourcesEvent(:final sources):
              answer.sources = sources;
            case TokenEvent(:final text):
              answer.text += text;
            case DoneEvent():
              answer.done = true;
            case ErrorEvent(:final message):
              answer.error = message;
          }
        });
        _toBottom();
      },
      onError: (Object e) {
        // Plain words, never an exception's text ("ClientException: Connection closed…").
        setState(() => answer.error = e is VaultException && !e.offline ? e.message : "I'm not able to answer right now. Please try again.");
        if (mounted && e is VaultException && e.unpaired) VaultScope.handle(context, e); // the bubble says the rest
      },
      onDone: () => setState(() {
        answer.done = true;
        _answering = null;
      }),
      cancelOnError: true,
    );
  }

  void _stop() {
    _answering?.cancel();
    setState(() {
      _answering = null;
      if (_messages.isNotEmpty) _messages.last.done = true;
    });
  }

  void _open(_Message m, int n) {
    if (n < 1 || n > m.sources.length) return;
    Navigator.of(context).push(MaterialPageRoute(
      builder: (_) => SourceScreen(source: m.sources[n - 1], answer: m.done ? m.text : ''),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        title: const Text('Chat'),
        actions: [
          if (_messages.isNotEmpty)
            IconButton(
                tooltip: 'New chat',
                onPressed: _busy ? null : () => setState(_messages.clear),
                icon: const Icon(Icons.add_comment_outlined)),
        ],
      ),
      body: Column(children: [
        Expanded(
          child: _messages.isEmpty
              ? ListView(padding: const EdgeInsets.all(24), children: [
                  const SizedBox(height: 40),
                  Icon(Icons.forum_outlined, size: 48, color: scheme.primary),
                  const SizedBox(height: 14),
                  Text('Ask about your documents', textAlign: TextAlign.center, style: Theme.of(context).textTheme.titleLarge),
                  const SizedBox(height: 6),
                  Text('Answers come from your own papers, with the page they came from.',
                      textAlign: TextAlign.center, style: TextStyle(color: scheme.onSurfaceVariant)),
                  const SizedBox(height: 24),
                  for (final q in _examples)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 8),
                      child: OutlinedButton(
                        onPressed: () => _ask(q),
                        style: OutlinedButton.styleFrom(padding: const EdgeInsets.all(14), alignment: Alignment.centerLeft),
                        child: Text(q),
                      ),
                    ),
                ])
              : ListView.builder(
                  controller: _scroll,
                  padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
                  itemCount: _messages.length,
                  itemBuilder: (context, i) => _bubble(_messages[i]),
                ),
        ),
        SafeArea(
          top: false,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 6, 12, 10),
            child: Row(children: [
              Expanded(
                child: TextField(
                  controller: _input,
                  minLines: 1,
                  maxLines: 4,
                  textInputAction: TextInputAction.send,
                  onSubmitted: (_) => _ask(),
                  decoration: const InputDecoration(hintText: 'Ask about your documents…'),
                ),
              ),
              const SizedBox(width: 8),
              IconButton.filled(
                tooltip: _busy ? 'Stop' : 'Send',
                onPressed: _busy ? _stop : _ask,
                icon: Icon(_busy ? Icons.stop_rounded : Icons.arrow_upward_rounded),
              ),
            ]),
          ),
        ),
      ]),
    );
  }

  Widget _bubble(_Message m) {
    final scheme = Theme.of(context).colorScheme;
    if (m.fromUser) {
      return Align(
        alignment: Alignment.centerRight,
        child: Container(
          margin: const EdgeInsets.only(bottom: 10, left: 48),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          decoration: BoxDecoration(color: scheme.primary, borderRadius: BorderRadius.circular(18)),
          child: Text(m.text, style: TextStyle(color: scheme.onPrimary, fontSize: 16)),
        ),
      );
    }
    return Container(
      margin: const EdgeInsets.only(bottom: 14, right: 24),
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(color: scheme.surfaceContainerLowest, borderRadius: BorderRadius.circular(18)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        if (m.error != null)
          Text(m.error!, style: TextStyle(color: scheme.error))
        else if (m.text.isEmpty && !m.done)
          const SizedBox(width: 22, height: 22, child: CircularProgressIndicator(strokeWidth: 2))
        else
          AnswerText(m.text, onCite: (n) => _open(m, n)),
        if (m.sources.isNotEmpty && m.done && m.error == null) ...[
          const SizedBox(height: 10),
          Wrap(spacing: 6, runSpacing: 6, children: [
            for (final s in m.sources)
              ActionChip(
                avatar: CircleAvatar(child: Text('${s.rank}', style: const TextStyle(fontSize: 11))),
                label: Text('${s.filename} · p.${s.page}', overflow: TextOverflow.ellipsis),
                onPressed: () => _open(m, s.rank),
              ),
          ]),
        ],
      ]),
    );
  }
}
