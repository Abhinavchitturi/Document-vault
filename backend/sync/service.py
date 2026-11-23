"""Several computers that keep one vault: pairing another one, and catching up with each other in the background.

Pairing (the same short code and security-code check as connecting a laptop):
  1. On a computer that keeps the vault: Settings -> "Add a computer that also keeps this vault" shows a code.
  2. On the new computer (its vault still empty): "Join an existing vault", its address and the code. The new computer
     checks the first one's certificate (the person compares the security codes), then sends the code, its own
     address and certificate, and a key the first computer will use to reach it. The first computer answers with a
     key for the other direction and the vault's encryption key (inside that encrypted, certificate-checked
     connection only), and the new computer copies the whole vault.
From then on each one asks the other every few seconds what changed and copies what is newer (stamps.py). With
three or more, each catches up with the one it was paired with, and changes travel through it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import socket
import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from sqlalchemy import delete, func, insert, select

from backend.database.schema import documents, sync_peers, sync_stamps, users
from backend.sync import records, stamps, transport

logger = logging.getLogger(__name__)

EVERY = 10  # seconds between catch-ups
CODE_SECONDS = 600
TYPED_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def computer_name() -> str:
    name = socket.gethostname().split(".")[0]
    return name[:100] or "Computer"


class SyncError(Exception):
    """Said to the person: why pairing or catching up didn't work."""


