"""Where everything lives (spec section 14).

Layout of the portable folder (ROOT):
    ROOT/python/          embeddable Python
    ROOT/ffmpeg/ffmpeg.exe
    ROOT/app/             this code (APP_DIR)
    ROOT/models/          default model download folder
    ROOT/data/            settings.json (always) + default data folder (logs, temp, jobs)
"""
import datetime
import glob
import logging
import os
import sys
import tempfile
import time

from . import APP_ID

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(APP_DIR)

_settings_dir = None


def _writable(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=folder, prefix=".probe")
        os.close(fd)
        os.remove(probe)
        return True
    except OSError:
        return False


def fallback_dir():
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP_ID)


def settings_dir():
    """data\\ in the portable folder; %LOCALAPPDATA%\\SecureOfflineTranscriber if that is read-only (FR-DATA-01)."""
    global _settings_dir
    if _settings_dir is None:
        own = os.path.join(ROOT, "data")
        _settings_dir = own if _writable(own) else fallback_dir()
        os.makedirs(_settings_dir, exist_ok=True)
    return _settings_dir


def using_fallback():
    return os.path.normcase(settings_dir()) == os.path.normcase(fallback_dir())


def to_stored(path):
    """Store paths inside the portable folder relative to it (FR-DATA-02)."""
    if not path:
        return ""
    full = os.path.abspath(path)
    try:
        rel = os.path.relpath(full, ROOT)
    except ValueError:  # another drive
        return full
    if rel.startswith(".."):
        return full
    return rel


def from_stored(path, default_rel=""):
    path = path or default_rel
    if not path:
        return ""
    path = os.path.expandvars(os.path.expanduser(path))
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(ROOT, path))


def default_output_folder():
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "SOT Transcripts")


# ------------------------------------------------------------------ logging
_log_handler = None


def setup_logging(data_folder):
    """Daily log file in <data>/logs, kept 7 days (NFR/FR-PRIV-05).

    Without a console (pythonw.exe) stdout/stderr are None: they go to the log
    file so libraries that print cannot crash the app (FR-PRIV-06)."""
    global _log_handler
    logs = os.path.join(data_folder, "logs")
    try:
        os.makedirs(logs, exist_ok=True)
        cutoff = time.time() - 7 * 86400
        for old in glob.glob(os.path.join(logs, "sot-*.log")):
            if os.path.getmtime(old) < cutoff:
                os.remove(old)
        path = os.path.join(logs, f"sot-{datetime.date.today():%Y-%m-%d}.log")
        stream = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream
    root = logging.getLogger()
    if _log_handler is not None:
        root.removeHandler(_log_handler)
    _log_handler = logging.StreamHandler(stream)
    _log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(processName)s %(name)s: %(message)s"))
    root.addHandler(_log_handler)
    root.setLevel(logging.INFO)
    return logs


def offline_env():
    """Set before loading any model library (FR-PRIV-02)."""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
