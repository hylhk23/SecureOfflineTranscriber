"""Voice library (version 2.4.0): voiceprints of people the user has named, kept only on this computer.

File: <data folder>/voices.json. Only numbers are stored (a voiceprint is 192 numbers from the voice model), never
audio. Every field is checked when the file is read; anything that does not pass is left out and reported, and
nothing in the file is ever run. Writes go to a temporary file first and then replace the old file.
A voiceprint belongs to the voice model that made it: people saved with another voice model are kept but not used
for matching until they are saved again with the model in use."""
import datetime
import json
import math
import os
import re
import tempfile
import uuid

from . import speakers

APP = "SecureOfflineTranscriber"
KIND = "voice-library"
FORMAT = 1
FILE_NAME = "voices.json"
MAX_SAMPLES = 20      # per person: the oldest is dropped when a 21st recording is added
MAX_NAME = 100
MAX_NOTE = 200
MAX_DIM = 1024
SAFE_MODEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PID = re.compile(r"^p-[0-9a-f]{8,32}$")


def path_for(data_folder):
    return os.path.join(data_folder, FILE_NAME)


def empty():
    return {"app": APP, "kind": KIND, "format": FORMAT, "people": []}


def today():
    return datetime.date.today().isoformat()


def _clean_text(v, limit):
    if not isinstance(v, str):
        return None
    v = " ".join(v.split())
    return v[:limit] if v else None


def _vector(v, dim=None):
    if not isinstance(v, list) or not v or len(v) > MAX_DIM or (dim and len(v) != dim):
        return None
    out = []
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or abs(x) > 1e3:
            return None
        out.append(float(x))
    return out


def validate(data):
    """Returns (library, problems). Never raises for bad content: bad people or samples are left out."""
    problems = []
    lib = empty()
    if not isinstance(data, dict) or data.get("app") != APP or data.get("kind") != KIND:
        return lib, ["This is not a Secure Offline Transcriber voice library."]
    if data.get("format") != FORMAT:
        return lib, [f"Voice library format {data.get('format')!r} is not one this program reads ({FORMAT})."]
    people = data.get("people")
    if not isinstance(people, list):
        return lib, ["The voice library has no list of people."]
    ids = set()
    for i, p in enumerate(people, 1):
        where = f"person {i}"
        if not isinstance(p, dict):
            problems.append(f"{where}: not an object; left out.")
            continue
        name = _clean_text(p.get("name"), MAX_NAME)
        pid = p.get("id")
        model = p.get("model")
        dim = p.get("dim")
        if not name:
            problems.append(f"{where}: no name; left out.")
            continue
        if not isinstance(pid, str) or not PID.match(pid) or pid in ids:
            problems.append(f"{where} ({name}): bad or repeated id; left out.")
            continue
        if not isinstance(model, str) or not SAFE_MODEL.match(model):
            problems.append(f"{where} ({name}): bad voice model name; left out.")
            continue
        if isinstance(dim, bool) or not isinstance(dim, int) or not 1 <= dim <= MAX_DIM:
            problems.append(f"{where} ({name}): bad voiceprint size; left out.")
            continue
        samples = []
        for s in p.get("samples") if isinstance(p.get("samples"), list) else []:
            if not isinstance(s, dict):
                continue
            v = _vector(s.get("v"), dim)
            sec = s.get("s")
            if v is None or isinstance(sec, bool) or not isinstance(sec, (int, float)) or not 0 < sec < 1e6:
                problems.append(f"{where} ({name}): a damaged voiceprint was left out.")
                continue
            d = s.get("date") if isinstance(s.get("date"), str) and DATE.match(s.get("date")) else ""
            samples.append({"v": v, "s": float(sec), "date": d})
        if not samples:
            problems.append(f"{where} ({name}): no usable voiceprint; left out.")
            continue
        person = {"id": pid, "name": name, "note": _clean_text(p.get("note"), MAX_NOTE) or "", "model": model,
                  "dim": dim, "samples": samples[-MAX_SAMPLES:]}
        for k in ("consent", "created", "updated", "last_matched"):
            v = p.get(k)
            person[k] = v if isinstance(v, str) and DATE.match(v) else ""
        if not person["consent"]:
            problems.append(f"{where} ({name}): no consent date recorded.")
        ids.add(pid)
        lib["people"].append(person)
    return lib, problems


def load(path):
    """(library, problems). A missing file is an empty library."""
    if not os.path.isfile(path):
        return empty(), []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as ex:
        return empty(), [f"The voice library could not be read ({ex}). It was not changed; saving a voice will "
                         "replace it."]
    return validate(data)


