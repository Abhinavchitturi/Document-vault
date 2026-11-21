"""Run the vault on the Mac or on Windows (Phase 13): start, stop, status, logs, update, start at login.

    ./vault start          checks PostgreSQL / Ollama / Tailscale, starts the vault in the background
    ./vault stop
    ./vault status         is it running, can phones reach it, is the assistant ready
    ./vault logs           the last lines of the log (add -f to follow)
    ./vault update         git pull, install what changed, rebuild the pages, restart; says if phones need a new app
    ./vault autostart on   start the vault whenever you log in to the Mac (off: only when you start it)

Or double-click "Start Vault.command" / "Stop Vault.command" in Finder.

On Windows the command is `vault` (vault.cmd) and the vault runs as a hidden background process of yours
(its process id is kept in data/run/vault.pid, its log in data/logs/vault.log); "Start Vault.bat" /
"Stop Vault.bat" are the double-click versions, and `vault autostart on` starts it at sign-in through the
Startup folder. There is no launchd there, so a crashed vault is not restarted by itself.

The vault runs under launchd, the Mac's own service manager, as a LaunchAgent labelled
com.documentvault.server: it keeps running after Terminal is closed, is restarted if it crashes,
and writes to data/logs/vault.log. The Mac is kept awake while it runs (phones may need it).
It still runs as you, so the Keychain (the vault key, Phase 11) opens as before.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LABEL = "com.documentvault.server"
PORT = 8000
DEFAULT_SQLITE = "sqlite:///data/index/vault.db"
MAX_LOG = 10 * 1024 * 1024
BREW_PATHS = ("/opt/homebrew/bin", "/usr/local/bin")  # tesseract, tailscale, pg_isready...


def _run(cmd: list[str], timeout: float = 30) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return subprocess.CompletedProcess(cmd, 127, "", str(e))


def _http_ok(url: str, timeout: float = 2) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (local addresses only)
            return r.status < 500
    except Exception:  # noqa: BLE001
        return False


@dataclass
class Check:
    name: str
    ok: bool
    message: str
    required: bool = False


class Vault:
    """Everything the commands do, with the outside world (commands, HTTP, time) injectable for tests."""

    def __init__(self, root: Path = ROOT, python: str = sys.executable, run=_run, http_ok=_http_ok,
                 sleep=time.sleep, home: Path | None = None, uid: int | None = None, say=print, tailscale=None):
        self.root, self.python, self.run, self.http_ok, self.sleep, self.say = root, python, run, http_ok, sleep, say
        if tailscale is None:  # the same lookup the vault uses for the phones' QR code (App Store Tailscale too)
            from backend.api.lan import tailscale_address as tailscale
        self.tailscale = tailscale
        home = home or Path.home()
        self.login_agent = home / "Library" / "LaunchAgents" / f"{LABEL}.plist"  # loaded at every login
        self.local_agent = root / "data" / "launchd" / f"{LABEL}.plist"  # loaded only by ./vault start
        self.log = root / "data" / "logs" / "vault.log"
        self.domain = f"gui/{uid if uid is not None else getattr(os, 'getuid', lambda: 0)()}"
        self.cmd = "./vault"  # how this command is typed, for the messages
        self.npm = "npm"

    # ---------- the LaunchAgent ----------

    def plist(self, at_login: bool) -> dict:
        path = ":".join(dict.fromkeys([*BREW_PATHS, str(Path(self.python).parent), "/usr/bin", "/bin",
                                        "/usr/sbin", "/sbin"]))
        env = {"PATH": path, "PYTHONUNBUFFERED": "1"}
        for key in ("VAULT_DATABASE_URL", "VAULT_OCR_LANGS", "VAULT_FACTS_READER"):
            if os.environ.get(key):
                env[key] = os.environ[key]
        return {
            "Label": LABEL,
            "ProgramArguments": [self.python, "-m", "backend.api.app", "--phones"],
            "WorkingDirectory": str(self.root),
            "EnvironmentVariables": env,
            "StandardOutPath": str(self.log),
            "StandardErrorPath": str(self.log),
            "RunAtLoad": True,
            "KeepAlive": {"SuccessfulExit": False},  # restarted after a crash, not after "stop"
            "ThrottleInterval": 15,
            "ProcessType": "Interactive",
        }

    def autostart_enabled(self) -> bool:
        return self.login_agent.exists()

    def _agent_path(self) -> Path:
        return self.login_agent if self.autostart_enabled() else self.local_agent

    def _write_agent(self, at_login: bool) -> Path:
        path = self.login_agent if at_login else self.local_agent
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(self.plist(at_login)))
        return path

    def loaded(self) -> tuple[bool, int | None]:
        """(launchd knows the vault, its process id if it is running)."""
        r = self.run(["launchctl", "print", f"{self.domain}/{LABEL}"])
        if r.returncode != 0:
            return False, None
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("pid = "):
                return True, int(line.split("=")[1])
        return True, None

    def _bootout(self) -> None:
        if not self.loaded()[0]:
            return
        self.run(["launchctl", "bootout", f"{self.domain}/{LABEL}"])
        for _ in range(20):  # launchd unloads in the background; bootstrap fails until it's gone
            if not self.loaded()[0]:
                return
            self.sleep(0.5)

    def _launch(self) -> tuple[bool, str]:
        """Start the vault in the background: (started, why not)."""
        path = self._write_agent(at_login=self.autostart_enabled())
        r = self.run(["launchctl", "bootstrap", self.domain, str(path)])
        if r.returncode != 0:
            return False, f"launchd refused to start the vault: {(r.stderr or r.stdout).strip()}"
        return True, ""

    def open_url(self, url: str) -> None:
        self.run(["open", url])

    def _open_ollama(self) -> None:
        self.run(["open", "-a", "Ollama"])

    def _tesseract_ok(self) -> bool:
        return self.run(["tesseract", "--version"]).returncode == 0

    def _tesseract_hint(self) -> str:
        return "missing: brew install tesseract tesseract-lang (photos and scans can't be read)"

    # ---------- what the vault needs ----------

    def database_check(self, fix: bool) -> Check:
        pg = self.run(["pg_isready", "-q"])
        if pg.returncode != 0 and fix and not os.environ.get("VAULT_DATABASE_URL", "").startswith("sqlite"):
            self.say("Starting PostgreSQL…")
            self.run(["brew", "services", "start", "postgresql@16"], timeout=60)
            for _ in range(20):
                self.sleep(1)
                if self.run(["pg_isready", "-q"]).returncode == 0:
                    pg = subprocess.CompletedProcess([], 0)
                    break
        return Check("Database (PostgreSQL)", pg.returncode == 0,
                     "running" if pg.returncode == 0 else "not running: brew services start postgresql@16",
                     required=not os.environ.get("VAULT_DATABASE_URL", "").startswith("sqlite"))

    def checks(self, fix: bool = True) -> list[Check]:
        out = [self.database_check(fix)]

        ollama = self.http_ok("http://localhost:11434/api/tags")
        if not ollama and fix:
            self.say("Opening Ollama…")
            self._open_ollama()
            for _ in range(20):
                self.sleep(1)
                if self.http_ok("http://localhost:11434/api/tags"):
                    ollama = True
                    break
        out.append(Check("Answers (Ollama)", ollama, "running" if ollama else
                         "not running: open the Ollama app (search and documents still work without it)"))

        address = self.tailscale() or ""
        out.append(Check("Private tunnel (Tailscale)", bool(address), f"connected ({address})" if address else
                         "not connected: open Tailscale and log in, or phones only work on home Wi-Fi"))

        built = (self.root / "frontend" / "out" / "index.html").exists()
        out.append(Check("Web pages", built, "built" if built else
                         "not built: cd frontend && npm install && npm run build", required=True))
        tess = self._tesseract_ok()
        out.append(Check("Reading scans (Tesseract)", tess, "installed" if tess else self._tesseract_hint()))
        return out

    def _show(self, checks: list[Check]) -> None:
        for c in checks:
            self.say(f"  {'✓' if c.ok else ('✗' if c.required else '!')} {c.name}: {c.message}")

    def _rotate_log(self) -> None:
        self.log.parent.mkdir(parents=True, exist_ok=True)
        if self.log.exists() and self.log.stat().st_size > MAX_LOG:
            self.log.replace(self.log.with_name("vault.log.1"))

    # ---------- commands ----------

    def start(self, wait: float = 120) -> bool:
        self.say("Checking what the vault needs…")
        checks = self.checks(fix=True)
        self._show(checks)
        missing = [c for c in checks if c.required and not c.ok]
        if missing:
            self.say("Can't start until the ✗ items above are fixed.")
            return False
        self._rotate_log()
        self._bootout()  # a running or crashed copy: start fresh
        started, why = self._launch()
        if not started:
            self.say(why)
            return False
        self.say("Starting the vault (the first start after an update can take a minute)…")
        waited = 0.0
        while waited < wait:
            if self.http_ok(f"http://localhost:{PORT}/api/auth/status"):
                self.say(f"The vault is running: http://localhost:{PORT}")
                self.say(f"Phones can connect while this computer is on and awake. Stop it with: {self.cmd} stop")
                return True
            self.sleep(2)
            waited += 2
            if waited >= 10 and not self.loaded()[1]:
                break  # it exited: no point waiting
        self.say("The vault didn't come up. The end of its log:")
        self.say(self.tail(25))
        return False

    def stop(self) -> None:
        running = self.loaded()[0]
        self._bootout()
        self.say("The vault is stopped. Phones will say the Mac is unreachable until you start it again."
                 if running else "The vault wasn't running.")

    def status(self) -> bool:
        loaded, pid = self.loaded()
        up = self.http_ok(f"http://localhost:{PORT}/api/auth/status")
        if up:
            self.say(f"Vault: running (process {pid}) at http://localhost:{PORT}" if pid else
                     f"Vault: running at http://localhost:{PORT} (started by hand, not with {self.cmd} start)")
        elif loaded:
            self.say(f"Vault: starting, or restarting after a problem. See: {self.cmd} logs")
        else:
            self.say(f"Vault: stopped. Start it with: {self.cmd} start")
        self.say(f"Start at login: {'on' if self.autostart_enabled() else 'off'}")
        self._show(self.checks(fix=False))
        return up

    def tail(self, lines: int = 40) -> str:
        if not self.log.exists():
            return "(no log yet)"
        with open(self.log, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 64 * 1024))
            return "\n".join(f.read().decode("utf-8", "replace").splitlines()[-lines:])

    def follow_log(self, lines: int) -> None:
        os.execvp("tail", ["tail", "-n", str(lines), "-f", str(self.log)])

    def autostart(self, on: bool) -> None:
        loaded = self.loaded()[0]
        if on:
            self.local_agent.unlink(missing_ok=True)
            self._write_agent(at_login=True)
            self.say("The vault will start by itself when you log in to this computer.")
        else:
            self.login_agent.unlink(missing_ok=True)
            self.say(f"The vault will only run when you start it ({self.cmd} start).")
        if loaded:
            self.say("It's running now; the change applies from the next start.")

    def update(self) -> bool:
        before = self.run(["git", "-C", str(self.root), "rev-parse", "HEAD"]).stdout.strip()
        pull = self.run(["git", "-C", str(self.root), "pull", "--ff-only"], timeout=120)
        if pull.returncode != 0:
            self.say("git pull failed:\n" + (pull.stderr or pull.stdout).strip())
            self.say("If it mentions local changes: git stash, then ./vault update again.")
            return False
        after = self.run(["git", "-C", str(self.root), "rev-parse", "HEAD"]).stdout.strip()
        if before == after:
            self.say("Already up to date.")
            return True
        changed = self.run(["git", "-C", str(self.root), "diff", "--name-only", before, after]).stdout.split()
        plan = update_plan(changed)
        if plan["requirements"]:
            self.say("Installing new Python packages…")
            r = self.run([self.python, "-m", "pip", "install", "-q", "-r", str(self.root / "requirements.txt")], timeout=900)
            if r.returncode != 0:
                self.say("pip failed:\n" + r.stderr.strip()[-2000:])
                return False
        if plan["frontend"]:
            self.say("Rebuilding the web pages…")
            front = str(self.root / "frontend")
            npm = self.npm
            for cmd in ([npm, "--prefix", front, "install", "--silent"], [npm, "--prefix", front, "run", "build"]):
                r = self.run(cmd, timeout=900)
                if r.returncode != 0:
                    self.say(f"{' '.join(cmd[:1] + cmd[3:])} failed:\n" + (r.stderr or r.stdout).strip()[-2000:])
                    return False
        self.say(f"Updated ({len(changed)} files changed).")
        if plan["phone_app"]:
            self.say("The phone app changed: build a new APK (cd mobile && flutter build apk --release) "
                     "and install it over the old one on each phone.")
        else:
            self.say("The phone app didn't change: phones need nothing.")
        if self.loaded()[0] or self.http_ok(f"http://localhost:{PORT}/api/auth/status"):
            self.say("Restarting the vault…")
            return self.start()
        return True


def _spawn_detached(cmd: list[str], cwd: Path, env: dict, log: Path) -> int:
    """Start a program that outlives this window, with no console of its own; returns its process id."""
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (Windows only)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as out:
        proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                creationflags=flags, close_fds=True)
    return proc.pid


class WindowsVault(Vault):
    """The same commands on Windows: no launchd, so the vault is a hidden background process of yours.
    Its process id is kept in data/run/vault.pid; `autostart` puts a small script in the Startup folder."""

    def __init__(self, *args, spawn=None, appdata: Path | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.cmd = "vault"
        self.npm = "npm.cmd"  # npm.ps1 is blocked by PowerShell's default policy
        self.spawn = spawn or _spawn_detached
        home = kwargs.get("home") or Path.home()
        appdata = appdata or Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        self.startup_script = (appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
                               / "Document Vault.vbs")
        self.pidfile = self.root / "data" / "run" / "vault.pid"

    # ---------- the background process ----------

    def _pid(self) -> int | None:
        try:
            return int(self.pidfile.read_text().strip())
        except (OSError, ValueError):
            return None

    def _alive(self, pid: int) -> bool:
        r = self.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
        return r.returncode == 0 and f'"{pid}"' in r.stdout and "python" in r.stdout.lower()  # not a reused id

    def loaded(self) -> tuple[bool, int | None]:
        pid = self._pid()
        if pid and self._alive(pid):
            return True, pid
        return False, None

    def _bootout(self) -> None:
        pid = self._pid()
        if pid and self._alive(pid):
            self.run(["taskkill", "/PID", str(pid), "/T", "/F"])  # /T: the whole tree (a venv's python.exe has a child)
            for _ in range(20):
                if not self._alive(pid):
                    break
                self.sleep(0.5)
        self.pidfile.unlink(missing_ok=True)

    def _env(self) -> dict:
        env = dict(os.environ)
        env.setdefault("VAULT_DATABASE_URL", DEFAULT_SQLITE)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONUTF8"] = "1"  # not the console's old code page: logs and files hold any language
        env["PATH"] = os.pathsep.join([str(Path(self.python).parent), env.get("PATH", "")])
        return env

    def _launch(self) -> tuple[bool, str]:
        try:
            pid = self.spawn([self.python, "-m", "backend.api.app", "--phones"], cwd=self.root, env=self._env(),
                             log=self.log)
        except OSError as e:
            return False, f"Windows could not start the vault: {e}"
        self.pidfile.parent.mkdir(parents=True, exist_ok=True)
        self.pidfile.write_text(str(pid))
        return True, ""

    # ---------- what the vault needs ----------

    def database_check(self, fix: bool) -> Check:
        url = os.environ.get("VAULT_DATABASE_URL", DEFAULT_SQLITE)
        if url.startswith("sqlite"):
            return Check("Database", True, "SQLite file in data/index")
        ready = self.run(["pg_isready", "-q"]).returncode == 0
        return Check("Database (PostgreSQL)", ready, "running" if ready else "not running: start the PostgreSQL service",
                     required=True)

    def _open_ollama(self) -> None:
        app = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama app.exe"
        env = dict(os.environ)
        try:
            if app.exists():
                self.spawn([str(app)], cwd=self.root, env=env, log=self.log)
            elif shutil.which("ollama"):
                self.spawn(["ollama", "serve"], cwd=self.root, env=env, log=self.log)
        except OSError:
            pass  # the check that follows reports that Ollama isn't running

    def _tesseract_ok(self) -> bool:
        from backend.parser.ocr import tesseract_path

        return tesseract_path() is not None

    def _tesseract_hint(self) -> str:
        return "missing: winget install UB-Mannheim.TesseractOCR (photos and scans can't be read)"

    def open_url(self, url: str) -> None:
        webbrowser.open(url)

    # ---------- start at sign-in ----------

    def autostart_enabled(self) -> bool:
        return self.startup_script.exists()

    def autostart(self, on: bool) -> None:
        running = self.loaded()[0]
        if on:
            self.startup_script.parent.mkdir(parents=True, exist_ok=True)
            launcher = str(self.root / "vault.cmd")
            # one line of VBScript: run `vault.cmd start` with no window (quotes inside a string are doubled)
            self.startup_script.write_text(
                f'CreateObject("WScript.Shell").Run """{launcher}"" start", 0, False\r\n', encoding="utf-16")
            self.say("The vault will start by itself when you sign in to Windows.")
        else:
            self.startup_script.unlink(missing_ok=True)
            self.say("The vault will only run when you start it (vault start, or Start Vault.bat).")
        if running:
            self.say("It's running now; the change applies from the next start.")

    def follow_log(self, lines: int) -> None:
        print(self.tail(lines))
        try:
            with open(self.log, "rb") as f:
                f.seek(0, 2)
                while True:
                    chunk = f.read()
                    if chunk:
                        print(chunk.decode("utf-8", "replace"), end="", flush=True)
                    else:
                        time.sleep(0.5)
        except KeyboardInterrupt:
            pass


