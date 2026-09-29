import 'package:flutter/material.dart';

import '../app.dart';
import '../phone_reminders.dart';

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
  DateTime? _lastRefresh;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _refreshReminders());
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    super.dispose();
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
      ScanScreen(onUploaded: () => _documentsKey.currentState?.reload()),
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
