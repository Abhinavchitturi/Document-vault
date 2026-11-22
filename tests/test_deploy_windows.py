"""The same ./vault commands on Windows, against a pretend Windows (no launchd; a hidden background process)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from backend.deploy.vault import WindowsVault


class FakeWindows:
    """Answers tasklist/taskkill and 'starts' the vault by remembering its process id."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.alive: set[int] = set()
        self.web_up = False
        self.spawned: list[list[str]] = []
        self.env: dict = {}
        self.next_pid = 5100

    def run(self, cmd, timeout=30):
        self.calls.append(cmd)
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, out, "")  # noqa: E731
        if cmd[0] == "tasklist":
            pid = int(cmd[2].split()[-1])
            return ok(f'"python.exe","{pid}","Console","1","90,000 K"\r\n' if pid in self.alive
                      else "INFO: No tasks are running which match the specified criteria.\r\n")
        if cmd[0] == "taskkill":
            self.alive.discard(int(cmd[2]))
            self.web_up = False
            return ok()
        return ok()

    def spawn(self, cmd, cwd, env, log):
        self.spawned.append(cmd)
        self.env = env
        if cmd[1:3] == ["-m", "backend.api.app"]:
            pid = self.next_pid
            self.next_pid += 1
            self.alive.add(pid)
            self.web_up = True
            return pid
        return 1

    def http_ok(self, url, timeout=2):
        return self.web_up if ":8000" in url else True  # other local services answer


def windows_vault(tmp_path, win, built=True):
    root = tmp_path / "repo"
    if built:
        (root / "frontend" / "out").mkdir(parents=True)
        (root / "frontend" / "out" / "index.html").write_text("<html>")
    said = []
    v = WindowsVault(root=root, python=r"C:\vault\venv\Scripts\python.exe", run=win.run, http_ok=win.http_ok,
                     sleep=lambda s: None, home=tmp_path / "home", say=said.append, tailscale=lambda: None,
                     spawn=win.spawn, appdata=tmp_path / "appdata")
    return v, said


def test_start_runs_the_vault_in_the_background_and_remembers_its_process(tmp_path, monkeypatch):
    monkeypatch.delenv("VAULT_DATABASE_URL", raising=False)
    win = FakeWindows()
    v, said = windows_vault(tmp_path, win)
    assert v.start()
    assert win.spawned[-1] == [r"C:\vault\venv\Scripts\python.exe", "-m", "backend.api.app", "--phones"]
    assert v.pidfile.read_text() == "5100" and v.loaded() == (True, 5100)
    assert win.env["VAULT_DATABASE_URL"] == "sqlite:///data/index/vault.db"  # no PostgreSQL needed
    assert any("running: http://localhost:8000" in s for s in said) and any("vault stop" in s for s in said)
    assert not any("brew" in " ".join(c) or "launchctl" in " ".join(c) for c in win.calls)


def test_stop_ends_the_whole_process_tree_and_forgets_it(tmp_path):
    win = FakeWindows()
    v, said = windows_vault(tmp_path, win)
    v.start()
    v.stop()
    assert ["taskkill", "/PID", "5100", "/T", "/F"] in win.calls
    assert v.loaded() == (False, None) and not v.pidfile.exists() and "stopped" in said[-1]
    v.stop()
    assert said[-1] == "The vault wasn't running."


def test_starting_again_replaces_the_running_copy(tmp_path):
    win = FakeWindows()
    v, _ = windows_vault(tmp_path, win)
    v.start()
    v.start()
    assert ["taskkill", "/PID", "5100", "/T", "/F"] in win.calls and v.loaded() == (True, 5101)


def test_a_stale_process_id_is_not_mistaken_for_the_vault(tmp_path):
    win = FakeWindows()
    v, _ = windows_vault(tmp_path, win)
    v.pidfile.parent.mkdir(parents=True)
    v.pidfile.write_text("777")  # left over from a reboot; nothing runs under that id now
    assert v.loaded() == (False, None)
    v.pidfile.write_text("not a number")
    assert v.loaded() == (False, None)


def test_start_refuses_without_the_web_pages(tmp_path):
    win = FakeWindows()
    v, said = windows_vault(tmp_path, win, built=False)
    assert not v.start() and not win.spawned
    assert any("npm run build" in s for s in said)


def test_status_reports_a_vault_that_is_up(tmp_path):
    win = FakeWindows()
    v, said = windows_vault(tmp_path, win)
    assert not v.status() and any("stopped" in s and "vault start" in s for s in said)
    v.start()
    said.clear()
    assert v.status() and "running (process 5100)" in said[0]


def test_autostart_writes_a_hidden_startup_script_and_removes_it(tmp_path):
    win = FakeWindows()
    v, said = windows_vault(tmp_path, win)
    assert not v.autostart_enabled()
    v.autostart(True)
    script = v.startup_script.read_text(encoding="utf-16")
    assert v.autostart_enabled() and v.startup_script.name == "Document Vault.vbs"
    assert 'Run """' in script and f'{v.root / "vault.cmd"}"" start", 0, False' in script  # hidden window
    assert Path(v.startup_script).parent.name == "Startup"
    v.autostart(False)
    assert not v.autostart_enabled()


def test_windows_uses_the_cmd_version_of_npm(tmp_path):
    v, _ = windows_vault(tmp_path, FakeWindows())
    assert v.npm == "npm.cmd" and v.cmd == "vault"