def save(lib, path):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    data = {"app": APP, "kind": KIND, "format": FORMAT,
            "note": "Voiceprints (numbers only, no audio) saved with the consent of each person. Kept on this "
                    "computer only.",
            "people": [_out(p) for p in lib["people"]]}
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".voices", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _out(p):
    return {"id": p["id"], "name": p["name"], "note": p.get("note", ""), "model": p["model"], "dim": p["dim"],
            "consent": p.get("consent", ""), "created": p.get("created", ""), "updated": p.get("updated", ""),
            "last_matched": p.get("last_matched", ""),
            "samples": [{"v": [round(x, 6) for x in s["v"]], "s": round(s["s"], 1), "date": s.get("date", "")}
                        for s in p["samples"]]}


# ------------------------------------------------------------------ people
def person_centroid(p):
    return speakers.centroid([s["v"] for s in p["samples"]], [s["s"] for s in p["samples"]])


def for_model(lib, model):
    """People whose voiceprints come from this voice model, with their averaged voiceprint."""
    return [{"id": p["id"], "name": p["name"], "centroid": person_centroid(p)}
            for p in lib["people"] if p["model"] == model]


def find(lib, pid):
    for p in lib["people"]:
        if p["id"] == pid:
            return p
    return None


def find_name(lib, name, model):
    key = " ".join(name.split()).casefold()
    for p in lib["people"]:
        if p["model"] == model and p["name"].casefold() == key:
            return p
    return None


def add_voice(lib, name, vector, seconds, model, consent_date=None, note="", pid=None):
    """Add one recording's voiceprint for a person (new or existing). Returns the person."""
    name = _clean_text(name, MAX_NAME)
    v = _vector(list(vector))
    if not name or v is None or not seconds or seconds <= 0:
        raise ValueError("A voice needs a name, a voiceprint and some speech.")
    if not SAFE_MODEL.match(model or ""):
        raise ValueError("Unknown voice model.")
    p = find(lib, pid) if pid else None
    if p is None:
        p = find_name(lib, name, model)
    if p is not None and (p["model"] != model or p["dim"] != len(v)):
        p = None  # another voice model: a separate entry
    if p is None:
        if not consent_date:
            raise ValueError("A new person needs a consent date.")
        p = {"id": "p-" + uuid.uuid4().hex[:16], "name": name, "note": _clean_text(note, MAX_NOTE) or "",
             "model": model, "dim": len(v), "consent": consent_date, "created": today(), "updated": today(),
             "last_matched": "", "samples": []}
        lib["people"].append(p)
    elif consent_date:
        p["consent"] = consent_date
    p["samples"].append({"v": speakers.norm(v), "s": float(seconds), "date": today()})
    p["samples"] = p["samples"][-MAX_SAMPLES:]
    p["updated"] = today()
    if note:
        p["note"] = _clean_text(note, MAX_NOTE) or p.get("note", "")
    return p


def rename(lib, pid, name):
    p = find(lib, pid)
    name = _clean_text(name, MAX_NAME)
    if p and name:
        p["name"] = name
    return p


def set_note(lib, pid, note):
    p = find(lib, pid)
    if p:
        p["note"] = _clean_text(note, MAX_NOTE) or ""
    return p


def delete(lib, pid):
    n = len(lib["people"])
    lib["people"] = [p for p in lib["people"] if p["id"] != pid]
    return len(lib["people"]) < n


def delete_all(lib):
    lib["people"] = []


def merge(lib, keep, drop):
    """Join two entries of the same person (same voice model). The newest samples are kept."""
    a, b = find(lib, keep), find(lib, drop)
    if not a or not b or a is b or a["model"] != b["model"] or a["dim"] != b["dim"]:
        raise ValueError("Only two entries made with the same voice model can be merged.")
    a["samples"] = sorted(a["samples"] + b["samples"], key=lambda s: s.get("date", ""))[-MAX_SAMPLES:]
    a["consent"] = max(a.get("consent", ""), b.get("consent", ""))
    a["updated"] = today()
    delete(lib, drop)
    return a


def touch_matched(lib, pids):
    for pid in pids:
        p = find(lib, pid)
        if p:
            p["last_matched"] = today()


def speech_seconds(p):
    return sum(s["s"] for s in p["samples"])


def export_people(lib, pids, path):
    sub = {"app": APP, "kind": KIND, "format": FORMAT, "people": [_out(p) for p in lib["people"] if p["id"] in pids]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sub, f, ensure_ascii=False, indent=1)
    return len(sub["people"])


def import_people(lib, path):
    """Add the people of another voice library file. A person with the same name and voice model gets the samples
    added; ids are new. Returns (added, updated, problems)."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as ex:
        return 0, 0, [f"The file cannot be read: {ex}"]
    other, problems = validate(data)
    added = updated = 0
    for p in other["people"]:
        q = find_name(lib, p["name"], p["model"])
        if q and q["dim"] == p["dim"]:
            q["samples"] = (q["samples"] + p["samples"])[-MAX_SAMPLES:]
            q["consent"] = q.get("consent") or p.get("consent", "")
            q["updated"] = today()
            updated += 1
        else:
            np_ = dict(p, id="p-" + uuid.uuid4().hex[:16])
            lib["people"].append(np_)
            added += 1
    return added, updated, problems
