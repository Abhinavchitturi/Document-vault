"""Local LLM through llama.cpp: the vault starts llama.cpp's own server (llama-server) next to itself.

Nothing else to install or open: `python -m backend.llm.setup` (run by the installers) downloads a pinned
llama-server build for this computer and the model file into ~/.document-vault/llm (or $VAULT_LLM_DIR),
checking both against their published SHA-256. When both are there, the vault starts llama-server on
127.0.0.1:8091 when it starts, and stops it when it stops. Command-line tools that need the model on their own
(ingest, the evaluations) start it for the length of their run with `running()`.

It only ever talks to this computer.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
import os
import platform
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


class LLMUnavailableError(RuntimeError):
    """The answer model can't be reached; the message says how to fix it."""

PORT = 8091
HOST = f"http://127.0.0.1:{PORT}"

# A llama.cpp build from https://github.com/ggml-org/llama.cpp/releases, pinned with GitHub's SHA-256 of each file.
BUILD = "b12142"
RELEASES = f"https://github.com/ggml-org/llama.cpp/releases/download/{BUILD}"
BINARIES = {  # (sys.platform, machine) -> (file, sha256)
    ("darwin", "arm64"): ("llama-b12142-bin-macos-arm64.tar.gz",
                          "76ba7de8af534937e74dc33a41330b92f1d0d2a6994394f7cbd998de006c605e"),
    ("darwin", "x86_64"): ("llama-b12142-bin-macos-x64.tar.gz",
                           "1459337a2a0c95b91dbfcb647c36ca19a0f25befa15db445b834d076d11268dd"),
    ("win32", "amd64"): ("llama-b12142-bin-win-cpu-x64.zip",
                         "93662f543833e6b2965305d92e4aba44166bb9cc9b76d55fff0d731508765c91"),
    ("win32", "arm64"): ("llama-b12142-bin-win-cpu-arm64.zip",
                         "109f0fe091fd632bb8a150322bbac92275eb23d769e59ddbda7eca577c064492"),
    ("linux", "x86_64"): ("llama-b12142-bin-ubuntu-x64.tar.gz",
                          "14b8487cba9882c56327dcaba6a43f014bae9fa1aa69273d2654790ce7e95af4"),
    ("linux", "aarch64"): ("llama-b12142-bin-ubuntu-arm64.tar.gz",
                           "d0ed8f1a21b4e19b6c588a3ba31488808c2c025ab80f9d56e199e984133be66a"),
}
# The answer model, one GGUF file (4-bit, Q4_K_M) pinned to a repository commit. Which one depends on the computer's
# memory: the 8B model needs about 6 GB of it while it runs, which a laptop with 8 GB doesn't have to spare (everything
# else slows to a crawl while it swaps), so smaller computers get the 3B model (about 2.5 GB). Below 6 GB, none.
MODELS = {
    "large": {
        "label": "llama3.1:8b",
        "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/"
               "bf5b95e96dac0462e2a09145ec66cae9a3f12067/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
        "size": 4920739232,
        "sha256": "7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c",
        "context": 8192,  # tokens: the question, up to a few documents' passages and the answer
    },
    "small": {
        "label": "llama3.2:3b",
        "file": "Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "url": "https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/"
               "5ab33fa94d1d04e903623ae72c95d1696f09f9e8/Llama-3.2-3B-Instruct-Q4_K_M.gguf",
        "size": 2019377696,
        "sha256": "6c1a2b41161032677be168d354123594c0e6e67d2b9227c84f296ad037c728ff",
        "context": 4096,  # less memory for the conversation, on a computer that has little
    },
}
LARGE_FROM_GB = 14  # a "16 GB" computer reports a little under 16
SMALL_FROM_GB = 5  # a "6 GB" laptop reports a little under 6 (the search model needs little now: ONNX)


def total_memory() -> int | None:
    """This computer's memory (RAM) in bytes, or None if it can't be told."""
    try:
        if sys.platform == "win32":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong), ("total", ctypes.c_ulonglong),
                            ("free", ctypes.c_ulonglong), ("page_total", ctypes.c_ulonglong),
                            ("page_free", ctypes.c_ulonglong), ("virtual_total", ctypes.c_ulonglong),
                            ("virtual_free", ctypes.c_ulonglong), ("extended", ctypes.c_ulonglong)]

            status = Status(length=ctypes.sizeof(Status))
            return int(status.total) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)) else None
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return int(out.strip())
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, AttributeError, subprocess.SubprocessError):
        return None


