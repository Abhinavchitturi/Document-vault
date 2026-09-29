"""Local LLM through Ollama (https://ollama.com): the model runs on this computer.

The vault only uses local models, so this client refuses any Ollama address that
isn't this machine: a typo in a config file must not quietly send family
documents somewhere else.

Setup (once):
    1. Install Ollama from ollama.com and open it (or run `ollama serve`).
    2. ollama pull llama3.1:8b        # ~5 GB; fits a 16 GB Mac
Any other local model works too: `--llm-model gemma2:9b`, `--llm-model mistral`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlparse

DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_HOST = "http://127.0.0.1:11434"
LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}


class LLMUnavailableError(RuntimeError):
    """The model can't be reached; the message says how to fix it."""


class OllamaLLM:
    def __init__(self, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST, timeout: float = 180):
        if urlparse(host).hostname not in LOCAL_HOSTNAMES:
            raise ValueError(f"Ollama host must be this computer (localhost), got {host!r}")
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    @property
    def name(self) -> str:
        return f"ollama/{self.model}"

    def list_models(self) -> list[str]:
        """Models downloaded into Ollama on this computer ([] if Ollama isn't running)."""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=2) as response:
                return sorted(m.get("name", "") for m in json.loads(response.read()).get("models", []) if m.get("name"))
        except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
            return []

    def status(self) -> tuple[bool, str]:
        """Quick check (no generation): is Ollama running and is the model downloaded?"""
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=2) as response:
                models = [m.get("name", "") for m in json.loads(response.read()).get("models", [])]
        except (urllib.error.URLError, ConnectionError, TimeoutError, ValueError):
            return False, "Ollama isn't running. Open the Ollama app (or run `ollama serve`), then ask again."
        wanted = self.model if ":" in self.model else f"{self.model}:latest"
        if wanted not in models:
            return False, f"The model {self.model} isn't downloaded yet. In a terminal, run: ollama pull {self.model}"
        return True, f"Answers ready ({self.model}, on this computer)"

    def _messages(self, system: str, prompt: str, history: list[dict] | None) -> list[dict]:
        return [{"role": "system", "content": system}, *(history or []), {"role": "user", "content": prompt}]

    def _open(self, messages: list[dict], stream: bool, schema: dict | None = None):
        body = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            "options": {"temperature": 0},  # the same question should get the same answer
        }
        if schema is not None:
            body["format"] = schema  # Ollama constrains the output to this JSON schema
        request = urllib.request.Request(
            f"{self.host}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            return urllib.request.urlopen(request, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            if e.code == 404 or "not found" in detail.lower():
                raise LLMUnavailableError(
                    f"The model {self.model} isn't downloaded yet. Run: ollama pull {self.model}"
                ) from e
            raise LLMUnavailableError(f"Ollama returned an error ({e.code}): {detail[:200]}") from e
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise LLMUnavailableError(
                "Can't reach Ollama on this computer. Open the Ollama app (or run `ollama serve`) and try again."
            ) from e

    def generate(self, system: str, prompt: str, history: list[dict] | None = None) -> str:
        with self._open(self._messages(system, prompt, history), stream=False) as response:
            return json.loads(response.read())["message"]["content"].strip()

    def generate_json(self, system: str, prompt: str, schema: dict) -> dict:
        """An answer shaped by a JSON schema (sorting documents, reading fields out of them)."""
        with self._open(self._messages(system, prompt, None), stream=False, schema=schema) as response:
            content = json.loads(response.read())["message"]["content"]
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMUnavailableError(f"The model's answer wasn't valid JSON: {content[:120]}") from e

    def stream(self, system: str, prompt: str, history: list[dict] | None = None):
        """Yield the answer piece by piece as the model writes it (like a chatbot)."""
        with self._open(self._messages(system, prompt, history), stream=True) as response:
            for line in response:  # Ollama streams one JSON object per line
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise LLMUnavailableError(f"Ollama error: {event['error']}")
                piece = event.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if event.get("done"):
                    return
