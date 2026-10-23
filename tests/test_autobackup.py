"""Scheduled encrypted backups to a folder (a second disk, a USB drive or a NAS share)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from backend.api.app import create_app
from backend.chunker.chunker import RegexTokenizer
from backend.security import autobackup, keys
from backend.security.backup import SUFFIX, restore_backup
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


def enable(store, nas, **over):
    settings = dict(enabled=True, folder=str(nas), frequency="daily", keep=7, password=PASSWORD)
    settings.update(over)
    return autobackup.configure(store, **settings)


def backups(folder):
    return sorted(p.name for p in folder.glob(f"*{SUFFIX}"))


def test_a_scheduled_backup_lands_in_the_folder_and_restores_on_a_new_mac(store, nas, tmp_path, monkeypatch):
    enable(store, nas)
    result = autobackup.run_now(store, n=FAST)
    assert result["last_error"] is None and result["last_success"] and len(backups(nas)) == 1
    assert result["last_file"] == backups(nas)[0]
    assert b"TW-778812" not in (nas / result["last_file"]).read_bytes()  # the NAS only holds ciphertext

    monkeypatch.setenv("VAULT_KEY_DIR", str(tmp_path / "new-mac-keys"))
    restore_backup(nas / result["last_file"], PASSWORD, tmp_path / "new")
    assert len(DocumentStore(tmp_path / "new", owner="arjun").list_documents()) == 1


def test_settings_are_checked_and_the_password_is_never_shown(store, nas, tmp_path):
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

    shown = enable(store, nas)
    assert shown["enabled"] and shown["has_password"] and PASSWORD not in json.dumps(shown)
    assert PASSWORD not in store._vault._meta(autobackup.META_KEY)  # the password is not in the database either
    assert keys.load_backup_password(store._vault.vault_id) == PASSWORD
    assert enable(store, nas, password=None)["has_password"]  # later changes keep the saved password

    off = autobackup.configure(store, enabled=False, folder="", frequency="daily", keep=7)
    assert not off["enabled"] and off["folder"] == str(nas)  # turning it off remembers the folder


def test_only_the_newest_backups_are_kept_and_other_files_are_left_alone(store, nas):
    enable(store, nas, keep=2)
    (nas / "holiday photos.zip").write_bytes(b"not ours")
    (nas / f"Some other tool{SUFFIX}").write_bytes(b"not ours either")
    for stamp in ("2026-01-01 09.00", "2026-01-02 09.00", "2026-01-03 09.00"):
        (nas / f"Document Vault backup {stamp}{SUFFIX}").write_bytes(b"old")
    result = autobackup.run_now(store, n=FAST)
    ours = [n for n in backups(nas) if n.startswith(autobackup.PREFIX)]
    assert len(ours) == 2 and result["last_file"] in ours and ours[0] == "Document Vault backup 2026-01-03 09.00" + SUFFIX
    assert (nas / "holiday photos.zip").exists() and (nas / f"Some other tool{SUFFIX}").exists()


def test_a_missing_nas_fails_loudly_without_creating_a_folder_and_retries_later(store, nas):
    enable(store, nas)
    nas.rmdir()  # the NAS is switched off: its mount point is gone
    result = autobackup.run_now(store, n=FAST)
    assert "isn't there" in result["last_error"] and result["last_success"] is None
    assert not nas.exists()  # it did not quietly create a local folder in its place

    config = autobackup.load(store)
    attempt = autobackup._parse(config["last_attempt"])
    assert not autobackup.is_due(config, attempt + timedelta(minutes=30))  # not hammering
    assert autobackup.is_due(config, attempt + timedelta(hours=1, minutes=1))

    nas.mkdir()  # back on
    assert autobackup.run_now(store, n=FAST)["last_error"] is None and len(backups(nas)) == 1


def test_when_a_backup_is_due(store, nas):
    assert not autobackup.is_due(autobackup.load(store))  # off by default
    enable(store, nas)
    config = autobackup.load(store)
    assert autobackup.is_due(config)  # never backed up: due right away
    autobackup.run_now(store, n=FAST)
    config = autobackup.load(store)
    done = autobackup._parse(config["last_success"])
    assert not autobackup.is_due(config, done + timedelta(hours=23))
    assert autobackup.is_due(config, done + timedelta(hours=25))
    weekly = dict(config, frequency="weekly")
    assert not autobackup.is_due(weekly, done + timedelta(days=6)) and autobackup.is_due(weekly, done + timedelta(days=8))


def test_a_lost_password_is_reported_not_raised(store, nas):
    enable(store, nas)
    keys.delete_backup_password(store._vault.vault_id)
    result = autobackup.run_now(store, n=FAST)
    assert "No backup password" in result["last_error"] and backups(nas) == []


def test_only_the_admin_can_use_the_backup_schedule(tmp_path, nas):
    store = DocumentStore(tmp_path / "index")
    app = create_app(store, FakeEmbedder(), TOK, frontend_dir=None)
    admin = signed_in(app, "abhinav")
    admin.post("/api/users", json={"name": "sunita", "password": "another long password"})

    body = {"enabled": True, "folder": str(nas), "frequency": "weekly", "keep": 3, "password": PASSWORD}
    saved = admin.put("/api/backup/schedule", json=body)
    assert saved.status_code == 200 and saved.json()["enabled"] and PASSWORD not in saved.text
    assert admin.put("/api/backup/schedule", json={**body, "folder": str(nas / "nope")}).status_code == 400
    assert admin.get("/api/backup/schedule").json()["frequency"] == "weekly"
    ran = admin.post("/api/backup/schedule/run").json()
    assert ran["last_error"] is None and len(backups(nas)) == 1

    actions = [e["action"] for e in admin.get("/api/activity").json()["entries"]]
    assert "backup_schedule_changed" in actions and "backup_made" in actions

    admin.post("/api/auth/logout")
    admin.post("/api/auth/login", json={"name": "sunita", "password": "another long password"})
    assert admin.get("/api/backup/schedule").status_code == 403
    assert admin.put("/api/backup/schedule", json=body).status_code == 403
    assert admin.post("/api/backup/schedule/run").status_code == 403
