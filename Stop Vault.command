#!/bin/sh
# Double-click in Finder to stop the vault.
cd "$(dirname "$0")" || exit 1
./vault stop
printf "\nYou can close this window.\n"
