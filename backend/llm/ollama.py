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

    def generate(self, system: str, prompt: str) -> str:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": 0},  # the same question should get the same answer
        }
        request = urllib.request.Request(
            f"{self.host}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read())["message"]["content"].strip()
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
