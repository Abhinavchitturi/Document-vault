import 'dart:io';

import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../pairing.dart';
import '../vault_client.dart';

/// First run: scan the QR code from the Mac (Settings -> Phones -> Pair a phone).
class PairScreen extends StatefulWidget {
  const PairScreen({super.key, required this.onPaired, this.pair = VaultClient.pair});

  final Future<void> Function(Pairing) onPaired;
  final Future<Pairing> Function(PairingCode code, String deviceName) pair;

  @override
  State<PairScreen> createState() => _PairScreenState();
}

String defaultDeviceName() => Platform.isIOS ? 'iPhone' : Platform.isAndroid ? 'Android phone' : 'Phone';

class _PairScreenState extends State<PairScreen> {
  bool _scanning = false;
  bool _busy = false;
  String? _error;

  Future<void> _use(String raw) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _scanning = false;
      _error = null;
    });
    try {
      final code = PairingCode.parse(raw);
      final pairing = await widget.pair(code, defaultDeviceName());
      await widget.onPaired(pairing);
    } on FormatException catch (e) {
      setState(() => _error = e.message);
    } on VaultException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _typeCode() async {
    final controller = TextEditingController();
    final raw = await showDialog<String>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Paste the pairing code'),
        content: TextField(
          controller: controller,
          maxLines: 4,
          decoration: const InputDecoration(hintText: '{"v":1,"url":"https://…"}'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, controller.text), child: const Text('Pair')),
        ],
      ),
    );
    if (raw != null && raw.trim().isNotEmpty) await _use(raw);
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    if (_scanning) {
      return Scaffold(
        appBar: AppBar(title: const Text('Scan the pairing code')),
        body: Stack(children: [
          MobileScanner(
            onDetect: (capture) {
              final value = capture.barcodes.map((b) => b.rawValue).whereType<String>().firstOrNull;
              if (value != null) _use(value);
            },
          ),
          Center(
            child: Container(
              width: 250,
              height: 250,
              decoration: BoxDecoration(border: Border.all(color: Colors.white, width: 3), borderRadius: BorderRadius.circular(24)),
            ),
          ),
          const Positioned(
            left: 24,
            right: 24,
            bottom: 40,
            child: Text('On the Mac: Settings → Phones → Pair a phone',
                textAlign: TextAlign.center, style: TextStyle(color: Colors.white, fontSize: 16, shadows: [Shadow(blurRadius: 6)])),
          ),
        ]),
      );
    }
    return Scaffold(
      body: SafeArea(
        child: ListView(padding: const EdgeInsets.fromLTRB(28, 56, 28, 28), children: [
          Center(
            child: Container(
              width: 72,
              height: 72,
              decoration: BoxDecoration(color: scheme.primary, borderRadius: BorderRadius.circular(22)),
              child: Icon(Icons.description_outlined, color: scheme.onPrimary, size: 36),
            ),
          ),
          const SizedBox(height: 22),
          Text('Document Vault', textAlign: TextAlign.center, style: Theme.of(context).textTheme.headlineMedium),
          const SizedBox(height: 10),
          Text("Your family's papers, answered privately by the vault on your Mac.",
              textAlign: TextAlign.center, style: TextStyle(color: scheme.onSurfaceVariant, fontSize: 16)),
          const SizedBox(height: 36),
          _Step(n: 1, text: 'On the Mac, open the vault and go to Settings → Phones.'),
          _Step(n: 2, text: 'Tap "Pair a phone". A QR code appears.'),
          _Step(n: 3, text: 'Scan it here. The phone must be on the same Wi-Fi.'),
          const SizedBox(height: 28),
          FilledButton.icon(
            onPressed: _busy ? null : () => setState(() => _scanning = true),
            icon: _busy
                ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.qr_code_scanner),
            label: Text(_busy ? 'Pairing…' : 'Scan pairing code'),
            style: FilledButton.styleFrom(minimumSize: const Size.fromHeight(54)),
          ),
          const SizedBox(height: 8),
          TextButton(onPressed: _busy ? null : _typeCode, child: const Text('Paste the code instead')),
          if (_error != null) ...[
            const SizedBox(height: 12),
            Card(
              color: scheme.errorContainer,
              child: Padding(
                padding: const EdgeInsets.all(14),
                child: Text(_error!, style: TextStyle(color: scheme.onErrorContainer)),
              ),
            ),
          ],
          const SizedBox(height: 24),
          Row(children: [
            Icon(Icons.lock_outline, size: 14, color: scheme.outline),
            const SizedBox(width: 6),
            Expanded(
              child: Text('The code locks this app to your vault\'s own certificate. Documents stay on the Mac.',
                  style: TextStyle(color: scheme.outline, fontSize: 12.5)),
            ),
          ]),
        ]),
      ),
    );
  }
}

class _Step extends StatelessWidget {
  const _Step({required this.n, required this.text});

  final int n;
  final String text;

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 7),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        CircleAvatar(radius: 13, backgroundColor: scheme.primaryContainer,
            child: Text('$n', style: TextStyle(fontSize: 13, color: scheme.onPrimaryContainer))),
        const SizedBox(width: 12),
        Expanded(child: Padding(padding: const EdgeInsets.only(top: 3), child: Text(text, style: const TextStyle(fontSize: 15)))),
      ]),
    );
  }
}
