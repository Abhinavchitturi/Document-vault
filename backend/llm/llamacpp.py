"""Local LLM through llama.cpp: the vault starts llama.cpp's own server (llama-server) next to itself.

Nothing else to install or open: `python -m backend.llm.setup` (run by the installers) downloads a pinned
llama-server build for this computer and the model file into ~/.document-vault/llm (or $VAULT_LLM_DIR),
checking both against their published SHA-256. When both are there, the vault starts llama-server on
127.0.0.1:8091 when it starts, and stops it when it stops. Otherwise it falls back to Ollama (ollama.py),
so a computer that already has Ollama keeps working as before.

Like the Ollama client, this one only ever talks to this computer.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from backend.llm.ollama import LLMUnavailableError

logger = logging.getLogger(__name__)

PORT = 8091
HOST = f"http://127.0.0.1:{PORT}"
CONTEXT = 8192  # tokens: the question, up to a few documents' passages and the answer

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
# The same model the vault used through Ollama (llama3.1:8b), as one GGUF file, pinned to a repository commit.
MODEL = {
    "label": "llama3.1:8b",
    "file": "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
    "url": "https://huggingface.co/bartowski/Meta-Llama-3.1-8B-Instruct-GGUF/resolve/"
           "bf5b95e96dac0462e2a09145ec66cae9a3f12067/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf",
    "size": 4920739232,
    "sha256": "7b064f5842bf9532c91456deda288a1b672397a54fa729aa665952863033557c",
}


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

    def start(self) -> None:
        if not (self.server and self.model):
            raise LLMUnavailableError("The local AI isn't downloaded. Run: python -m backend.llm.setup")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        command = [str(self.server), "--model", str(self.model), "--host", "127.0.0.1", "--port", str(self.port),
                   "--ctx-size", str(CONTEXT), "--n-gpu-layers", "99"]  # all layers on the GPU where there is one
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        with open(self.log, "ab") as out:
            self.process = subprocess.Popen(command, cwd=self.server.parent, stdin=subprocess.DEVNULL, stdout=out,
                                            stderr=out, creationflags=flags)
        logger.info("Started llama.cpp (%s) with %s on port %d", BUILD, self.model.name, self.port)

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def exited(self) -> int | None:
        """The exit code if llama-server stopped by itself (out of memory, a bad file), else None."""
        return self.process.poll() if self.process else None


class LlamaCppLLM:
    """The same interface as OllamaLLM, talking to llama-server's OpenAI-style API."""

    def __init__(self, model: str = MODEL["label"], host: str = HOST, timeout: float = 180, server: LlamaServer | None = None):
        if not host.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError(f"llama.cpp must run on this computer (localhost), got {host!r}")
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.server = server

    @property
    def name(self) -> str:
        return f"llama.cpp/{self.model}"

    def list_models(self) -> list[str]:
        return [self.model]

    def status(self) -> tuple[bool, str]:
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
