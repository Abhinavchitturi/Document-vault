#!/bin/sh
# Double-click in Finder to start the vault (and open it in the browser).
cd "$(dirname "$0")" || exit 1
./vault start
printf "\nYou can close this window; the vault keeps running.\n"
