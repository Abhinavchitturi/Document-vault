"""Phase 10: reminders before expiries and due dates."""

from datetime import date, datetime, timedelta

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.reminders import desktop
from backend.reminders.reminders import Reminders, describe
from backend.upload.ingest import ingest_file
from backend.vector_store.store import DocumentStore
from tests.fakes import PASSWORD, FakeEmbedder, signed_in

TODAY = date(2026, 9, 1)


def vault_with(tmp_path, **dates):
    """A vault whose documents carry the given facts: name=(kind, date, amount)."""
    store = DocumentStore(tmp_path / "index", owner="abhinav")
    for name, (kind, when, amount) in dates.items():
        (tmp_path / f"{name}.txt").write_text(f"{name} document {name}")
        doc = ingest_file(tmp_path / f"{name}.txt", store, FakeEmbedder(), RegexTokenizer(),
                          "bill" if kind == "due" else "insurance").document
        store.update_facts(doc["doc_id"], {"expires_on" if kind == "expires" else "due_on": when.isoformat(),
                                           "amount": amount, "issuer": "Test Co"}, "rules")
    return store


def test_reminder_text():
    doc = {"filename": "Bike_Insurance_2026.pdf", "category": "insurance", "amount": "1842", "issuer": "Sahyadri"}
    assert describe(doc, "expires", date(2027, 3, 14), 7) == ("Bike Insurance 2026 expires in 7 days",
                                                              "14 Mar 2027 · Rs. 1,842 · Sahyadri")
    bill = {"filename": "Electricity.pdf", "category": "bill", "amount": "1355.82"}
    assert describe(bill, "due", date(2026, 9, 5), 1)[0] == "Electricity is due tomorrow"
    assert describe(bill, "due", date(2026, 9, 5), -2)[0] == "Electricity was due 2 days ago"
    assert describe(doc, "expires", date(2027, 3, 14), -1)[0] == "Bike Insurance 2026 expired yesterday"
    assert describe({**doc, "category": "warranty"}, "expires", date(2027, 3, 14), 0)[0].endswith("warranty ends today")


def test_items_window_status_and_done(tmp_path):
    store = vault_with(tmp_path, bike=("expires", TODAY + timedelta(days=20), "1842"),
                       power=("due", TODAY + timedelta(days=3), "2486"),
                       old=("due", TODAY - timedelta(days=5), "100"),
                       far=("expires", TODAY + timedelta(days=200), None))
    r = Reminders(store.engine)
    items = r.items(store, today=TODAY)
    assert [(i["filename"], i["status"]) for i in items] == [("old.txt", "overdue"), ("power.txt", "soon"), ("bike.txt", "upcoming")]
    bike = items[-1]
    assert bike["next_notice"] == (TODAY + timedelta(days=13)).isoformat()  # 7 days before
    r.mark_done(store.get(items[0]["doc_id"]), "due")
    after = r.items(store, today=TODAY)
    assert after[-1]["filename"] == "old.txt" and after[-1]["done"] and after[-1]["next_notice"] is None


def test_phone_schedule_follows_the_persons_choices(tmp_path):
    store = vault_with(tmp_path, bike=("expires", TODAY + timedelta(days=20), "1842"))
    r = Reminders(store.engine)
    now = datetime.combine(TODAY, datetime.min.time())
    first = r.schedule(store, now=now)
    assert [s["at"] for s in first] == [f"{TODAY + timedelta(days=d)}T09:00" for d in (13, 19, 20)]  # 7, 1, 0 days before
    assert first[0]["title"] == "bike expires in 7 days"
    assert r.schedule(store, now=now) == first  # stable ids: rescheduling replaces, never duplicates
    r.save_settings(store.owner_id, {"expiry_days": [14], "time_of_day": "7:30"})
    assert [s["at"] for s in r.schedule(store, now=now)] == [f"{TODAY + timedelta(days=6)}T07:30"]
    r.mark_done(store.get(first[0]["doc_id"]), "expires")
    assert r.schedule(store, now=now) == []


