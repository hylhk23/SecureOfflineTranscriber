#!/bin/bash
# Secure Offline Transcriber - start with the log shown in this window (for troubleshooting).
DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/python/bin/python3" -s -E "$DIR/app/OfflineTranscriber.py" --debug "$@"
echo
read -r -p "The program has closed. Press Return to close this window."
