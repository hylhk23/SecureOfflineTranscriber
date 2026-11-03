"""The model list (models.json): which models can be downloaded, from where, and how Automatic uses them.

The program ships a built-in list in app\\models.json. A newer list can be given to users as one file:
Options > Transcription > Import model list... checks it and saves it as data\\models.json, which is then used
instead of the built-in one (until the program itself brings a newer built-in list).

A list can add, update or remove models of the four kinds this program can run (Whisper, SenseVoiceSmall,
Parakeet, Moonshine). A model of a new kind needs a new program version. Downloads always come from
huggingface.co at a fixed revision and every file is checked by SHA-256, so a list file cannot make the
program fetch anything else."""
import copy
import json
import os
import re
import tempfile

from . import APP_ID, VERSION, paths

FORMAT = 2
KINDS = ("whisper", "sensevoice", "parakeet", "moonshine")
LANGS = ("en", "fr", "yue", "zh")
PRIORITIES = ("Must", "Should", "Could")
BUILTIN = os.path.join(paths.APP_DIR, "models.json")
SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ListError(Exception):
    """The file is not a usable model list; the message lists every problem."""


def override_path():
    return os.path.join(paths.settings_dir(), "models.json")


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or "0")))


def read(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def validate(data):
    """Returns (cleaned list, warnings). Raises ListError with every problem found."""
    from .engines import detect_type
    err, warn = [], []
    if not isinstance(data, dict) or data.get("app") != APP_ID or not isinstance(data.get("models"), list):
        raise ListError("This is not a Secure Offline Transcriber model list.")
    if data.get("format") != FORMAT:
        err.append(f"format is {data.get('format')!r}; this program reads format {FORMAT}.")
    if not vtuple(data.get("list_version")):
        err.append("list_version is missing (for example \"2026.11.03\").")
    need = data.get("min_app_version", "0")
    if vtuple(need) > vtuple(VERSION):
        err.append(f"This list needs program version {need} or newer (this is {VERSION}).")
    out = copy.deepcopy(data)
    ids = set()
    for i, m in enumerate(out["models"], 1):
        where = f"model {i} ({m.get('id', '?')})" if isinstance(m, dict) else f"model {i}"
        if not isinstance(m, dict):
            err.append(f"{where}: not an object.")
            continue
        mid = m.get("id", "")
        if not SAFE_ID.match(str(mid)):
            err.append(f"{where}: id must be lower-case letters, digits, '.', '-' or '_' (it is the folder name).")
        elif mid in ids:
            err.append(f"{where}: id is used twice.")
        ids.add(mid)
        if not str(m.get("name", "")).strip():
            err.append(f"{where}: name is missing.")
        if m.get("kind") not in KINDS:
            err.append(f"{where}: kind {m.get('kind')!r} is not one this program can run ({', '.join(KINDS)}); "
                       "a new kind needs a new program version.")
        if not REPO.match(str(m.get("repo", ""))):
            err.append(f"{where}: repo must be a Hugging Face 'owner/name'.")
        if not HEX40.match(str(m.get("revision", ""))):
            err.append(f"{where}: revision must be the 40-character commit id (run update-model-list to fill it).")
        langs = m.get("languages")
        if not isinstance(langs, list) or not langs or any(l not in LANGS for l in langs):
            err.append(f"{where}: languages must be a list taken from {', '.join(LANGS)}.")
        if not str(m.get("license", "")).strip():
            err.append(f"{where}: license is missing.")
        url = m.get("license_url", "")
        if url and not str(url).startswith(("https://", "http://")):
            err.append(f"{where}: license_url must start with https://.")
        if m.get("offered", "Could") not in PRIORITIES:
            err.append(f"{where}: offered must be one of {', '.join(PRIORITIES)}.")
        files = m.get("files")
        if not isinstance(files, list) or not files:
            err.append(f"{where}: files list is missing.")
            continue
        names = set()
        for f in files:
            fn = str(f.get("name", "")) if isinstance(f, dict) else ""
            if not SAFE_FILE.match(fn) or ".." in fn:
                err.append(f"{where}: file name {fn!r} is not allowed (plain names only, no folders).")
                continue
            if fn in names:
                err.append(f"{where}: file {fn} is listed twice.")
            names.add(fn)
            if not isinstance(f.get("size"), int) or f["size"] <= 0:
                err.append(f"{where}: {fn} has no size (run update-model-list to fill it).")
            if not HEX64.match(str(f.get("sha256", ""))):
                err.append(f"{where}: {fn} has no SHA-256 (run update-model-list to fill it).")
        if m.get("kind") in KINDS and names:
            with tempfile.TemporaryDirectory() as d:  # the same file check the program uses on a real folder
                for fn in names:
                    if SAFE_FILE.match(fn):
                        open(os.path.join(d, fn), "w").close()
                kind, missing = detect_type(d)
            if kind != m.get("kind"):
                err.append(f"{where}: the files do not make a {m.get('kind')} model"
                           + (f" (missing: {', '.join(missing)})" if not kind else f" (they look like {kind})") + ".")
        m["size"] = sum(f.get("size", 0) for f in files if isinstance(f, dict) and isinstance(f.get("size"), int))
        m.setdefault("offered", "Could")
        m.setdefault("note", "")
    rec = out.get("recommended")
    if not isinstance(rec, dict) or not rec.get("below") or not rec.get("at_or_above"):
        err.append("recommended must have 'ram_gb', 'below' and 'at_or_above'.")
    else:
        for k in ("below", "at_or_above"):
            for mid in rec.get(k, []):
                if mid not in ids:
                    err.append(f"recommended.{k}: {mid} is not in the list.")
        if not isinstance(rec.get("ram_gb", 12), (int, float)):
            err.append("recommended.ram_gb must be a number.")
    auto = out.get("automatic")
    if not isinstance(auto, dict):
        err.append("automatic must give the model order for each language.")
    else:
        by_id = {m.get("id"): m for m in out["models"] if isinstance(m, dict)}
        for lang, order in auto.items():
            if lang not in LANGS or not isinstance(order, list):
                err.append(f"automatic.{lang}: unknown language or not a list.")
                continue
            for mid in order:
                if mid not in by_id:
                    err.append(f"automatic.{lang}: {mid} is not in the list.")
                elif lang not in by_id[mid].get("languages", []):
                    warn.append(f"automatic.{lang}: {mid} does not list {lang} among its languages.")
        for lang in LANGS:
            if lang not in auto:
                warn.append(f"automatic: no order for {lang}; the smallest suitable installed model will be used.")
    if err:
        raise ListError("\n".join(err))
    return out, warn


def load():
    """The list in use: the imported list when it is valid and not older than the built-in one."""
    builtin, _ = validate(read(BUILTIN))
    info = {"data": builtin, "source": "built-in", "path": BUILTIN, "problem": ""}
    ov = override_path()
    if os.path.isfile(ov):
        try:
            data, _ = validate(read(ov))
        except (ListError, OSError, ValueError) as ex:
            info["problem"] = f"The imported model list in {ov} was not used: {ex}"
            return info
        if vtuple(data["list_version"]) >= vtuple(builtin["list_version"]):
            return {"data": data, "source": "imported", "path": ov, "problem": ""}
        info["problem"] = (f"The imported model list ({data['list_version']}) is older than the program's own "
                           f"({builtin['list_version']}), so the program's list is used.")
    return info


def diff(old, new):
    o = {m["id"]: m for m in old["models"]}
    n = {m["id"]: m for m in new["models"]}
    added = [n[i]["name"] for i in n if i not in o]
    removed = [o[i]["name"] for i in o if i not in n]
    changed = [n[i]["name"] for i in n if i in o and n[i]["revision"] != o[i]["revision"]]
    return added, removed, changed


def import_file(path, current):
    """Check a list file and make it the list in use. Returns (data, added, removed, changed, warnings)."""
    try:
        raw = read(path)
    except (OSError, ValueError) as ex:
        raise ListError(f"The file cannot be read: {ex}")
    data, warn = validate(raw)
    builtin = validate(read(BUILTIN))[0]
    if vtuple(data["list_version"]) < vtuple(builtin["list_version"]):
        raise ListError(f"This list ({data['list_version']}) is older than the one built into the program "
                        f"({builtin['list_version']}).")
    target = override_path()
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)
    os.replace(tmp, target)
    return (data,) + diff(current, data) + (warn,)


def restore_builtin():
    try:
        os.remove(override_path())
    except FileNotFoundError:
        pass


def is_list_file(path):
    """True for a model list file (so it can be dropped on the window)."""
    if not path.lower().endswith(".json"):
        return False
    try:
        with open(path, encoding="utf-8-sig") as f:
            head = f.read(4096)
        return f'"{APP_ID}"' in head and '"models"' in head and '"list_version"' in head
    except OSError:
        return False
