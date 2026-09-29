import 'package:flutter/material.dart';
import 'package:local_auth/local_auth.dart';

/// Face ID / Touch ID / fingerprint before showing anything. Uses the phone's own lock
/// (with its passcode as the fallback); the fingerprint never leaves the phone.
class LockScreen extends StatefulWidget {
  const LockScreen({super.key, required this.userName, required this.onUnlocked, this.authenticate});

  final String userName;
  final VoidCallback onUnlocked;
  final Future<bool> Function()? authenticate;

  @override
  State<LockScreen> createState() => _LockScreenState();
}

Future<bool> deviceAuthenticate() async {
  final auth = LocalAuthentication();
  if (!await auth.isDeviceSupported()) return true; // no screen lock on this phone: nothing to check with
  return auth.authenticate(localizedReason: 'Unlock your Document Vault', persistAcrossBackgrounding: true);
}

class _LockScreenState extends State<LockScreen> {
  String? _error;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _unlock());
  }

  Future<void> _unlock() async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (await (widget.authenticate ?? deviceAuthenticate)()) {
        widget.onUnlocked();
        return;
      }
      if (mounted) setState(() => _error = 'Not unlocked. Try again.');
    } catch (e) {
      if (mounted) setState(() => _error = 'Face ID / fingerprint is not available: $e');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: Padding(
            padding: const EdgeInsets.all(32),
            child: Column(mainAxisSize: MainAxisSize.min, children: [
              Icon(Icons.lock_outline, size: 56, color: scheme.primary),
              const SizedBox(height: 16),
              Text('Document Vault', style: Theme.of(context).textTheme.headlineSmall),
              const SizedBox(height: 6),
              Text('Locked · ${widget.userName}', style: TextStyle(color: scheme.onSurfaceVariant)),
              const SizedBox(height: 28),
              FilledButton.icon(
                onPressed: _busy ? null : _unlock,
                icon: const Icon(Icons.fingerprint),
                label: const Text('Unlock'),
                style: FilledButton.styleFrom(minimumSize: const Size(220, 52)),
              ),
              if (_error != null) ...[
                const SizedBox(height: 14),
                Text(_error!, textAlign: TextAlign.center, style: TextStyle(color: scheme.error)),
              ],
            ]),
          ),
        ),
      ),
    );
  }
}
