import 'package:flutter/material.dart';

import '../app.dart';
import '../phone_reminders.dart';
import '../share_inbox.dart';

import 'chat_screen.dart';
import 'documents_screen.dart';
import 'scan_screen.dart';
import 'settings_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  int _tab = 0;
  final _documentsKey = GlobalKey<DocumentsScreenState>();
  final _scanKey = GlobalKey<ScanScreenState>();
  DateTime? _lastRefresh;
  ShareInbox? _shares;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _refreshReminders();
      _shares = VaultScope.of(context).shares..addListener(_takeShared);
      _takeShared(); // shared before the app was unlocked
    });
  }

  @override
  void dispose() {
    _shares?.removeListener(_takeShared);
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }

  /// "Send to → Document Vault": show the Scan tab and upload what was shared.
  void _takeShared() {
    final shares = _shares;
    if (!mounted || shares == null || !shares.hasFiles) return;
    final files = shares.take();
    setState(() => _tab = 2);
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      await _scanKey.currentState?.uploadShared(files);
      await ShareInbox.discard(files);
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refreshReminders();
  }

  /// Keep the phone's scheduled reminders in step with the vault (at most every 15 minutes).
  void _refreshReminders() {
    if (!mounted) return;
    final now = DateTime.now();
    if (_lastRefresh != null && now.difference(_lastRefresh!) < const Duration(minutes: 15)) return;
    _lastRefresh = now;
    final scope = VaultScope.of(context);
    refreshReminders(scope.api.reminderSchedule, scope.reminders);
  }

  @override
  Widget build(BuildContext context) {
    final pages = [
      const ChatScreen(),
      DocumentsScreen(key: _documentsKey),
      ScanScreen(key: _scanKey, onUploaded: () => _documentsKey.currentState?.reload()),
      const SettingsScreen(),
    ];
    return Scaffold(
      body: IndexedStack(index: _tab, children: pages), // keeps the chat when switching tabs
      bottomNavigationBar: NavigationBar(
        selectedIndex: _tab,
        onDestinationSelected: (i) {
          setState(() => _tab = i);
          if (i == 1) _documentsKey.currentState?.reload();
        },
        destinations: const [
          NavigationDestination(icon: Icon(Icons.chat_bubble_outline), selectedIcon: Icon(Icons.chat_bubble), label: 'Chat'),
          NavigationDestination(icon: Icon(Icons.folder_outlined), selectedIcon: Icon(Icons.folder), label: 'Documents'),
          NavigationDestination(icon: Icon(Icons.document_scanner_outlined), selectedIcon: Icon(Icons.document_scanner), label: 'Scan'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), selectedIcon: Icon(Icons.settings), label: 'Settings'),
        ],
      ),
    );
  }
}
