"""Scheduled encrypted backups to a folder (a second disk, a USB drive or a NAS share)."""

from __future__ import annotations

import json
import shutil
from datetime import timedelta

import pytest

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.security import autobackup, keys
from backend.security.backup import SUFFIX, BackupError, restore_backup, verify_backup
from backend.upload.ingest import ingest_file
from backend.vector_store.store import DocumentStore
from tests.fakes import FakeEmbedder, signed_in

TOK = RegexTokenizer()
PASSWORD = "a long backup password"
FAST = 2 ** 14  # cheap key derivation: these tests make several backups


@pytest.fixture
def store(tmp_path):
    s = DocumentStore(tmp_path / "index", owner="arjun")
    note = tmp_path / "bike_policy.txt"
    note.write_text("Two-wheeler insurance. Policy number TW-778812. Expires 14 March 2027.")
    ingest_file(note, s, FakeEmbedder(), TOK)
    return s


@pytest.fixture
def nas(tmp_path):
    folder = tmp_path / "nas"
    folder.mkdir()
    return folder


@pytest.fixture
def usb(tmp_path):
    folder = tmp_path / "usb"
    folder.mkdir()
    return folder


def enable(store, *folders, **over):
    settings = dict(enabled=True, folders=[str(f) for f in folders], frequency="daily", keep=7, password=PASSWORD)
    settings.update(over)
    return autobackup.configure(store, **settings)


def dest(result, folder):
    return next(d for d in result["folders"] if d["path"] == str(folder))


def backups(folder):
    return sorted(p.name for p in folder.glob(f"*{SUFFIX}"))


def test_a_scheduled_backup_lands_in_the_folder_and_restores_on_a_new_mac(store, nas, tmp_path, monkeypatch):
    enable(store, nas)
    result = autobackup.run_now(store, n=FAST)
    d = dest(result, nas)
    assert d["last_error"] is None and d["last_success"] and len(backups(nas)) == 1
    assert d["last_file"] == backups(nas)[0] and d["last_verified"] and d["documents"] == 1
    assert b"TW-778812" not in (nas / d["last_file"]).read_bytes()  # the NAS only holds ciphertext

    monkeypatch.setenv("VAULT_KEY_DIR", str(tmp_path / "new-mac-keys"))
    restore_backup(nas / d["last_file"], PASSWORD, tmp_path / "new")
    assert len(DocumentStore(tmp_path / "new", owner="arjun").list_documents()) == 1


def test_settings_are_checked_and_the_password_is_never_shown(store, nas, tmp_path):
    with pytest.raises(autobackup.AutoBackupError, match="Choose the folder"):
        enable(store)
    for name in "abcdef":
        (nas / name).mkdir()
    with pytest.raises(autobackup.AutoBackupError, match="at most"):
        enable(store, *[nas / name for name in "abcdef"])
    with pytest.raises(autobackup.AutoBackupError, match="Choose a backup password"):
        enable(store, nas, password=None)
    with pytest.raises(autobackup.AutoBackupError, match="at least"):
        enable(store, nas, password="short")
    with pytest.raises(autobackup.AutoBackupError, match="isn't there"):
        enable(store, tmp_path / "unplugged")
    with pytest.raises(autobackup.AutoBackupError, match="full path"):
        enable(store, "relative/folder")
    with pytest.raises(autobackup.AutoBackupError, match="daily or weekly"):
        enable(store, nas, frequency="hourly")
    assert not autobackup.status(store)["enabled"]
    assert [d["path"] for d in enable(store, nas, nas)["folders"]] == [str(nas)]  # the same folder twice is one location
    autobackup.configure(store, enabled=False, folders=[], frequency="daily", keep=7)

    shown = enable(store, nas)
    assert shown["enabled"] and shown["has_password"] and PASSWORD not in json.dumps(shown)
    assert PASSWORD not in store._vault._meta(autobackup.META_KEY)  # the password is not in the database either
    assert keys.load_backup_password(store._vault.vault_id) == PASSWORD
    assert enable(store, nas, password=None)["has_password"]  # later changes keep the saved password

    off = autobackup.configure(store, enabled=False, folders=[], frequency="daily", keep=7)
    assert not off["enabled"] and [d["path"] for d in off["folders"]] == [str(nas)]  # turning it off remembers the folder


