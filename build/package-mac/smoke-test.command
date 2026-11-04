#!/bin/bash
# Secure Offline Transcriber - check that every library loads, open the window and Options, then close.
DIR="$(cd "$(dirname "$0")" && pwd)"
"$DIR/python/bin/python3" -s -E "$DIR/app/OfflineTranscriber.py" --smoke-test
rc=$?
echo
if [ $rc -eq 0 ]; then echo "SMOKE TEST PASSED"; else echo "SMOKE TEST FAILED (code $rc). Run run-debug.command for details."; fi
read -r -p "Press Return to close this window."
exit $rc
