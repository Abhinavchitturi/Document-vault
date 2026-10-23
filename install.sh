#!/bin/sh
# One-step setup for Document Vault on a Mac: ./install.sh [--with-samples]
#
# Installs what the vault needs (Homebrew packages, Python packages, the web app, the local answer
# model), then tells you how to start it. Safe to run again: it skips what is already done.
#   --with-samples   also load the fictional sample documents, so there is something to try
set -eu
cd "$(dirname "$0")"

say()  { printf '\n==> %s\n' "$1"; }
fail() { printf '\nSetup stopped: %s\n' "$1" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || fail "this installer is for macOS. On Linux, follow 'Quick start' in the README by hand."

say "Checking Homebrew"
command -v brew >/dev/null 2>&1 || fail "Homebrew is missing. Install it from https://brew.sh, then run ./install.sh again."

say "Installing Tesseract (reads scans and photos) and Node (builds the web pages)"
for pkg in tesseract tesseract-lang node; do
  brew list "$pkg" >/dev/null 2>&1 || brew install "$pkg"
done

say "Checking Python 3.11+"
PY=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
[ -n "$PY" ] || { brew install python@3.11; PY="$(brew --prefix python@3.11)/bin/python3.11"; }

say "Installing Python packages (the first time this takes a few minutes)"
[ -x venv/bin/python ] || "$PY" -m venv venv
venv/bin/python -m pip install --quiet --upgrade pip
venv/bin/python -m pip install --quiet -r requirements.txt

say "Building the web app"
(cd frontend && npm install --no-audit --no-fund && npm run build)

say "Checking Ollama (the local AI that writes the answers)"
if ! command -v ollama >/dev/null 2>&1 && [ ! -d /Applications/Ollama.app ]; then
  printf 'Ollama is not installed. Download it from https://ollama.com, open it once, then run ./install.sh again.\n' >&2
  fail "Ollama is needed for answers."
fi
if command -v ollama >/dev/null 2>&1 && ! ollama list >/dev/null 2>&1; then
  fail "Ollama is installed but not running. Open the Ollama app, then run ./install.sh again."
fi
if command -v ollama >/dev/null 2>&1 && ! ollama list | grep -q 'llama3.1:8b'; then
  say "Downloading the answer model (about 5 GB, once)"
  ollama pull llama3.1:8b
fi

if [ "${1:-}" = "--with-samples" ]; then
  say "Loading the fictional sample documents"
  export VAULT_DATABASE_URL="${VAULT_DATABASE_URL:-sqlite:///data/index/vault.db}"
  venv/bin/python -m backend.upload.ingest samples/pdfs samples/scans --owner "$(id -un)" || true
fi

say "All set"
cat <<'MSG'
Start the vault:   ./vault start        (or double-click "Start Vault.command" in Finder)
Then open:         http://localhost:8000   and choose a password.
Stop it with:      ./vault stop
MSG
