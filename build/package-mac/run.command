#!/bin/bash
# Secure Offline Transcriber - start the program (macOS, Apple silicon).
# Works from any folder, also one with spaces or non-English letters in its name.
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/python/bin/python3"
if [ ! -x "$PY" ]; then
  echo "The file python/bin/python3 is missing. Unzip SecureOfflineTranscriber-portable-macos-arm64.zip again."
  read -r -p "Press Return to close."; exit 1
fi
if xattr -p com.apple.quarantine "$PY" >/dev/null 2>&1; then
  echo "macOS has marked this folder as downloaded from the internet, so it would block the program's files."
  echo "Folder: $DIR"
  read -r -p "Remove that mark from this folder now? (y/N) " answer
  case "$answer" in
    [yY]*) xattr -dr com.apple.quarantine "$DIR" && echo "Done." ;;
    *) echo "Not changed. See README.txt, section START."; read -r -p "Press Return to close."; exit 1 ;;
  esac
fi
nohup "$PY" -s -E "$DIR/app/OfflineTranscriber.py" "$@" >/dev/null 2>&1 &
disown
echo "Secure Offline Transcriber is starting. You can close this window."