class Sync:
    def __init__(self, store, embed=None, addresses=None, fingerprint: str | None = None):
        """`addresses()`: this computer's addresses for the others (Wi-Fi, Tailscale); `fingerprint`: its
        certificate's SHA-256. `embed(chunks)`: search vectors, for a document whose vectors can't be used here."""
        self.store, self.engine, self.embed = store, store.engine, embed
        self.addresses, self.fingerprint = addresses or (lambda: []), fingerprint
        self._codes: dict[str, float] = {}
        self._lock = threading.Lock()
        self._catching_up = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.joining: dict | None = None  # {"state", "done", "total", "error"} while copying the vault
        with self.engine.begin() as conn:
            self.centre_id = stamps.centre_id(conn)

    # ---------- the computer that already keeps the vault ----------

    def invite(self) -> dict:
        """A one-time code for another computer to join this vault (10 minutes)."""
        if not self.fingerprint:
            raise SyncError("Other devices can't reach this computer: start the vault with device access on.")
        letters = "".join(secrets.choice(TYPED_ALPHABET) for _ in range(8))
        with self._lock:
            now = time.monotonic()
            self._codes = {c: t for c, t in self._codes.items() if t > now}
            self._codes[letters] = now + CODE_SECONDS
        return {"code": f"{letters[:4]}-{letters[4:]}", "expires_in": CODE_SECONDS, "urls": self.addresses(),
                "fingerprint": self.fingerprint, "name": computer_name()}

    def accept(self, request: dict) -> dict:
        """Another computer joins with a code: it becomes a peer, and gets what it needs to copy the vault."""
        typed = "".join(str(request.get("code") or "").split()).replace("-", "").upper()
        with self._lock:
            expires = self._codes.pop(typed, None)
        if expires is None or expires < time.monotonic():
            raise PermissionError("This code is wrong or expired. Show a new one on the computer that keeps the vault.")
        for field in ("centre_id", "name", "urls", "fingerprint", "token"):
            if not request.get(field):
                raise ValueError(f"missing {field}")
        mine = secrets.token_urlsafe(32)  # the joining computer uses it to reach this one
        from backend.security import keys

        key = keys.load(self.store._vault.vault_id)
        if key is None:
            raise SyncError("This computer doesn't have the vault's key to share.")
        with self.engine.begin() as conn:
            stamps.backfill(conn)  # everything already here goes to the new computer too
            conn.execute(delete(sync_peers).where(sync_peers.c.centre_id == request["centre_id"]))
            conn.execute(insert(sync_peers).values(
                centre_id=request["centre_id"], name=str(request["name"])[:100], urls=json.dumps(list(request["urls"])[:8]),
                fingerprint=request["fingerprint"], token_out=self.store.cipher.seal_text(request["token"]),
                token_in_hash=_hash(mine), added_at=datetime.now(timezone.utc)))
        logger.info("%s now also keeps this vault", request["name"])
        self._wake.set()
        return {"centre_id": self.centre_id, "name": computer_name(), "urls": self.addresses(),
                "fingerprint": self.fingerprint, "token": mine, "vault_id": self.store._vault.vault_id,
                "key": base64.b64encode(key).decode(), "model": self.store.model}

    def peer_for_token(self, token: str | None) -> dict | None:
        if not token or len(token) > 200:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(select(sync_peers).where(sync_peers.c.token_in_hash == _hash(token))).mappings().first()
        return dict(row) if row else None

    # ---------- the computer that joins ----------

    def can_join(self) -> bool:
        """Only a vault with no documents joins another: nothing of its own would be lost or mixed in. Someone may
        already have signed up on it: the other vault's people (and passwords) come over, a same name taking theirs."""
        with self.engine.connect() as conn:
            docs = conn.execute(select(func.count()).select_from(documents)).scalar()
            peers = conn.execute(select(func.count()).select_from(sync_peers)).scalar()
        return docs == 0 and peers == 0

    def join(self, address: str, code: str, fingerprint: str) -> dict:
        """Join the vault kept on the computer at `address` (whose certificate the person checked: `fingerprint`)."""
        if not self.can_join():
            raise SyncError("Only a vault with no documents can join another one (this one has documents, or already "
                            "keeps another vault).")
        if not self.fingerprint:
            raise SyncError("Start the vault with device access on, so the other computer can reach this one.")
        url = address if "://" in address else f"https://{address.strip()}"
        if url.count(":") < 2:
            url += ":8443"
        mine = secrets.token_urlsafe(32)  # the other computer uses it to reach this one
        try:
            answer = transport.call_json(url, fingerprint, "POST", "/api/sync/join", body={
                "code": code, "centre_id": self.centre_id, "name": computer_name(), "urls": self.addresses(),
                "fingerprint": self.fingerprint, "token": mine})
        except transport.Unreachable as e:
            raise SyncError(f"Can't reach the vault at {address}. Is that computer on, with Document Vault open, and on the "
                            f"same Wi-Fi or Tailscale? ({e})") from e
        except PermissionError as e:
            raise SyncError(str(e)) from e
        self.store.adopt_vault(answer["vault_id"], base64.b64decode(answer["key"]))
        with self.engine.begin() as conn:
            # the vault joined wins: what this computer did before (its own sign-up, settings) isn't news to send back,
            # and the other vault's people (a same name with their password) replace this one's
            conn.execute(delete(sync_stamps))
            conn.execute(insert(sync_peers).values(
                centre_id=answer["centre_id"], name=str(answer["name"])[:100],
                urls=json.dumps([url, *[u for u in answer.get("urls", []) if u != url]][:8]),
                fingerprint=answer["fingerprint"], token_out=self.store.cipher.seal_text(answer["token"]),
                token_in_hash=_hash(mine), added_at=datetime.now(timezone.utc)))
        logger.info("Joined the vault kept on %s; copying it", answer["name"])
        self.joining = {"state": "copying", "done": 0, "total": 0, "error": None, "with": answer["name"]}
        threading.Thread(target=self._first_copy, name="sync-join", daemon=True).start()
        return {"with": answer["name"]}

    def _first_copy(self) -> None:
        try:
            for peer in self.peers(raw=True):
                self._catch_up(peer, progress=self.joining)
            self.joining.update(state="done")
        except Exception as e:  # noqa: BLE001 (shown in the app; it carries on in the background)
            logger.warning("Copying the vault stopped: %s", e)
            self.joining.update(state="failed", error=f"{e} It carries on by itself in the background.")

    # ---------- catching up ----------

    def peers(self, raw: bool = False) -> list[dict]:
        with self.engine.connect() as conn:
            rows = [dict(r) for r in conn.execute(select(sync_peers).order_by(sync_peers.c.added_at)).mappings()]
        if raw:
            return rows
        iso = lambda d: (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat(timespec="seconds") if d else None  # noqa: E731
        return [{"centre_id": r["centre_id"], "name": r["name"], "urls": json.loads(r["urls"]), "fingerprint": r["fingerprint"],
                 "synced_at": iso(r["synced_at"]), "problem": r["problem"]} for r in rows]

    def start(self) -> None:
        threading.Thread(target=self._loop, name="sync", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def nudge(self) -> None:
        """Something changed here: the others hear about it when they next ask; catch up with them soon too."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            for peer in self.peers(raw=True):
                try:
                    self._catch_up(peer)
                except Exception as e:  # noqa: BLE001 (kept for Settings; tried again next round)
                    logger.info("Couldn't catch up with %s: %s", peer["name"], e)
            self._wake.wait(EVERY)
            self._wake.clear()

    def _catch_up(self, peer: dict, progress: dict | None = None) -> int:
        """Copy whatever the peer has that is newer than here. Returns how many things changed here."""
        with self._catching_up:
            token = self.store.cipher.open_text(peer["token_out"])
            last_error = None
            for url in json.loads(peer["urls"]):
                try:
                    changed = self._pull(url, peer["fingerprint"], token, progress)
                except (transport.Unreachable, RuntimeError, PermissionError) as e:
                    last_error = str(e)
                    continue
                with self.engine.begin() as conn:
                    conn.execute(sync_peers.update().where(sync_peers.c.centre_id == peer["centre_id"])
                                 .values(synced_at=datetime.now(timezone.utc), problem=None))
                return changed
            with self.engine.begin() as conn:
                conn.execute(sync_peers.update().where(sync_peers.c.centre_id == peer["centre_id"])
                             .values(problem=f"Not reached: {last_error}"))
            raise transport.Unreachable(last_error or "no address")

    def _pull(self, url: str, fingerprint: str, token: str, progress: dict | None) -> int:
        theirs = transport.call_json(url, fingerprint, "GET", "/api/sync/peer/manifest", token)["stamps"]
        with self.engine.connect() as conn:
            ours = {(s["kind"], s["key"]): s for s in stamps.manifest(conn)}
        wanted = [s for s in theirs if s["kind"] in stamps.KINDS and stamps.newer(s, ours.get((s["kind"], s["key"])))]
        order = {"user": 0, "meta": 1, "device": 2, "doc": 3}  # people before their documents and devices
        wanted.sort(key=lambda s: order[s["kind"]])
        if progress is not None:
            progress.update(total=sum(1 for s in wanted if s["kind"] == "doc"), done=0)
        for s in wanted:
            self._take(url, fingerprint, token, s)
            if progress is not None and s["kind"] == "doc":
                progress["done"] += 1
        if wanted:
            logger.info("Caught up: %d change(s) from %s", len(wanted), url)
        return len(wanted)

    def _take(self, url: str, fingerprint: str, token: str, s: dict) -> None:
        kind, key = s["kind"], s["key"]
        if s["gone"]:
            if kind == "doc":
                records.remove_doc(self.store, key, s)
            elif kind == "device":
                records.remove_device(self.engine, key, s)
            return
        record = transport.call_json(url, fingerprint, "GET", f"/api/sync/peer/item/{kind}/{quote(key, safe='')}", token)
        if record is None:
            return  # gone meanwhile: its deletion comes next round
        if kind == "doc":
            files = {}
            for part in record.get("parts", []):
                status, data, _ = transport.call(url, fingerprint, "GET", f"/api/sync/peer/file/{quote(key, safe='')}/{part}", token)
                if status == 200:
                    files[part] = data
            records.apply_doc(self.store, record, files, s, embed=self.embed)
        elif kind == "user":
            records.apply_user(self.engine, record, s)
        elif kind == "device":
            records.apply_device(self.engine, record, s)
        elif kind == "meta":
            records.apply_meta(self.store, record, s)

    # ---------- answering the others ----------

    def manifest(self) -> dict:
        with self.engine.connect() as conn:
            return {"centre_id": self.centre_id, "stamps": stamps.manifest(conn)}

    def item(self, kind: str, key: str) -> dict | None:
        if kind == "doc":
            record = records.export_doc(self.store, key)
            if record is not None:
                record["parts"] = sorted(records.doc_files(self.store, record["stored_as"]))
            return record
        if kind == "user":
            return records.export_user(self.engine, key)
        if kind == "device":
            return records.export_device(self.engine, key)
        if kind == "meta":
            return records.export_meta(self.engine, key)
        return None

    def file(self, doc_id: str, part: str) -> bytes | None:
        with self.engine.connect() as conn:
            stored_as = conn.execute(select(documents.c.file_path).where(documents.c.id == doc_id)).scalar()
        if stored_as is None or part not in ("file", "ocr"):
            return None
        return records.doc_files(self.store, stored_as).get(part)

    def status(self) -> dict:
        return {"centre_id": self.centre_id, "name": computer_name(), "peers": self.peers(), "joining": self.joining,
                "can_join": self.can_join()}