def test_mac_shows_each_reminder_once_and_catches_up(tmp_path):
    store = vault_with(tmp_path, power=("due", TODAY + timedelta(days=7), "2486"))
    r = Reminders(store.engine)
    from backend.auth.accounts import Accounts

    Accounts(store.engine).setup("abhinav", PASSWORD)  # the admin: Mac notifications on by default
    assert r.due_now(store, today=TODAY - timedelta(days=1)) == []  # not yet
    missed = r.due_now(store, today=TODAY + timedelta(days=2))  # the Mac was off on the day: caught up
    assert [(m["lead"], m["title"]) for m in missed] == [(7, "power is due in 5 days")]

    shown = []
    monkey_today = TODAY
    real_due_now = r.due_now
    r.due_now = lambda view: real_due_now(view, today=monkey_today)
    assert desktop.check_once(store, r, notify=lambda t, b: shown.append((t, b)) or True) == 1
    assert shown == [("power is due in 7 days", "abhinav: 8 Sep 2026 · Rs. 2,486 · Test Co")]
    assert desktop.check_once(store, r, notify=lambda t, b: shown.append((t, b)) or True) == 0  # not repeated

    r.save_settings(store.owner_id, {"desktop": False})
    monkey_today = TODAY + timedelta(days=6)  # the 1-day reminder would be due now
    assert desktop.check_once(store, r, notify=lambda t, b: shown.append((t, b)) or True) == 0


def test_calendar_file(tmp_path):
    store = vault_with(tmp_path, bike=("expires", date(2027, 3, 14), "1842"))
    ics = Reminders(store.engine).calendar_file(store)
    assert ics.startswith("BEGIN:VCALENDAR") and "DTSTART;VALUE=DATE:20270314" in ics
    assert "SUMMARY:bike expires" in ics and "TRIGGER:-P30D" in ics and "Rs. 1\\,842" in ics
    assert ics.count("BEGIN:VEVENT") == 1 and ics.endswith("END:VCALENDAR\r\n")


def test_reminder_api_is_per_person(tmp_path):
    store = DocumentStore(tmp_path / "index", owner="abhinav")
    store.set_family_mode(False)  # private mode (Phase 12)
    app = create_app(store, FakeEmbedder(), RegexTokenizer(), frontend_dir=None)
    me = signed_in(app)
    due = (date.today() + timedelta(days=3)).strftime("%d %B %Y")
    doc = me.post("/api/documents", files=[("files", ("gas.txt", f"Gas bill. Amount due: Rs. 500. Due date: {due}".encode()))]).json()[0]["document"]
    got = me.get("/api/reminders").json()
    assert [i["status"] for i in got["items"]] == ["soon"] and got["settings"]["desktop"] is True
    assert me.get("/api/reminders/schedule").json()["reminders"]
    ics = me.get("/api/reminders/calendar.ics")
    assert ics.headers["content-type"].startswith("text/calendar") and "gas" in ics.text
    assert me.post("/api/reminders/settings", json={"time_of_day": "25:00"}).status_code == 400
    assert me.post("/api/reminders/settings", json={"due_days": [3, 0]}).json()["due_days"] == [3, 0]

    me.post("/api/users", json={"name": "Sunita", "password": PASSWORD + "2"})
    sunita = TestClient(app)
    sunita.post("/api/auth/login", json={"name": "Sunita", "password": PASSWORD + "2"})
    assert sunita.get("/api/reminders").json()["items"] == []
    assert sunita.get("/api/reminders").json()["settings"]["desktop"] is False  # not the Mac's owner
    assert sunita.post("/api/reminders/done", json={"doc_id": doc["doc_id"], "kind": "due"}).status_code == 404
    done = me.post("/api/reminders/done", json={"doc_id": doc["doc_id"], "kind": "due"}).json()["items"]
    assert done[0]["done"] is True
    assert me.get("/api/reminders/schedule").json()["reminders"] == []
