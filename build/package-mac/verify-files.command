#!/bin/bash
# Checks every file of Secure Offline Transcriber against SHA256SUMS.txt. It changes nothing.
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR" || exit 1
if [ ! -f SHA256SUMS.txt ]; then echo "SHA256SUMS.txt is missing."; read -r -p "Press Return to close."; exit 1; fi
echo "Checking $(wc -l < SHA256SUMS.txt | tr -d ' ') files..."
bad=$(LC_ALL=en_US.UTF-8 shasum -a 256 -c SHA256SUMS.txt 2>/dev/null | grep -v ': OK$')
if [ -z "$bad" ]; then
  echo "All files are complete and unchanged."
else
  echo "These files are missing or changed:"; echo "$bad" | head -40
  echo; echo "Delete this folder and unzip the zip again."
fi
echo
read -r -p "Press Return to close this window."