def update_plan(changed: list[str]) -> dict:
    """What an update has to do, from the files it changed."""
    return {
        "requirements": any(p == "requirements.txt" for p in changed),
        "frontend": any(p.startswith("frontend/") for p in changed),
        "phone_app": any(p.startswith("mobile/lib/") or p in ("mobile/pubspec.yaml", "mobile/pubspec.lock")
                         or p.startswith("mobile/android/") for p in changed),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="./vault", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("start", help="start the vault in the background")
    sub.add_parser("stop", help="stop the vault")
    sub.add_parser("restart", help="stop and start")
    sub.add_parser("status", help="is it running, and is everything it needs ready")
    logs = sub.add_parser("logs", help="the end of the log")
    logs.add_argument("-f", "--follow", action="store_true", help="keep showing new lines (Ctrl+C to stop)")
    logs.add_argument("-n", "--lines", type=int, default=60)
    sub.add_parser("update", help="get the latest version and restart")
    auto = sub.add_parser("autostart", help="start the vault when you log in")
    auto.add_argument("state", choices=["on", "off"])
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):  # a Windows console can't show every character (✓, ✗): don't crash on one
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    if sys.platform not in ("darwin", "win32") and args.command in ("start", "stop", "restart", "autostart"):
        raise SystemExit("./vault manages the vault with macOS's launchd or Windows. Elsewhere run: "
                         "python -m backend.api.app --phones")
    vault = WindowsVault() if sys.platform == "win32" else Vault()
    if args.command in ("start", "restart"):
        ok = vault.start()
        if ok and sys.stdout.isatty():
            vault.open_url(f"http://localhost:{PORT}")
        raise SystemExit(0 if ok else 1)
    if args.command == "stop":
        vault.stop()
    elif args.command == "status":
        raise SystemExit(0 if vault.status() else 3)
    elif args.command == "logs":
        if args.follow and vault.log.exists():
            vault.follow_log(args.lines)
        else:
            print(vault.tail(args.lines))
    elif args.command == "update":
        raise SystemExit(0 if vault.update() else 1)
    elif args.command == "autostart":
        vault.autostart(args.state == "on")


if __name__ == "__main__":
    main()