def choose_model(memory: int | None = None) -> dict | None:
    """The answer model for this computer: by its memory, or $VAULT_AI_MODEL (large | small | none). None: too little
    memory for any (search still works; answers are left out)."""
    chosen = os.environ.get("VAULT_AI_MODEL", "").strip().lower()
    if chosen in MODELS:
        return MODELS[chosen]
    if chosen == "none":
        return None
    memory = total_memory() if memory is None else memory
    if memory is None or memory >= LARGE_FROM_GB * 2**30:
        return MODELS["large"]
    return MODELS["small"] if memory >= SMALL_FROM_GB * 2**30 else None


MODEL = choose_model()


def too_little_memory() -> str | None:
    """Why this computer can't run the private AI (too little memory), or None."""
    if MODEL is not None:
        return None
    memory = total_memory() or 0
    return (f"This computer has {memory / 2**30:.1f} GB of memory and the private AI needs a computer with 6 GB or more, "
            "so it isn't used here. Searching still finds your documents and shows the matching passages.")


def llm_dir() -> Path:
    return Path(os.environ.get("VAULT_LLM_DIR") or Path.home() / ".document-vault" / "llm")


def binary_for_this_computer() -> tuple[str, str] | None:
    return BINARIES.get((sys.platform, platform.machine().lower()))


def server_path() -> Path | None:
    """llama-server from the pinned build, if it has been downloaded."""
    name = "llama-server.exe" if sys.platform == "win32" else "llama-server"
    folder = llm_dir() / "bin" / BUILD
    return next((p for p in sorted(folder.rglob(name)) if p.is_file()), None) if folder.is_dir() else None


def model_path() -> Path | None:
    if MODEL is None:
        return None
    path = llm_dir() / "models" / MODEL["file"]
    return path if path.is_file() and path.stat().st_size == MODEL["size"] else None


def installed() -> bool:
    return server_path() is not None and model_path() is not None


class LlamaServer:
    """llama-server as a child of the vault: started with it, stopped with it."""

    def __init__(self, log: Path, server: Path | None = None, model: Path | None = None, port: int = PORT):
        self.log, self.port = log, port
        self.server, self.model = server or server_path(), model or model_path()
        self.process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stopped = False

    def start(self) -> None:
        with self._lock:
            if not self._stopped:
                self._start()

    def _start(self) -> None:
        if not (self.server and self.model):
            raise LLMUnavailableError("The local AI isn't downloaded. Run: python -m backend.llm.setup")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        command = [str(self.server), "--model", str(self.model), "--host", "127.0.0.1", "--port", str(self.port),
                   "--ctx-size", str(MODEL["context"]), "--n-gpu-layers", "99"]  # all layers on the GPU where there is one
        # Below normal priority: on a small laptop its background reading of a new document must never slow down
        # adding the next one, searching or the app itself. CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS on Windows.
        flags = 0x08000000 | 0x00004000 if sys.platform == "win32" else 0
        lower = None if sys.platform == "win32" else (lambda: os.nice(5))
        self._stop_leftover()
        with open(self.log, "ab") as out:
            self.process = subprocess.Popen(command, cwd=self.server.parent, stdin=subprocess.DEVNULL, stdout=out,
                                            stderr=out, creationflags=flags, preexec_fn=lower)
        self._pidfile().write_text(str(self.process.pid))
        logger.info("Started llama.cpp (%s) with %s on port %d", BUILD, self.model.name, self.port)

    def _pidfile(self) -> Path:
        return llm_dir() / "llama-server.pid"

    def _stop_leftover(self) -> None:
        """A llama-server an earlier engine started and couldn't stop (the app was forced to quit, or crashed)
        still holds the port: stop it, so this one can start. Only ever our own (its program is in llm_dir)."""
        try:
            pid = int(self._pidfile().read_text().strip())
        except (OSError, ValueError):
            return
        try:
            if sys.platform == "win32":
                found = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                       text=True, timeout=10).stdout
                if "llama-server" in found.lower():
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
            else:
                command = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                                         timeout=10).stdout
                if "llama-server" in command and str(llm_dir()) in command:
                    os.kill(pid, 15)
                    for _ in range(50):  # up to 5 s for it to let go of the port
                        time.sleep(0.1)
                        if subprocess.run(["ps", "-p", str(pid)], capture_output=True).returncode != 0:
                            break
            logger.info("Stopped a llama.cpp an earlier run left behind (pid %d)", pid)
        except (OSError, subprocess.SubprocessError):
            pass

    def stop(self) -> None:
        with self._lock:
            self._stopped = True  # the vault is closing: a start that was still waiting doesn't happen
            self._stop()

    def _stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        if self.process:
            try:  # stopped cleanly: nothing left over for the next start to look for (tasklist is slow on Windows)
                if self._pidfile().read_text().strip() == str(self.process.pid):
                    self._pidfile().unlink()
            except OSError:
                pass

    def exited(self) -> int | None:
        """The exit code if llama-server stopped by itself (out of memory, a bad file), else None."""
        return self.process.poll() if self.process else None