def test_only_the_newest_backups_are_kept_and_other_files_are_left_alone(store, nas):
    enable(store, nas, keep=2)
    (nas / "holiday photos.zip").write_bytes(b"not ours")
    (nas / f"Some other tool{SUFFIX}").write_bytes(b"not ours either")
    for stamp in ("2026-01-01 09.00", "2026-01-02 09.00", "2026-01-03 09.00"):
        (nas / f"Document Vault backup {stamp}{SUFFIX}").write_bytes(b"old")
    result = autobackup.run_now(store, n=FAST)
    ours = [n for n in backups(nas) if n.startswith(autobackup.PREFIX)]
    assert len(ours) == 2 and dest(result, nas)["last_file"] in ours and ours[0] == "Document Vault backup 2026-01-03 09.00" + SUFFIX
    assert (nas / "holiday photos.zip").exists() and (nas / f"Some other tool{SUFFIX}").exists()


def test_a_missing_nas_fails_loudly_without_creating_a_folder_and_retries_later(store, nas):
    enable(store, nas)
    nas.rmdir()  # the NAS is switched off: its mount point is gone
    d = dest(autobackup.run_now(store, n=FAST), nas)
    assert "isn't there" in d["last_error"] and d["last_success"] is None
    assert not nas.exists()  # it did not quietly create a local folder in its place

    config = autobackup.load(store)
    attempt = autobackup._parse(config["folders"][0]["last_attempt"])
    assert not autobackup.is_due(config, attempt + timedelta(minutes=30))  # not hammering
    assert autobackup.is_due(config, attempt + timedelta(hours=1, minutes=1))

    nas.mkdir()  # back on
    assert dest(autobackup.run_now(store, n=FAST), nas)["last_error"] is None and len(backups(nas)) == 1


def test_when_a_backup_is_due(store, nas):
    assert not autobackup.is_due(autobackup.load(store))  # off by default
    enable(store, nas)
    config = autobackup.load(store)
    assert autobackup.is_due(config)  # never backed up: due right away
    autobackup.run_now(store, n=FAST)
    config = autobackup.load(store)
    done = autobackup._parse(config["folders"][0]["last_success"])
    assert not autobackup.is_due(config, done + timedelta(hours=23))
    assert autobackup.is_due(config, done + timedelta(hours=25))
    weekly = dict(config, frequency="weekly")
    assert not autobackup.is_due(weekly, done + timedelta(days=6)) and autobackup.is_due(weekly, done + timedelta(days=8))


def test_a_lost_password_is_reported_not_raised(store, nas):
    enable(store, nas)
    keys.delete_backup_password(store._vault.vault_id)
    result = autobackup.run_now(store, n=FAST)
    assert "No backup password" in dest(result, nas)["last_error"] and backups(nas) == []


def test_every_location_gets_a_checked_copy_and_one_failing_does_not_stop_the_other(store, nas, usb):
    result = enable(store, nas, usb)
    assert result["good_copies"] == 0
    result = autobackup.run_now(store, n=FAST)
    assert result["good_copies"] == 2
    assert backups(nas) == backups(usb) == [dest(result, nas)["last_file"]]  # the same backup in both places
    assert dest(result, usb)["documents"] == 1

    shutil.rmtree(usb)  # the USB drive is pulled out
    result = autobackup.run_now(store, n=FAST)
    assert "isn't there" in dest(result, usb)["last_error"] and dest(result, nas)["last_error"] is None
    assert result["good_copies"] == 1 and len(backups(nas)) == 1  # same minute: the NAS copy was simply refreshed


def test_a_saved_location_can_stay_while_its_drive_is_unplugged_but_a_new_one_must_exist(store, nas, usb, tmp_path):
    enable(store, nas, usb)
    usb.rmdir()
    kept = enable(store, nas, usb, keep=3)  # changing another setting must still work
    assert [d["path"] for d in kept["folders"]] == [str(nas), str(usb)] and kept["keep"] == 3
    with pytest.raises(autobackup.AutoBackupError, match="isn't there"):
        enable(store, nas, usb, tmp_path / "never-existed")


