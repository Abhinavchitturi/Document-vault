import 'package:flutter/material.dart';

import '../app.dart';
import '../models.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  Info? _info;
  bool? _lock;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    if (_lock == null) _load();
  }

  Future<void> _load() async {
    final scope = VaultScope.of(context);
    final lock = await scope.store.lockEnabled();
    if (mounted) setState(() => _lock = lock);
    try {
      final info = await scope.api.info();
      if (mounted) setState(() => _info = info);
    } catch (e) {
      if (mounted) VaultScope.handle(context, e);
    }
  }

  Future<void> _unpair() async {
    final scope = VaultScope.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('Unpair this phone?'),
        content: const Text('The phone forgets the vault, and scans still waiting to be sent are deleted. '
            'To use it again, scan a new pairing code on the Mac.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(context, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(context, true), child: const Text('Unpair')),
        ],
      ),
    );
    if (ok != true) return;
    try {
      await scope.api.unpair(scope.pairing.deviceId);
    } catch (_) {
      // Mac unreachable: forget it here anyway; it can be removed in Settings -> Phones on the Mac.
    }
    await scope.onUnpaired();
  }

  @override
  Widget build(BuildContext context) {
    final scope = VaultScope.of(context);
    final scheme = Theme.of(context).colorScheme;
    final info = _info;
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(padding: const EdgeInsets.all(16), children: [
        Card(
          child: Column(children: [
            ListTile(
              leading: CircleAvatar(child: Text(scope.pairing.userName.characters.first.toUpperCase())),
              title: Text(scope.pairing.userName),
              subtitle: Text(info == null ? 'Connecting…' : '${info.numDocuments} documents · ${info.isAdmin ? 'Admin' : 'Family member'}'),
            ),
            ListTile(
              leading: Icon(info?.assistantReady == true ? Icons.check_circle_outline : Icons.cloud_off_outlined),
              title: Text(info == null ? 'Assistant' : (info.assistantReady ? 'Assistant ready' : 'Assistant offline')),
              subtitle: info == null || info.assistantMessage.isEmpty ? null : Text(info.assistantMessage),
            ),
          ]),
        ),
        const SizedBox(height: 14),
        Card(
          child: SwitchListTile(
            secondary: const Icon(Icons.fingerprint),
            title: const Text('Lock with Face ID / fingerprint'),
            subtitle: const Text('When the app opens, and after 2 minutes away.'),
            value: _lock ?? true,
            onChanged: _lock == null
                ? null
                : (on) async {
                    await scope.store.setLockEnabled(on);
                    setState(() => _lock = on);
                  },
          ),
        ),
        const SizedBox(height: 14),
        Card(
          child: Column(children: [
            ListTile(
              leading: const Icon(Icons.computer_outlined),
              title: const Text('Vault addresses'),
              subtitle: Text([
                for (final u in scope.pairing.urls)
                  '${Uri.parse(u).host.startsWith('100.') || u.contains('.ts.net') ? 'Anywhere (Tailscale)' : 'Home Wi-Fi'}: $u',
              ].join('\n')),
            ),
            ListTile(
              leading: const Icon(Icons.verified_user_outlined),
              title: const Text('Certificate'),
              subtitle: Text('Pinned: ${scope.pairing.fingerprint.substring(0, 16)}…'),
            ),
            ListTile(
              leading: Icon(Icons.link_off, color: scheme.error),
              title: Text('Unpair this phone', style: TextStyle(color: scheme.error)),
              onTap: _unpair,
            ),
          ]),
        ),
        const SizedBox(height: 18),
        Row(mainAxisAlignment: MainAxisAlignment.center, children: [
          Icon(Icons.lock_outline, size: 13, color: scheme.outline),
          const SizedBox(width: 6),
          Text('Documents and answers stay on the Mac.',
              style: TextStyle(color: scheme.outline, fontSize: 12.5)),
        ]),
      ]),
    );
  }
}