class LlamaCppLLM:
    """The answer model, through llama-server's OpenAI-style API: plain answers, streamed answers, and JSON."""

    def __init__(self, model: str | None = None, host: str = HOST, timeout: float = 180, server: LlamaServer | None = None):
        if not host.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError(f"llama.cpp must run on this computer (localhost), got {host!r}")
        self.model = model or (MODEL["label"] if MODEL else "none")
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.server = server

    @property
    def name(self) -> str:
        return f"llama.cpp/{self.model}"

    def list_models(self) -> list[str]:
        return [self.model]

    def status(self) -> tuple[bool, str]:
        if self.server and (problem := too_little_memory()):
            return False, problem
        if self.server and not (self.server.server and self.server.model):
            size = f"about {MODEL['size'] / 1e9:.0f} GB" if MODEL else ""
            return False, ("The local AI isn't downloaded yet. Run the setup again, or in the vault folder: "
                           f"python -m backend.llm.setup ({size}, once).")
        if self.server and self.server.process is None:  # not started yet (it starts once the search model is ready):
            # nothing to ask. On Windows, knocking on a port nobody listens on takes 2 seconds, every reload.
            return False, "The answer model is starting. Answers start in a moment."
        if self.server and self.server.exited() is not None:
            return False, (f"The local AI stopped (code {self.server.exited()}). Restart the vault; "
                           f"if it keeps stopping, see {self.server.log}.")
        try:
            with urllib.request.urlopen(f"{self.host}/health", timeout=2) as response:
                ok = response.status == 200
        except urllib.error.HTTPError as e:
            if e.code == 503:
                return False, "The answer model is loading. Answers start in a moment."
            return False, f"The local AI answered with an error ({e.code})."
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            if self.server and self.server.process is None:  # it starts once the search model is ready
                return False, "The answer model is starting. Answers start in a moment."
            return False, "The local AI isn't running. Restart the vault."
        return (True, f"Answers ready ({self.model}, llama.cpp on this computer)") if ok else (False, "The local AI isn't ready.")

    def _messages(self, system: str, prompt: str, history: list[dict] | None) -> list[dict]:
        return [{"role": "system", "content": system}, *(history or []), {"role": "user", "content": prompt}]

    def _open(self, messages: list[dict], stream: bool, schema: dict | None = None):
        body = {"messages": messages, "stream": stream, "temperature": 0}  # the same question, the same answer
        if schema is not None:  # llama.cpp constrains the output to this JSON schema
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
        request = urllib.request.Request(f"{self.host}/v1/chat/completions", data=json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            if e.code == 503:
                raise LLMUnavailableError("The answer model is still loading. Ask again in a moment.") from e
            raise LLMUnavailableError(f"The local AI returned an error ({e.code}): "
                                      f"{e.read().decode(errors='replace')[:200]}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise LLMUnavailableError("Can't reach the local AI on this computer. Restart the vault and try again.") from e

    def generate(self, system: str, prompt: str, history: list[dict] | None = None) -> str:
        with self._open(self._messages(system, prompt, history), stream=False) as response:
            return json.loads(response.read())["choices"][0]["message"]["content"].strip()

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        """An answer shaped by a JSON schema (sorting documents, reading fields out of them)."""
        with self._open(self._messages(system, prompt, None), stream=False, schema=schema) as response:
            content = json.loads(response.read())["choices"][0]["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMUnavailableError(f"The model's answer wasn't valid JSON: {content[:120]}") from e

    def stream(self, system: str, prompt: str, history: list[dict] | None = None):
        """Yield the answer piece by piece as the model writes it (server-sent events)."""
        with self._open(self._messages(system, prompt, history), stream=True) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                event = json.loads(data)
                if event.get("error"):
                    raise LLMUnavailableError(f"llama.cpp error: {event['error']}")
                choice = (event.get("choices") or [{}])[0]
                piece = (choice.get("delta") or {}).get("content") or ""
                if piece:
                    yield piece
                if choice.get("finish_reason"):
                    return


@contextmanager
def running(log: Path | None = None, wait: float = 120):
    """The answer model for a command-line run (ingest, the evaluations): llama-server started for the length of the
    `with` block, or None when it isn't downloaded (the tools then do without it)."""
    if not installed():
        yield None
        return
    server = LlamaServer(log=log or llm_dir() / "llama-server.log")
    server.start()
    llm = LlamaCppLLM(server=server)
    try:
        deadline = time.monotonic() + wait
        while not llm.status()[0] and time.monotonic() < deadline and server.exited() is None:
            time.sleep(0.5)
        yield llm
    finally:
        server.stop()
