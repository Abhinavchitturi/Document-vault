#!/bin/sh
# One-command setup for Document Vault on a Mac.
#
#   curl -fsSL https://raw.githubusercontent.com/Abhinavchitturi/Document-vault/main/install.sh | sh -s -- --with-samples
#
# or, from a copy you already downloaded:  ./install.sh --with-samples
#
# Downloads the vault (into ~/Document-Vault, or $DOCUMENT_VAULT_DIR) when it isn't already here,
# installs what it needs (Homebrew packages, Python packages, the web app, the local answer model),
# starts it and opens it in your browser. Safe to run again: it skips what is already done and
# updates the download.
#   --with-samples   also load the fictional sample documents, so there is something to try
#   --no-start       set everything up but don't start the vault
set -eu

say()  { printf '\n==> %s\n' "$1"; }
fail() { printf '\nSetup stopped: %s\n' "$1" >&2; exit 1; }

SAMPLES=0
START=1
for arg in "$@"; do
  case "$arg" in
    --with-samples) SAMPLES=1 ;;
    --no-start) START=0 ;;
    *) fail "unknown option '$arg' (use --with-samples and/or --no-start)" ;;
  esac
done

[ "$(uname -s)" = "Darwin" ] || fail "this installer is for macOS. On Linux, follow 'Quick start' in the README by hand."

REPO_URL="${DOCUMENT_VAULT_REPO:-https://github.com/Abhinavchitturi/Document-vault.git}"
HERE="$(cd "$(dirname "$0")" 2>/dev/null && pwd || true)"
if [ -n "$HERE" ] && [ -f "$HERE/backend/deploy/vault.py" ]; then
  cd "$HERE"  # run from a copy that is already downloaded
elif [ -f backend/deploy/vault.py ]; then
  :  # piped into sh from inside a copy
else
  say "Downloading Document Vault"
  command -v git >/dev/null 2>&1 || fail "git is missing. Run 'xcode-select --install', then run this again."
  DIR="${DOCUMENT_VAULT_DIR:-$HOME/Document-Vault}"
  if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull --ff-only --quiet || printf 'Could not update %s; using the copy that is there.\n' "$DIR"
  elif [ -e "$DIR" ] && [ -n "$(ls -A "$DIR" 2>/dev/null)" ]; then
    fail "$DIR already exists and is not a Document Vault download. Move it, or set DOCUMENT_VAULT_DIR to another folder."
  else
    git clone --quiet "$REPO_URL" "$DIR" || fail "could not download $REPO_URL. Check your internet connection."
  fi
  cd "$DIR"
  printf 'Using %s\n' "$PWD"
fi

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

if [ "$SAMPLES" = 1 ]; then
  say "Loading the fictional sample documents"
  export VAULT_DATABASE_URL="${VAULT_DATABASE_URL:-sqlite:///data/index/vault.db}"
  venv/bin/python -m backend.upload.ingest samples/pdfs samples/scans --owner "$(id -un)" || true
fi

if [ "$START" = 1 ]; then
  say "Starting the vault"
  ./vault start
  open "http://localhost:8000" 2>/dev/null || true
  say "All set"
  printf 'Document Vault is running at http://localhost:8000 (choose a password on the first screen).\n'
  printf 'Stop it with:  %s/vault stop     Start it again with:  %s/vault start\n' "$PWD" "$PWD"
else
  say "All set"
  printf 'Start the vault:  %s/vault start   then open http://localhost:8000 and choose a password.\n' "$PWD"
fi
