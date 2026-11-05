"""Session files (.sot.json): media path + SHA-256, segments, edits, settings used, statistics."""
import datetime
import json
import os

from . import VERSION


def save(path, data):
    data = dict(data)
    data["app"] = "SecureOfflineTranscriber"
    data["version"] = VERSION
    data.setdefault("saved", datetime.datetime.now().isoformat(timespec="seconds"))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("app") != "SecureOfflineTranscriber":
        raise ValueError("This is not a Secure Offline Transcriber session file.")
    return data
