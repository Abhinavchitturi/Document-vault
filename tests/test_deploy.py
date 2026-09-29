"""Phase 13: ./vault start / stop / status / update / autostart, against a pretend Mac."""

from __future__ import annotations

import plistlib
import subprocess

from backend.deploy.vault import LABEL, Vault, update_plan


class FakeMac:
    """Answers the commands ./vault runs; records them."""

    def __init__(self, postgres=True, tailscale="100.101.102.103", loaded=False):
        self.postgres, self.tailscale, self.is_loaded = postgres, tailscale, loaded
        self.calls: list[list[str]] = []
        self.web_up = False
        self.git_heads = ["aaa", "aaa"]
        self.changed = ""

    def run(self, cmd, timeout=30):
        self.calls.append(cmd)
        ok = lambda out="": subprocess.CompletedProcess(cmd, 0, out, "")  # noqa: E731
        fail = subprocess.CompletedProcess(cmd, 1, "", "no")
        match cmd[:2]:
            case ["pg_isready", _]:
                return ok() if self.postgres else fail
            case ["brew", "services"]:
                self.postgres = True
                return ok()
            case ["tailscale", "ip"]:
                return ok(self.tailscale + "\n") if self.tailscale else fail
            case ["tesseract", _]:
                return ok("tesseract 5.4")
            case ["launchctl", "print"]:
                return ok("state = running\n\tpid = 4242\n") if self.is_loaded else subprocess.CompletedProcess(cmd, 113, "", "")
            case ["launchctl", "bootstrap"]:
                self.is_loaded = True
                self.web_up = True
                return ok()
            case ["launchctl", "bootout"]:
                self.is_loaded = self.web_up = False
                return ok()
            case ["git", "-C"] if cmd[3] == "rev-parse":
                return ok(self.git_heads.pop(0) + "\n")
            case ["git", "-C"] if cmd[3] == "pull":
                return ok("Updating")
            case ["git", "-C"] if cmd[3] == "diff":
                return ok(self.changed)
        return ok()

    def http_ok(self, url, timeout=2):
        return self.web_up if ":8000" in url else True  # Ollama answers

    def ran(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


def vault(tmp_path, mac, built=True):
    root = tmp_path / "repo"
    if built:
        (root / "frontend" / "out").mkdir(parents=True)
        (root / "frontend" / "out" / "index.html").write_text("<html>")
    said = []
    v = Vault(root=root, python="/Users/abhinav/vault/venv/bin/python", run=mac.run, http_ok=mac.http_ok,
              sleep=lambda s: None, home=tmp_path / "home", uid=501, say=said.append)
    return v, said


def test_the_launch_agent_runs_the_vault_for_phones_and_restarts_it_after_a_crash(tmp_path):
    v, _ = vault(tmp_path, FakeMac())
    p = plistlib.loads(plistlib.dumps(v.plist(at_login=False)))
    assert p["Label"] == LABEL
    assert p["ProgramArguments"] == ["/Users/abhinav/vault/venv/bin/python", "-m", "backend.api.app", "--phones"]
    assert p["KeepAlive"] == {"SuccessfulExit": False} and p["WorkingDirectory"].endswith("repo")
    assert p["StandardOutPath"].endswith("data/logs/vault.log")
    assert p["EnvironmentVariables"]["PATH"].startswith("/opt/homebrew/bin:/usr/local/bin:/Users/abhinav/vault/venv/bin")


def test_start_checks_what_is_needed_starts_postgres_and_waits_until_it_answers(tmp_path):
    mac = FakeMac(postgres=False)
    v, said = vault(tmp_path, mac)
    assert v.start()
    assert mac.ran("brew", "services", "start", "postgresql@16")
    assert mac.ran("launchctl", "bootstrap", "gui/501", str(v.local_agent))
    assert v.local_agent.exists() and not v.login_agent.exists()  # not at login unless asked
    assert any("running: http://localhost:8000" in s for s in said)
    assert any("100.101.102.103" in s for s in said)


def test_start_refuses_without_the_web_pages_and_says_how_to_build_them(tmp_path):
    mac = FakeMac()
    v, said = vault(tmp_path, mac, built=False)
    assert not v.start()
    assert not mac.ran("launchctl", "bootstrap")
    assert any("npm run build" in s for s in said)


def test_a_vault_that_dies_on_start_shows_its_log(tmp_path):
    mac = FakeMac()
    v, said = vault(tmp_path, mac)
    v.log.parent.mkdir(parents=True)
    v.log.write_text("This vault is encrypted and its key is not on this Mac.\n")
    real = mac.run

    def dies(cmd, timeout=30):
        r = real(cmd, timeout)
        if cmd[:2] == ["launchctl", "bootstrap"]:
            mac.web_up = False
        if cmd[:2] == ["launchctl", "print"] and mac.is_loaded:
            return subprocess.CompletedProcess(cmd, 0, "state = waiting\n", "")  # no pid: it exited
        return r

    v.run = dies
    assert not v.start(wait=30)
    assert any("key is not on this Mac" in s for s in said)


def test_stop_and_autostart(tmp_path):
    mac = FakeMac(loaded=True)
    v, said = vault(tmp_path, mac)
    v.stop()
    assert mac.ran("launchctl", "bootout", f"gui/501/{LABEL}") and "stopped" in said[-1]
    v.autostart(True)
    assert v.login_agent.exists() and plistlib.loads(v.login_agent.read_bytes())["RunAtLoad"] is True
    v.start()
    assert mac.ran("launchctl", "bootstrap", "gui/501", str(v.login_agent))
    v.autostart(False)
    assert not v.login_agent.exists()


def test_update_installs_only_what_changed_and_says_whether_phones_need_a_new_app(tmp_path):
    assert update_plan(["backend/api/app.py"]) == {"requirements": False, "frontend": False, "phone_app": False}
    assert update_plan(["requirements.txt", "frontend/app/page.tsx", "mobile/lib/src/app.dart"]) == \
        {"requirements": True, "frontend": True, "phone_app": True}
    assert update_plan(["mobile/test/app_test.dart", "mobile/README.md"])["phone_app"] is False

    mac = FakeMac(loaded=True)
    mac.web_up = True
    mac.git_heads = ["aaa", "bbb"]
    mac.changed = "backend/api/app.py\nfrontend/app/page.tsx\n"
    v, said = vault(tmp_path, mac)
    assert v.update()
    assert mac.ran("npm", "--prefix") and not mac.ran("/Users/abhinav/vault/venv/bin/python", "-m", "pip")
    assert any("phones need nothing" in s for s in said)
    assert mac.ran("launchctl", "bootstrap")  # restarted on the new version

    mac.git_heads = ["bbb", "bbb"]
    assert v.update() and said[-1] == "Already up to date."
