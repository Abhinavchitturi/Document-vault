// Reminders as ordinary phone notifications (Phase 10).
//
// Each time the app reaches the vault it asks for every upcoming reminder (GET
// /api/reminders/schedule) and hands them to Android / iOS to show at the right time. From
// then on the phone shows them by itself: with the app closed, the phone offline and the Mac
// switched off. Replacing the whole set each time keeps "Paid" / "Done" and changed reminder
// days in step with the vault.

import 'package:flutter/foundation.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:timezone/data/latest.dart' as tzdata;
import 'package:timezone/timezone.dart' as tz;

class ScheduledReminder {
  ScheduledReminder({required this.id, required this.at, required this.title, required this.body});

  final int id; // stable per document, date and lead, so rescheduling replaces rather than duplicates
  final DateTime at; // local wall-clock time
  final String title;
  final String body;

  factory ScheduledReminder.fromJson(Map<String, dynamic> j) => ScheduledReminder(
        id: j['id'] as int,
        at: DateTime.parse(j['at'] as String),
        title: j['title'] as String,
        body: j['body'] as String,
      );
}

/// What the app needs from the phone's notification system (a fake stands in for tests).
abstract class ReminderScheduler {
  /// Replace every scheduled reminder with [reminders]; returns how many were scheduled.
  Future<int> replaceAll(List<ScheduledReminder> reminders);

  Future<void> clear();
}

class PhoneNotifications implements ReminderScheduler {
  final _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;

  static const _details = NotificationDetails(
    android: AndroidNotificationDetails('reminders', 'Reminders',
        channelDescription: 'Policies and warranties expiring, bills falling due',
        importance: Importance.high, priority: Priority.high),
    iOS: DarwinNotificationDetails(),
  );

  Future<void> _init() async {
    if (_ready) return;
    tzdata.initializeTimeZones();
    try {
      tz.setLocalLocation(tz.getLocation((await FlutterTimezone.getLocalTimezone()).identifier));
    } catch (_) {
      // unknown zone name: keep UTC-based scheduling, which is off by at most the zone offset
    }
    await _plugin.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
        iOS: DarwinInitializationSettings(),
      ),
    );
    await _plugin
        .resolvePlatformSpecificImplementation<AndroidFlutterLocalNotificationsPlugin>()
        ?.requestNotificationsPermission(); // Android 13+ asks once
    _ready = true;
  }

  @override
  Future<int> replaceAll(List<ScheduledReminder> reminders) async {
    await _init();
    await _plugin.cancelAll();
    final now = DateTime.now();
    var count = 0;
    // Phones cap pending alarms (iOS: 64); the soonest matter most.
    for (final r in reminders.where((r) => r.at.isAfter(now)).take(60)) {
      await _plugin.zonedSchedule(
        id: r.id,
        scheduledDate: tz.TZDateTime.from(r.at, tz.local),
        notificationDetails: _details,
        // Inexact is fine for a reminder (within minutes) and needs no special alarm permission.
        androidScheduleMode: AndroidScheduleMode.inexactAllowWhileIdle,
        title: r.title,
        body: r.body,
      );
      count++;
    }
    return count;
  }

  @override
  Future<void> clear() async {
    await _init();
    await _plugin.cancelAll();
  }
}

/// When the phone last refreshed its reminders from the vault, and how many it scheduled.
final reminderStatus = ValueNotifier<({DateTime at, int count})?>(null);

/// Fetch the schedule and hand it to the phone. Quiet on failure: the reminders already
/// scheduled keep working until the next successful refresh.
Future<void> refreshReminders(Future<List<ScheduledReminder>> Function() fetch, ReminderScheduler scheduler) async {
  try {
    final count = await scheduler.replaceAll(await fetch());
    reminderStatus.value = (at: DateTime.now(), count: count);
  } catch (_) {
    // Mac off or notifications unavailable: try again next time
  }
}
