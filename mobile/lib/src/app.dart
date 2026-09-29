import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path_provider/path_provider.dart';

import 'pairing.dart';
import 'phone_reminders.dart';
import 'screens/home_screen.dart';
import 'screens/lock_screen.dart';
import 'screens/pair_screen.dart';
import 'upload_queue.dart';
import 'vault_client.dart';

const brand = Color(0xFF5B4FE9);
const lockAfter = Duration(minutes: 2); // back from the background after this long: Face ID / fingerprint again

ThemeData _theme(Brightness b) {
  final scheme = ColorScheme.fromSeed(seedColor: brand, brightness: b);
  return ThemeData(
    colorScheme: scheme,
    useMaterial3: true,
    scaffoldBackgroundColor: b == Brightness.light ? const Color(0xFFF6F7FB) : null,
    cardTheme: const CardThemeData(elevation: 0, margin: EdgeInsets.zero),
    inputDecorationTheme: InputDecorationTheme(
      filled: true,
      border: OutlineInputBorder(borderRadius: BorderRadius.circular(14), borderSide: BorderSide.none),
    ),
  );
}

/// What every screen can reach: the vault connection, and what to do when the phone gets unpaired.
class VaultScope extends InheritedWidget {
  const VaultScope({super.key, required this.api, required this.pairing, required this.store,
      required this.onUnpaired, required this.queue, required this.reminders, required super.child});

  /// The phone's notification system, for reminders.
  final ReminderScheduler reminders;

  final VaultApi api;

  /// Scans waiting for the Mac to be reachable.
  final UploadQueue queue;
  final Pairing pairing;
  final PairingStore store;
  final Future<void> Function() onUnpaired;

  static VaultScope of(BuildContext context) => context.dependOnInheritedWidgetOfExactType<VaultScope>()!;

  /// Show an error; if the vault no longer knows this phone, go back to pairing.
  static void handle(BuildContext context, Object error) {
    final scope = of(context);
    if (error is VaultException && error.unpaired) {
      scope.onUnpaired();
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(error.toString())));
  }

  @override
  bool updateShouldNotify(VaultScope oldWidget) => api != oldWidget.api || pairing != oldWidget.pairing;
}

class VaultApp extends StatefulWidget {
  const VaultApp({super.key, required this.store, required this.connect, this.authenticate, this.storageDir,
      this.reminders});

  /// Phone notifications for reminders; replaced in tests.
  final ReminderScheduler? reminders;

  final PairingStore store;
  final VaultApi Function(Pairing) connect;

  /// Face ID / fingerprint check; replaced in tests.
  final Future<bool> Function()? authenticate;

  /// Where the upload queue lives (default: the app's private documents folder).
  final Directory? storageDir;

  @override
  State<VaultApp> createState() => _VaultAppState();
}

class _VaultAppState extends State<VaultApp> with WidgetsBindingObserver {
  Pairing? _pairing;
  VaultApi? _api;
  UploadQueue? _queue;
  late final ReminderScheduler _reminders = widget.reminders ?? PhoneNotifications();
  bool _loading = true;
  bool _locked = false;
  DateTime? _backgroundSince;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _load();
  }

  Future<void> _load() async {
    final pairing = await widget.store.load();
    final lock = pairing != null && await widget.store.lockEnabled();
    final queue = UploadQueue(widget.storageDir ?? await getApplicationDocumentsDirectory());
    if (!mounted) return;
    setState(() {
      _queue = queue;
      _pairing = pairing;
      _api = pairing == null ? null : widget.connect(pairing);
      _locked = lock;
      _loading = false;
    });
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.paused || state == AppLifecycleState.hidden) {
      _backgroundSince ??= DateTime.now();
    } else if (state == AppLifecycleState.resumed) {
      final since = _backgroundSince;
      _backgroundSince = null;
      if (_pairing != null && since != null && DateTime.now().difference(since) > lockAfter) {
        widget.store.lockEnabled().then((on) {
          if (on && mounted) setState(() => _locked = true);
        });
      }
    }
  }

  Future<void> _paired(Pairing p) async {
    await widget.store.save(p);
    setState(() {
      _pairing = p;
      _api = widget.connect(p);
      _locked = false;
    });
  }

  Future<void> _unpaired() async {
    await widget.store.clear();
    setState(() {
      _pairing = null;
      _api = null;
    });
    // Waiting scans belonged to that pairing (and a removed phone may be a lost one): don't keep them.
    final queue = _queue;
    if (queue != null) unawaited(_clear(queue));
    unawaited(_reminders.clear().catchError((_) {})); // an unpaired phone shouldn't keep reminding
  }

  static Future<void> _clear(UploadQueue queue) async {
    for (final item in await queue.list()) {
      await queue.remove(item.id);
    }
  }

  @override
  Widget build(BuildContext context) {
    Widget home;
    if (_loading) {
      home = const Scaffold(body: Center(child: CircularProgressIndicator()));
    } else if (_pairing == null) {
      home = PairScreen(onPaired: _paired);
    } else if (_locked) {
      home = LockScreen(
          userName: _pairing!.userName, authenticate: widget.authenticate, onUnlocked: () => setState(() => _locked = false));
    } else {
      home = const HomeScreen();
    }
    final signedIn = !_loading && _pairing != null && !_locked;
    return MaterialApp(
      title: 'Document Vault',
      debugShowCheckedModeBanner: false,
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      // Above the navigator, so screens opened on top (a source page) can reach the vault too.
      builder: (context, child) => signedIn
          ? VaultScope(api: _api!, pairing: _pairing!, store: widget.store, onUnpaired: _unpaired, queue: _queue!,
              reminders: _reminders, child: child!)
          : child!,
      home: home,
    );
  }
}
