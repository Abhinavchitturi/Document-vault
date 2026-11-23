"""Two real vaults on one computer, kept as one: joining with a code, copying, two-way changes, deletions, catching up
after one was off, and signing in to the copy with the same password. Slow (two engines, the search model), so it runs
as a script (and from tests/test_sync.py when VAULT_SYNC_E2E=1):

    python tests/sync_two_vaults.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PASSWORD = "a long enough password 1"


class Engine:
    """One vault's engine, talked to over its pipe like the apps do."""

    def __init__(self, folder: Path, port: int):
        self.folder, self.port, self.ids, self.cookie = folder, port, iter(range(1, 10**6)), None
        folder.mkdir(parents=True, exist_ok=True)
        self.start()

    def start(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(REPO), "VAULT_KEYCHAIN": "off", "VAULT_KEY_DIR": str(self.folder / "keys"),
               "VAULT_LLM_DIR": str(self.folder / "llm"), "VAULT_FACTS_READER": "rules", "VAULT_AI_AUTO_DOWNLOAD": "0",
               "VAULT_DATABASE_URL": "sqlite:///data/index/vault.db", "PYTHONUTF8": "1"}
        self.process = subprocess.Popen(
            [sys.executable, "-m", "backend.engine", "--no-llm", "--phone-port", str(self.port), "--phone-address", "127.0.0.1"],
            cwd=self.folder, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=open(self.folder / "engine.log", "a"), text=True, encoding="utf-8")
        for line in self.process.stdout:
            if '"ready"' in line:
                return
            if '"failed"' in line:
                raise SystemExit(f"engine on {self.port} failed: {line}")

    def stop(self) -> None:
        self.process.stdin.close()
        self.process.wait(timeout=60)

    def ask(self, method: str, path: str, **body):
        number = next(self.ids)
        headers = {"cookie": self.cookie} if self.cookie else {}
        self.process.stdin.write(json.dumps({"id": number, "method": method, "path": path, "headers": headers, **body}) + "\n")
        self.process.stdin.flush()
        for line in self.process.stdout:
            message = json.loads(line)
            if message.get("id") == number:
                if "set-cookie" in message.get("headers", {}):
                    self.cookie = message["headers"]["set-cookie"].split(";")[0]
                return message.get("status"), message.get("json")
        raise SystemExit("engine stopped")

    def documents(self) -> dict[str, dict]:
        status, docs = self.ask("GET", "/api/documents")
        assert status == 200, docs
        return {d["filename"]: d for d in docs}

    def add(self, name: str, text: str) -> None:
        path = self.folder / name
        path.write_text(text)
        status, result = self.ask("POST", "/api/documents", files=[{"path": str(path)}], form={"category": "auto"})
        assert status == 200 and result[0]["status"] == "added", result


def wait_for(what: str, check, seconds: float = 90) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            print(f"  ok: {what}", flush=True)
            return
        time.sleep(1)
    raise SystemExit(f"FAILED: {what} (waited {seconds} s)")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="vault-sync-") as tmp:
        a = Engine(Path(tmp) / "a", 8471)
        b = None
        try:
            print("A: the first computer keeps the vault", flush=True)
            assert a.ask("POST", "/api/auth/setup", json={"name": "abhinav", "password": PASSWORD})[0] == 200
            a.add("Bike Insurance 2026.txt", "Two-wheeler policy TW-778812. Period: 15 March 2026 to 14 March 2027.")
            status, invite = a.ask("POST", "/api/sync/invite")
            assert status == 200, invite

            print("B: a second computer joins with the code", flush=True)
            b = Engine(Path(tmp) / "b", 8472)
            # B was already set up (signed up, no documents yet): it joins from Settings, as an admin
            assert b.ask("POST", "/api/auth/setup", json={"name": "abhinav", "password": "B's own long password"})[0] == 200
            status, probe = b.ask("GET", "/api/sync/probe?address=127.0.0.1:8471")
            assert status == 200 and probe["fingerprint"] == invite["fingerprint"], probe  # the security code matches
            status, joined = b.ask("POST", "/api/sync/join-vault",
                                   json={"address": "127.0.0.1:8471", "code": invite["code"].lower(), "fingerprint": probe["fingerprint"]})
            assert status == 200, joined
            wait_for("B copied the vault", lambda: (b.ask("GET", "/api/sync/joining")[1]["joining"] or {}).get("state") == "done")
            assert b.ask("GET", "/api/auth/status")[1]["setup_needed"] is False
            assert b.ask("POST", "/api/auth/login", json={"name": "abhinav", "password": PASSWORD})[0] == 200, "same password on B"
            assert "Bike Insurance 2026.txt" in b.documents()
            status, answer = b.ask("GET", "/api/search?q=two%20wheeler%20policy&k=3")
            assert status == 200 and answer["results"][0]["filename"] == "Bike Insurance 2026.txt", answer  # searchable on B

            print("Changes travel both ways", flush=True)
            b.add("Electricity Bill 2026 08.txt", "Electricity bill for August 2026. Amount due Rs 2,486. Due date 15 Sep 2026.")
            wait_for("a document added on B shows on A", lambda: "Electricity Bill 2026 08.txt" in a.documents(), 60)
            bike = a.documents()["Bike Insurance 2026.txt"]
            assert a.ask("PATCH", f"/api/documents/{bike['doc_id']}", json={"category": "warranty"})[0] == 200
            wait_for("a re-filed document on A is re-filed on B",
                     lambda: b.documents()["Bike Insurance 2026.txt"]["category"] == "warranty", 60)
            bill = b.documents()["Electricity Bill 2026 08.txt"]
            assert b.ask("DELETE", f"/api/documents/{bill['doc_id']}")[0] == 200
            wait_for("a document deleted on B is deleted on A", lambda: "Electricity Bill 2026 08.txt" not in a.documents(), 60)
            time.sleep(12)  # another round each way: the deletion stays a deletion
            assert "Electricity Bill 2026 08.txt" not in a.documents() and "Electricity Bill 2026 08.txt" not in b.documents()
            print("  ok: deleted stays deleted", flush=True)

            print("Catching up after B was off", flush=True)
            b.stop()
            a.add("Washing Machine Warranty.txt", "Warranty for the washing machine, serial WM-4471, valid until 2 Jan 2029.")
            b.start()
            b.cookie = None
            assert b.ask("POST", "/api/auth/login", json={"name": "abhinav", "password": PASSWORD})[0] == 200
            wait_for("B caught up after being off", lambda: "Washing Machine Warranty.txt" in b.documents(), 60)
            peers = b.ask("GET", "/api/sync/status")[1]["peers"]
            assert len(peers) == 1 and peers[0]["synced_at"] and not peers[0]["problem"], peers
            print("All good: two computers keep one vault.", flush=True)
        finally:
            for engine in (b, a):
                if engine is not None and engine.process.poll() is None:
                    engine.stop()
            for name in ("a", "b"):
                log = Path(tmp) / name / "engine.log"
                if log.exists() and os.environ.get("SHOW_LOGS"):
                    print(f"--- {name}/engine.log", log.read_text()[-3000:])


if __name__ == "__main__":
    main()
