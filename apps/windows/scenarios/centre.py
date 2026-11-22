"""The centre computer, for the Windows app's scenarios: a real vault engine with network access for other devices
(on 127.0.0.1:8463), one document in it, and a one-time pairing code. Runs until `stop` appears in its folder.

    python apps/windows/scenarios/centre.py <folder>      -> <folder>/pairing.json  {"address", "code", "fingerprint"}
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

folder = Path(sys.argv[1]).resolve()
folder.mkdir(parents=True, exist_ok=True)
repo = Path(__file__).resolve().parents[3]
env = {**os.environ, "PYTHONPATH": str(repo), "VAULT_KEYCHAIN": "off", "VAULT_AI_AUTO_DOWNLOAD": "0", "VAULT_KEY_DIR": str(folder / "keys"),
       "VAULT_LLM_DIR": str(folder / "llm"), "VAULT_FACTS_READER": "rules", "PYTHONUTF8": "1"}
engine = subprocess.Popen(
    [sys.executable, "-m", "backend.engine", "--index-dir", str(folder / "index"),
     "--database-url", "sqlite:///" + (folder / "vault.db").as_posix(), "--no-llm",
     "--phone-port", "8463", "--phone-address", "127.0.0.1", "--owner", "centre"],
    cwd=folder, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=open(folder / "centre.log", "w"),
    text=True, encoding="utf-8")

for line in engine.stdout:
    if '"ready"' in line:
        break
    if '"failed"' in line:
        sys.exit(f"the centre didn't start: {line}")

ids = iter(range(1, 10**6))


def ask(method, path, **body):
    number = next(ids)
    engine.stdin.write(json.dumps({"id": number, "method": method, "path": path, "headers": headers, **body}) + "\n")
    engine.stdin.flush()
    for line in engine.stdout:
        message = json.loads(line)
        if message.get("id") == number:
            return message


headers = {}
signed = ask("POST", "/api/auth/setup", json={"name": "centre", "password": "a long test password for the centre"})
headers["cookie"] = signed["headers"]["set-cookie"].split(";")[0]
document = folder / "Car Insurance 2026.txt"
document.write_text("Car policy KMI/PC/26/0093318. Period: 1 April 2026 to 31 March 2027. Insured: Kavya Mehra.")
added = ask("POST", "/api/documents", files=[{"path": str(document)}], form={"category": "auto"})
assert added["status"] == 200, added
pairing = ask("POST", "/api/mobile/pairing?typed=true")["json"]
(folder / "pairing.json").write_text(json.dumps(
    {"address": "127.0.0.1:8463", "code": pairing["code"], "fingerprint": pairing["fingerprint"]}))
print("centre ready, code", pairing["code"], flush=True)

while not (folder / "stop").exists():
    time.sleep(1)
engine.stdin.close()
engine.wait(timeout=30)