def test_a_damaged_copy_is_removed_reported_and_never_replaces_a_good_one(store, nas, usb, monkeypatch):
    enable(store, nas, usb, keep=1)
    good = (usb / "Document Vault backup 2026-01-01 09.00").with_suffix(SUFFIX)
    good.write_bytes(b"older, still the only copy on the USB drive")

    real_copy = autobackup.shutil.copyfile

    def flaky_copy(src, dst, *a, **kw):
        out = real_copy(src, dst, *a, **kw)
        if str(usb) in str(dst):  # the USB drive silently corrupts what it is given
            data = bytearray(open(dst, "rb").read())
            data[len(data) // 2] ^= 0xFF
            open(dst, "wb").write(bytes(data))
        return out

    monkeypatch.setattr(autobackup.shutil, "copyfile", flaky_copy)
    result = autobackup.run_now(store, n=FAST)
    assert dest(result, nas)["last_error"] is None and len(backups(nas)) == 1
    d = dest(result, usb)
    assert "did not pass its check" in d["last_error"] and d["last_success"] is None
    assert backups(usb) == [good.name]  # the bad copy is gone, and the older backup was not pruned for it
    assert result["good_copies"] == 1


def test_scheduled_runs_only_touch_locations_that_are_due(store, nas, usb):
    enable(store, nas, usb)
    shutil.rmtree(usb)  # the USB drive is not plugged in for the first backup
    autobackup.run_now(store, n=FAST)
    usb.mkdir()
    before = autobackup.status(store)
    autobackup.run_now(store, n=FAST, only_due=True)  # the NAS is done for a day, the USB drive retries after an hour
    after = autobackup.status(store)
    assert [d["last_attempt"] for d in after["folders"]] == [d["last_attempt"] for d in before["folders"]]
    assert backups(usb) == [] and len(backups(nas)) == 1

    config = autobackup.load(store)
    failed_at = autobackup._parse(config["folders"][1]["last_attempt"])
    assert not autobackup.is_due(config, failed_at + timedelta(minutes=30))
    assert autobackup.is_due(config, failed_at + timedelta(hours=1, minutes=1))  # because of the USB drive alone


def test_a_location_on_the_same_disk_as_the_vault_is_flagged(store, nas, usb):
    # in the tests everything shares one disk, which is exactly the case to warn about
    result = enable(store, nas, usb)
    assert "same disk as the vault" in dest(result, nas)["warning"]


def test_verify_backup_catches_wrong_passwords_and_damage(store, nas, tmp_path):
    enable(store, nas)
    path = nas / dest(autobackup.run_now(store, n=FAST), nas)["last_file"]
    found = verify_backup(path, PASSWORD)
    assert found["documents"] == 1 and found["files"] >= 1

    with pytest.raises(BackupError, match="Wrong backup password"):
        verify_backup(path, "not the right password")
    data = path.read_bytes()
    flipped = tmp_path / "flipped.dvbackup"
    flipped.write_bytes(data[: len(data) // 2] + bytes([data[len(data) // 2] ^ 1]) + data[len(data) // 2 + 1:])
    with pytest.raises(BackupError, match="damaged"):
        verify_backup(flipped, PASSWORD)
    cut = tmp_path / "cut.dvbackup"
    cut.write_bytes(data[: len(data) - 50])
    with pytest.raises(BackupError):
        verify_backup(cut, PASSWORD)
    (tmp_path / "x.dvbackup").write_bytes(b"hello")
    with pytest.raises(BackupError, match="not a Document Vault backup"):
        verify_backup(tmp_path / "x.dvbackup", PASSWORD)


def test_only_the_admin_can_use_the_backup_schedule(tmp_path, nas):
    store = DocumentStore(tmp_path / "index")
    app = create_app(store, FakeEmbedder(), TOK, frontend_dir=None)
    admin = signed_in(app, "abhinav")
    admin.post("/api/users", json={"name": "sunita", "password": "another long password"})

    body = {"enabled": True, "folders": [str(nas)], "frequency": "weekly", "keep": 3, "password": PASSWORD}
    saved = admin.put("/api/backup/schedule", json=body)
    assert saved.status_code == 200 and saved.json()["enabled"] and PASSWORD not in saved.text
    assert admin.put("/api/backup/schedule", json={**body, "folders": [str(nas / "nope")]}).status_code == 400
    assert admin.get("/api/backup/schedule").json()["frequency"] == "weekly"
    ran = admin.post("/api/backup/schedule/run").json()
    assert ran["folders"][0]["last_error"] is None and ran["good_copies"] == 1 and len(backups(nas)) == 1

    actions = [e["action"] for e in admin.get("/api/activity").json()["entries"]]
    assert "backup_schedule_changed" in actions and "backup_made" in actions

    admin.post("/api/auth/logout")
    admin.post("/api/auth/login", json={"name": "sunita", "password": "another long password"})
    assert admin.get("/api/backup/schedule").status_code == 403
    assert admin.put("/api/backup/schedule", json=body).status_code == 403
    assert admin.post("/api/backup/schedule/run").status_code == 403
