#!/bin/bash
# "Document Vault.app" on the Mac. The app holds no vault code: the first time it is opened it runs
# install.sh in a Terminal window (so the progress and any error are visible), which downloads the vault
# into ~/Document-Vault and sets it up. After that, opening the app starts the vault and opens it in
# the browser, like ./vault start.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DIR="${DOCUMENT_VAULT_DIR:-$HOME/Document-Vault}"
INSTALL_URL="https://raw.githubusercontent.com/Abhinavchitturi/Document-vault/main/install.sh"
URL="http://localhost:8000"
CACHE="$HOME/Library/Caches/Document Vault"
LOG="$HOME/Library/Logs/Document Vault.log"

dialog() {  # message, default button: prints the button pressed (nothing for Cancel)
  osascript -e "button returned of (display dialog \"$1\" with title \"Document Vault\" buttons {\"Cancel\", \"$2\"} default button \"$2\" with icon note)" 2>/dev/null
}
notify() { osascript -e "display notification \"$1\" with title \"Document Vault\"" >/dev/null 2>&1; }
in_terminal() {  # run a script in a new Terminal window (a .command file opens there, no extra permission)
  mkdir -p "$CACHE"
  printf '#!/bin/bash\nexport PATH="%s"\n%s\n' "$PATH" "$1" > "$CACHE/$2.command"
  chmod +x "$CACHE/$2.command"
  open "$CACHE/$2.command"
}

# Already running: just show it.
if curl -fs -o /dev/null --max-time 2 "$URL/api/auth/status"; then
  open "$URL"
  exit 0
fi

# Installed: start it.
if [ -x "$DIR/vault" ] && [ -x "$DIR/venv/bin/python" ] && [ -f "$DIR/frontend/out/index.html" ]; then
  notify "Starting… it opens in your browser in a moment."
  if "$DIR/vault" start >"$LOG" 2>&1; then
    open "$URL"
  else
    in_terminal "cat \"$LOG\"; echo; echo 'Document Vault did not start. The lines above say why.'" "Document Vault problem"
  fi
  exit 0
fi

# First time: the two free apps the installer can't fetch by itself.
if ! command -v brew >/dev/null 2>&1 || { [ ! -d /Applications/Ollama.app ] && ! command -v ollama >/dev/null 2>&1; }; then
  answer=$(dialog "Document Vault needs two free apps first:\n\n•  Homebrew, which installs the tools it uses\n•  Ollama, which runs the AI on this Mac\n\nInstall both, open Ollama once, then open Document Vault again." "Open download pages")
  if [ -n "$answer" ]; then
    open "https://brew.sh"
    open "https://ollama.com/download"
  fi
  exit 0
fi

answer=$(dialog "Document Vault will set itself up in $DIR.\n\nThe first setup downloads about 6 GB (Python packages and the AI model) and takes 10 to 30 minutes. A Terminal window shows the progress; when it is done the vault opens in your browser.\n\nYour documents never leave this Mac." "Set up")
[ -n "$answer" ] || exit 0
in_terminal "curl -fsSL \"$INSTALL_URL\" | sh -s --
echo
echo 'You can close this window. From now on, just open Document Vault.'" "Document Vault setup"
