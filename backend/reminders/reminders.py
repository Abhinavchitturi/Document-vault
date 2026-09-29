"""Phase 10: reminders before a policy or warranty expires and before a bill is due.

Built on the facts read in Phase 9 (documents.expires_on / due_on). Each person chooses how
many days ahead to be reminded (default: 30, 7 and 1 day before an expiry and on the day;
7 and 1 day before a due date and on the day) and marks items "done" (paid, renewed).

Where reminders appear:
    phone    the app schedules them as ordinary phone notifications each time it connects,
             so they appear on the day even when the Mac is off (schedule())
    Mac      desktop notifications while the vault runs, caught up if the Mac was off on the
             day (due_now(), backend/reminders/desktop.py)
    calendar a .ics file to import into any calendar app (calendar_file())
    web      the Reminders card on the Dashboard (items())
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, insert, select

from backend.database.schema import reminder_done, reminder_settings, reminders_sent, users

DEFAULTS = {"expiry_days": [30, 7, 1, 0], "due_days": [7, 1, 0], "time_of_day": "09:00"}
KINDS = {"expires": "expires_on", "due": "due_on"}
MISSED_DAYS = 30  # keep showing an item this long after its date, until it's marked done
CATCH_UP_DAYS = 3  # the Mac shows a reminder it missed (it was off) if it's at most this old


def _days(text: str) -> list[int]:
    return sorted({int(x) for x in text.split(",") if x.strip().isdigit() and 0 <= int(x) <= 365}, reverse=True)


def title_of(doc: dict) -> str:
    return Path(doc["filename"]).stem.replace("_", " ")


def describe(doc: dict, kind: str, when: date, days_left: int) -> tuple[str, str]:
    """(title, body) of a reminder: 'Bike Insurance 2026 expires in 7 days', '14 Mar 2027 · Rs. 1,842 · …'."""
    verb = ("warranty ends" if doc["category"] == "warranty" else "expires") if kind == "expires" else "is due"
    if days_left > 1:
        when_text = f"in {days_left} days"
    elif days_left == 1:
        when_text = "tomorrow"
    elif days_left == 0:
        when_text = "today"
    else:
        when_text = "yesterday" if days_left == -1 else f"{-days_left} days ago"
        verb = {"expires": "expired", "warranty ends": "warranty ended", "is due": "was due"}[verb]
    title = f"{title_of(doc)} {verb} {when_text}"
    amount = f"Rs. {float(doc['amount']):,.2f}".replace(".00", "") if doc.get("amount") else None
    shared = f"{doc['owner']}'s" if doc.get("mine") is False else None  # shared with this person (Phase 12)
    body = " · ".join(x for x in (f"{when.day} {when:%b %Y}", amount, doc.get("issuer"), doc.get("reference"), shared)
                      if x)
    return title, body


class Reminders:
    def __init__(self, engine):
        self.engine = engine

    # ---------- choices ----------

    def settings(self, user_id: int) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(reminder_settings).where(reminder_settings.c.user_id == user_id)).first()
            is_admin = conn.execute(select(users.c.is_admin).where(users.c.id == user_id)).scalar()
        if row is None:  # desktop notifications on for the Mac's owner (the admin), off for others
            return {**DEFAULTS, "desktop": bool(is_admin)}
        return {"expiry_days": _days(row.expiry_days), "due_days": _days(row.due_days),
                "time_of_day": row.time_of_day, "desktop": bool(row.desktop)}

    def save_settings(self, user_id: int, changes: dict) -> dict:
        current = {**self.settings(user_id), **{k: v for k, v in changes.items() if v is not None}}
        for key in ("expiry_days", "due_days"):
            current[key] = sorted({int(d) for d in current[key] if 0 <= int(d) <= 365}, reverse=True)
        hh, _, mm = str(current["time_of_day"]).partition(":")
        if not (hh.isdigit() and mm.isdigit() and int(hh) < 24 and int(mm) < 60):
            raise ValueError("time_of_day must look like 09:00")
        current["time_of_day"] = f"{int(hh):02d}:{int(mm):02d}"
        row = {"user_id": user_id, "expiry_days": ",".join(map(str, current["expiry_days"])),
               "due_days": ",".join(map(str, current["due_days"])), "time_of_day": current["time_of_day"],
               "desktop": bool(current["desktop"])}
        with self.engine.begin() as conn:
            conn.execute(delete(reminder_settings).where(reminder_settings.c.user_id == user_id))
            conn.execute(insert(reminder_settings).values(**row))
        return self.settings(user_id)

    # ---------- done marks ----------

    def _done(self, doc_ids) -> set[tuple[str, str, date]]:
        if not doc_ids:
            return set()
        with self.engine.connect() as conn:
            rows = conn.execute(select(reminder_done).where(reminder_done.c.document_id.in_(list(doc_ids)))).all()
        return {(r.document_id, r.kind, r.date) for r in rows}

    def mark_done(self, doc: dict, kind: str, done: bool = True) -> None:
        when = date.fromisoformat(doc[KINDS[kind]])
        with self.engine.begin() as conn:
            conn.execute(delete(reminder_done).where(reminder_done.c.document_id == doc["doc_id"],
                                                     reminder_done.c.kind == kind, reminder_done.c.date == when))
            if done:
                conn.execute(insert(reminder_done).values(document_id=doc["doc_id"], kind=kind, date=when,
                                                          done_at=datetime.now(timezone.utc)))

    # ---------- what's coming ----------

    def _dated(self, view) -> list[tuple[dict, str, date]]:
        out = []
        for doc in view.documents.values():
            for kind, field in KINDS.items():
                if doc.get(field):
                    out.append((doc, kind, date.fromisoformat(doc[field])))
        return out

    def items(self, view, today: date | None = None, horizon: int | None = None) -> list[dict]:
        """Open reminders: dated items from MISSED_DAYS ago up to the furthest reminder ahead (or
        `horizon` days), not marked done, soonest first."""
        today = today or date.today()
        prefs = self.settings(view.owner_id)
        dated = self._dated(view)
        done = self._done({d["doc_id"] for d, _, _ in dated})
        out = []
        for doc, kind, when in dated:
            leads = prefs["expiry_days"] if kind == "expires" else prefs["due_days"]
            ahead = horizon if horizon is not None else max(leads, default=0)
            left = (when - today).days
            if not -MISSED_DAYS <= left <= ahead:
                continue
            is_done = (doc["doc_id"], kind, when) in done
            next_notice = next((when - timedelta(days=d) for d in sorted(leads, reverse=True)
                                if when - timedelta(days=d) >= today), None)
            title, body = describe(doc, kind, when, left)
            out.append({"doc_id": doc["doc_id"], "filename": doc["filename"], "category": doc["category"],
                        "owner": doc.get("owner"), "mine": doc.get("mine", True),
                        "kind": kind, "date": when.isoformat(), "days_left": left, "done": is_done,
                        "status": "done" if is_done else "overdue" if left < 0 else "today" if left == 0
                        else "soon" if left <= 7 else "upcoming",
                        "next_notice": next_notice.isoformat() if next_notice and not is_done else None,
                        "title": title, "body": body, "amount": doc.get("amount"), "issuer": doc.get("issuer")})
        return sorted(out, key=lambda i: (i["done"], i["date"]))

    def schedule(self, view, now: datetime | None = None, days: int = 400) -> list[dict]:
        """Every future reminder for the phone to schedule, at the person's chosen time of day."""
        now = now or datetime.now()
        prefs = self.settings(view.owner_id)
        hh, mm = map(int, prefs["time_of_day"].split(":"))
        dated = self._dated(view)
        done = self._done({d["doc_id"] for d, _, _ in dated})
        out = []
        for doc, kind, when in dated:
            if (doc["doc_id"], kind, when) in done:
                continue
            for lead in prefs["expiry_days"] if kind == "expires" else prefs["due_days"]:
                at = datetime.combine(when - timedelta(days=lead), time(hh, mm))
                if now < at <= now + timedelta(days=days):
                    title, body = describe(doc, kind, when, lead)
                    key = f"{doc['doc_id']}|{kind}|{when}|{lead}"
                    out.append({"id": int(hashlib.sha1(key.encode()).hexdigest()[:7], 16),  # stable, fits an int
                                "at": at.isoformat(timespec="minutes"), "title": title, "body": body,
                                "doc_id": doc["doc_id"]})
        return sorted(out, key=lambda r: r["at"])

    def due_now(self, view, today: date | None = None) -> list[dict]:
        """Reminders whose day has come (or came in the last CATCH_UP_DAYS while the Mac was off)
        and that the Mac hasn't shown yet."""
        today = today or date.today()
        prefs = self.settings(view.owner_id)
        dated = self._dated(view)
        done = self._done({d["doc_id"] for d, _, _ in dated})
        with self.engine.connect() as conn:
            sent = {(r.document_id, r.kind, r.date, r.lead) for r in conn.execute(select(reminders_sent)).all()}
        out = []
        for doc, kind, when in dated:
            if (doc["doc_id"], kind, when) in done:
                continue
            leads = prefs["expiry_days"] if kind == "expires" else prefs["due_days"]
            fired = [d for d in leads if 0 <= (today - (when - timedelta(days=d))).days <= CATCH_UP_DAYS]
            if not fired:
                continue
            lead = min(fired)  # the most recent one: no point showing "in 30 days" after "in 7 days"
            if any((doc["doc_id"], kind, when, d) in sent for d in leads if d <= max(fired)):
                continue
            title, body = describe(doc, kind, when, (when - today).days)
            out.append({"doc_id": doc["doc_id"], "kind": kind, "date": when, "lead": lead, "title": title, "body": body})
        return out

    def mark_sent(self, reminder: dict) -> None:
        with self.engine.begin() as conn:
            conn.execute(insert(reminders_sent).values(document_id=reminder["doc_id"], kind=reminder["kind"],
                                                       date=reminder["date"], lead=reminder["lead"],
                                                       sent_at=datetime.now(timezone.utc)))

    # ---------- calendar ----------

    def calendar_file(self, view) -> str:
        """An iCalendar (.ics) file: one all-day event per expiry and due date, with alarms at the
        person's reminder days. Import it once into Apple Calendar, Google Calendar, Outlook..."""
        prefs = self.settings(view.owner_id)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Family Document Vault//Reminders//EN",
                 "CALSCALE:GREGORIAN", "X-WR-CALNAME:Document Vault"]
        for doc, kind, when in sorted(self._dated(view), key=lambda x: x[2]):
            title, body = describe(doc, kind, when, 0)
            summary = title.removesuffix(" today")
            lines += ["BEGIN:VEVENT", f"UID:{doc['doc_id']}-{kind}@document-vault", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{when:%Y%m%d}", f"DTEND;VALUE=DATE:{when + timedelta(days=1):%Y%m%d}",
                      f"SUMMARY:{_ics_text(summary)}", f"DESCRIPTION:{_ics_text(body)}"]
            for lead in prefs["expiry_days"] if kind == "expires" else prefs["due_days"]:
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_text(summary)}",
                          f"TRIGGER:-P{lead}D" if lead else "TRIGGER:PT9H", "END:VALARM"]
            lines.append("END:VEVENT")
        lines.append("END:VCALENDAR")
        return "\r\n".join(lines) + "\r\n"


def _ics_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
