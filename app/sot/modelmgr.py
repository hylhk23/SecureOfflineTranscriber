"""Model manager: catalogue, scanning, downloads, other locations, packs."""
import hashlib
import json
import os
import shutil
import sys
import threading
import time
import urllib.request
import zipfile

from . import paths
from .engines import detect_type, kind_languages

MANIFEST = ".sot-model.json"  # written into each downloaded model folder: which list entry and revision it holds
KIND_LABEL = {"whisper": "Whisper", "sensevoice": "SenseVoiceSmall", "parakeet": "Parakeet", "moonshine": "Moonshine"}
LANG_NAMES = {"en": "English", "fr": "French", "yue": "Cantonese", "zh": "Mandarin", "auto": "Auto-detect"}


def load_catalog():
    """Models of the list in use (see sot.modellist)."""
    from .modellist import load
    return load()["data"]["models"]


def read_manifest(folder):
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def human_size(n):
    if n >= 1e9:
        return f"{n / 1e9:.1f} GB"
    if n >= 1e6:
        return f"{n / 1e6:.0f} MB"
    return f"{n / 1e3:.0f} KB"


def ram_gb():
    try:
        if os.name == "nt":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MS()
            m.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 2 ** 30, m.ullAvailPhys / 2 ** 30
        if sys.platform == "darwin":
            return _mac_ram()
        with open("/proc/meminfo") as f:
            info = {l.split(":")[0]: int(l.split()[1]) for l in f}
        return info["MemTotal"] / 2 ** 20, info.get("MemAvailable", 0) / 2 ** 20
    except Exception:
        return 8.0, 4.0


def _mac_ram():
    """macOS: total memory from sysconf; free = free + inactive + speculative pages from vm_stat."""
    import re
    import subprocess
    total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        page = int(re.search(r"page size of (\d+) bytes", out).group(1))
        counts = {}
        for line in out.splitlines()[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                if v.strip().rstrip(".").isdigit():
                    counts[k.strip()] = int(v.strip().rstrip("."))
        avail = page * sum(counts.get(k, 0) for k in ("Pages free", "Pages inactive", "Pages speculative"))
    except Exception:
        avail = total / 2
    return total / 2 ** 30, avail / 2 ** 30


def free_disk(folder):
    p = folder
    while p and not os.path.exists(p):
        p = os.path.dirname(p)
    try:
        return shutil.disk_usage(p or ".").free
    except OSError:
        return 0


class ModelInfo:
    def __init__(self, key, name, kind, folder, languages, source, status, size=0, entry=None, location=""):
        self.key, self.name, self.kind, self.folder = key, name, kind, folder
        self.languages, self.source, self.status = languages, source, status
        self.size, self.entry, self.location = size, entry, location
        self.catalog_id = entry["id"] if entry else None
        self.progress = 0.0

    @property
    def installed(self):
        return self.status in ("Installed", "Update available")

    @property
    def short(self):
        """Used in file names ({model})."""
        if self.catalog_id:
            return self.catalog_id
        return os.path.basename(self.folder.rstrip("\\/")) or self.kind


class ModelManager:
    def __init__(self, settings):
        self.settings = settings
        self.downloads = {}  # id -> (thread, stop_event, state dict)
        self.models = {}
        self.before_update = None  # set by the window: unload the model before its files are replaced
        self.reload_list()

    def reload_list(self):
        from .modellist import load
        self.list_info = load()
        self.listdata = self.list_info["data"]
        self.catalog = self.listdata["models"]
        self.by_id = {e["id"]: e for e in self.catalog}

    def automatic_order(self, language):
        """The model order Automatic tries: your own order from Options, else the model list's."""
        own = (self.settings.get("transcription.per_language") or {}).get(language)
        return list(own) if own else list(self.listdata.get("automatic", {}).get(language, []))

    # ---------------------------------------------------------------- scanning
    def folder_for(self, entry):
        return os.path.join(self.settings.download_folder(), entry["id"])

    def _status_of(self, entry):
        """Installed / Update available (an older revision that still works) / Paused / Damaged / Not installed."""
        folder = self.folder_for(entry)
        if not os.path.isdir(folder):
            return "Not installed", 0
        done = 0
        complete = True
        partial = False
        other = False
        for f in entry["files"]:
            p = os.path.join(folder, f["name"])
            if os.path.isfile(p) and os.path.getsize(p) == f["size"]:
                done += f["size"]
            else:
                complete = False
                if os.path.isfile(p + ".part"):
                    partial = True
                    done += os.path.getsize(p + ".part")
                elif os.path.isfile(p):
                    other = True
        man = read_manifest(folder)
        if complete:
            if man and man.get("revision") and man["revision"] != entry["revision"]:
                return "Update available", entry["size"]
            if not man:  # downloaded by version 2.1.0 or copied in: record which revision these files are
                self._write_manifest(folder, entry)
            return "Installed", entry["size"]
        if partial:
            return "Paused", done
        if detect_type(folder)[0] == entry["kind"]:
            return "Update available", done  # the files of an earlier revision: still usable
        if other or done:
            return "Damaged", done
        return "Not installed", 0

    def _write_manifest(self, folder, e):
        try:
            with open(os.path.join(folder, MANIFEST), "w", encoding="utf-8") as fh:
                json.dump({"id": e["id"], "revision": e["revision"], "repo": e["repo"],
                           "list_version": self.listdata.get("list_version"),
                           "files": [{"name": f["name"], "sha256": f["sha256"]} for f in e["files"]]}, fh, indent=1)
        except OSError:
            pass  # a read-only folder: the model still works

    def match_catalog(self, folder, kind):
        """Identify a found folder as a catalogue model by the size of its main file."""
        for e in self.catalog:
            if e["kind"] != kind:
                continue
            main = max(e["files"], key=lambda f: f["size"])
            p = os.path.join(folder, main["name"])
            if os.path.isfile(p) and os.path.getsize(p) == main["size"]:
                return e
        return None

    def scan_location(self, loc):
        found = []
        cands = [loc]
        try:
            cands += [os.path.join(loc, d) for d in sorted(os.listdir(loc)) if os.path.isdir(os.path.join(loc, d))]
        except OSError:
            return None  # offline
        for c in cands:
            kind, _ = detect_type(c)
            if kind:
                found.append((c, kind))
        return found

    def scan(self):
        models = {}
        for e in self.catalog:
            status, done = self._status_of(e)
            st = self.downloads.get(e["id"])
            if st and st[0].is_alive():
                status = "Downloading"
            m = ModelInfo(e["id"], e["name"], e["kind"], self.folder_for(e), e["languages"], "download",
                          status, e["size"], e)
            m.progress = done / e["size"] if e["size"] else 0
            models[m.key] = m
        # model folders in the download folder that the list no longer names stay usable (and deletable)
        dl = self.settings.download_folder()
        try:
            extra = sorted(d for d in os.listdir(dl) if d not in self.by_id and os.path.isdir(os.path.join(dl, d)))
        except OSError:
            extra = []
        for d in extra:
            folder = os.path.join(dl, d)
            kind, _ = detect_type(folder)
            if not kind:
                continue
            m = ModelInfo("dl:" + d, f"{d} (not in the model list)", kind, folder, kind_languages(kind, folder),
                          "download", "Installed", size=_folder_size(folder))
            models[m.key] = m
        cache = dict(self.settings.get("transcription.location_cache") or {})
        for loc in self.settings.locations():
            found = self.scan_location(loc)
            stored = paths.to_stored(loc)
            if found is None:
                for item in cache.get(stored, []):
                    m = ModelInfo("loc:" + item["folder"], item["name"], item["kind"], item["folder"],
                                  item["languages"], "location", "Location offline",
                                  location=loc, entry=self.by_id.get(item.get("catalog_id")))
                    models[m.key] = m
                continue
            items = []
            for folder, kind in found:
                entry = self.match_catalog(folder, kind)
                name = f"{entry['name'] if entry else os.path.basename(folder)} ({os.path.basename(folder)})" \
                    if entry else f"{os.path.basename(folder)} ({KIND_LABEL[kind]})"
                langs = entry["languages"] if entry else kind_languages(kind, folder)
                m = ModelInfo("loc:" + folder, name, kind, folder, langs, "location", "Installed",
                              size=_folder_size(folder), entry=entry, location=loc)
                models[m.key] = m
                items.append({"folder": folder, "name": name, "kind": kind, "languages": langs,
                              "catalog_id": entry["id"] if entry else None})
            cache[stored] = items
        self.settings.set("transcription.location_cache", cache)
        self.models = models
        return models

    def installed(self):
        return [m for m in self.models.values() if m.installed]

    # ---------------------------------------------------------------- choosing a model
    def find(self, key):
        return self.models.get(key)

    def by_catalog(self, cid):
        m = self.models.get(cid)
        if m and m.installed:
            return m
        for m in self.models.values():
            if m.source == "location" and m.installed and m.catalog_id == cid:
                return m
        return None

    def candidates(self, language):
        """Installed models for a language in Automatic order, then any that fit."""
        order = []
        for cid in self.automatic_order(language):
            m = self.by_catalog(cid)
            if m and m not in order:
                order.append(m)
        for m in sorted(self.installed(), key=lambda x: x.size):
            if language in m.languages and m not in order:
                order.append(m)
        return order

    def lid_model(self):
        """Smallest installed Whisper for language detection, else SenseVoiceSmall."""
        whispers = sorted([m for m in self.installed() if m.kind == "whisper" and len(m.languages) > 1],
                          key=lambda m: m.size)
        if whispers:
            return whispers[0]
        sv = [m for m in self.installed() if m.kind == "sensevoice"]
        return sv[0] if sv else None

    def recommended(self, cantonese_mandarin=False):
        """Recommended set for this PC's memory, from the model list (version 2.1.4: English first).
        With cantonese_mandarin, the list's models for Cantonese and Mandarin are added (SenseVoiceSmall)."""
        rec = self.listdata["recommended"]
        total, _ = ram_gb()
        out = list(rec["at_or_above"] if total >= float(rec.get("ram_gb", 12)) else rec["below"])
        if cantonese_mandarin:
            out += [m for m in self.recommended_cantonese_mandarin() if m not in out]
        return out

    def recommended_cantonese_mandarin(self):
        """Models the list adds for people who also transcribe Cantonese or Mandarin (may be empty)."""
        return [m for m in self.listdata["recommended"].get("cantonese_mandarin", []) if m in self.by_id]

    # ---------------------------------------------------------------- downloads
    def opener(self):
        if self.settings.get("advanced.proxy") == "none":
            return urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return urllib.request.build_opener()

    def start_download(self, cid, on_done=None):
        if self.settings.get("advanced.offline"):
            raise RuntimeError("Offline Mode is on. Turn it off in the Options menu to download.")
        if cid in self.downloads and self.downloads[cid][0].is_alive():
            return
        e = self.by_id[cid]
        status, have = self._status_of(e)
        need = e["size"] - (have if status != "Update available" else 0)
        if status == "Update available" and self.before_update:
            self.before_update()  # Windows cannot replace a model file that is loaded
        if free_disk(self.settings.download_folder()) - need < 1e9:
            raise RuntimeError(f"Not enough disk space: {e['name']} needs {human_size(need)} and at least "
                               f"1 GB must stay free on the drive.")
        stop = threading.Event()
        state = {"done": 0, "total": e["size"], "error": None, "finished": False}
        t = threading.Thread(target=self._download, args=(e, stop, state, on_done), daemon=True)
        self.downloads[cid] = (t, stop, state)
        t.start()

    def pause_download(self, cid):
        if cid in self.downloads:
            self.downloads[cid][1].set()

    def download_state(self, cid):
        d = self.downloads.get(cid)
        return d[2] if d else None

    def _download(self, e, stop, state, on_done):
        folder = self.folder_for(e)
        try:
            os.makedirs(folder, exist_ok=True)
            opener = self.opener()
            base_done = 0
            man = read_manifest(folder) or {}
            same_rev = man.get("revision") == e["revision"]
            for f in e["files"]:
                dest = os.path.join(folder, f["name"])
                if os.path.isfile(dest) and os.path.getsize(dest) == f["size"] and \
                        (same_rev or file_sha256(dest) == f["sha256"]):
                    base_done += f["size"]
                    state["done"] = base_done
                    continue
                part = dest + ".part"
                have = os.path.getsize(part) if os.path.isfile(part) else 0
                if have > f["size"]:
                    os.remove(part)
                    have = 0
                if have < f["size"]:
                    url = f"https://huggingface.co/{e['repo']}/resolve/{e['revision']}/{f['name']}"
                    req = urllib.request.Request(url, headers={"User-Agent": "SecureOfflineTranscriber/2.1"})
                    if have:
                        req.add_header("Range", f"bytes={have}-")
                    with opener.open(req, timeout=60) as r:
                        if have and r.status != 206:  # server ignored Range: start again
                            have = 0
                        with open(part, "ab" if have else "wb") as out:
                            while True:
                                if stop.is_set():
                                    state["error"] = "Paused"
                                    return
                                chunk = r.read(1 << 20)
                                if not chunk:
                                    break
                                out.write(chunk)
                                have += len(chunk)
                                state["done"] = base_done + have
                # verify
                h = hashlib.sha256()
                with open(part, "rb") as fh:
                    for chunk in iter(lambda: fh.read(4 << 20), b""):
                        h.update(chunk)
                if h.hexdigest() != f["sha256"] or os.path.getsize(part) != f["size"]:
                    os.remove(part)
                    raise RuntimeError(f"{f['name']} failed its SHA-256 check and was deleted. Try again.")
                os.replace(part, dest)
                base_done += f["size"]
                state["done"] = base_done
            self._write_manifest(folder, e)
            state["finished"] = True
        except Exception as ex:  # network errors, disk errors
            state["error"] = str(ex)
        finally:
            if on_done:
                on_done(e["id"], state)

    def delete(self, m):
        if m.source != "download":
            raise RuntimeError("Models in other locations are never deleted. Remove the location instead.")
        if os.path.isdir(m.folder):
            shutil.rmtree(m.folder)

    # ---------------------------------------------------------------- locations and folders
    def add_location(self, folder):
        found = self.scan_location(folder)
        if found is None:
            raise RuntimeError("That folder cannot be opened.")
        locs = self.settings.get("transcription.locations")
        stored = paths.to_stored(folder)
        if stored not in locs:
            self.settings.set("transcription.locations", locs + [stored])
        return found

    def remove_location(self, folder):
        stored = paths.to_stored(folder)
        self.settings.set("transcription.locations",
                          [l for l in self.settings.get("transcription.locations") if l != stored])
        cache = dict(self.settings.get("transcription.location_cache") or {})
        cache.pop(stored, None)
        self.settings.set("transcription.location_cache", cache)

    def add_model_folder(self, folder, copy, progress=None):
        """Add one model folder. Returns (kind, None) or (None, missing files)."""
        kind, info = detect_type(folder)
        if not kind:
            return None, info
        if copy:
            entry = self.match_catalog(folder, kind)
            name = entry["id"] if entry else os.path.basename(folder.rstrip("\\/"))
            dest = os.path.join(self.settings.download_folder(), name)
            if os.path.exists(dest):
                raise RuntimeError(f"{dest} already exists.")
            copy_tree(folder, dest, progress)
            if not entry:  # not a catalogue model: keep it visible through a location
                self.add_location(dest)
        else:
            self.add_location(folder)
        return kind, None

    def move_download_folder(self, new, move, progress=None):
        old = self.settings.download_folder()
        if move and os.path.isdir(old) and os.path.normcase(os.path.abspath(old)) != os.path.normcase(os.path.abspath(new)):
            copy_tree(old, new, progress)
            shutil.rmtree(old)  # only after every file was copied and checked
        else:
            os.makedirs(new, exist_ok=True)
        self.settings.set("transcription.download_folder", paths.to_stored(new))

    # ---------------------------------------------------------------- packs
    def export_pack(self, keys, zip_path, progress=None):
        manifest = {"app": "SecureOfflineTranscriber", "models": []}
        files = []
        for k in keys:
            m = self.models[k]
            name = m.short
            entry_files = []
            for fn in sorted(os.listdir(m.folder)):
                p = os.path.join(m.folder, fn)
                if os.path.isfile(p) and not fn.endswith(".part"):
                    entry_files.append({"name": fn, "sha256": file_sha256(p), "size": os.path.getsize(p)})
                    files.append((p, f"{name}/{fn}"))
            manifest["models"].append({"folder": name, "kind": m.kind, "catalog_id": m.catalog_id,
                                       "files": entry_files})
        total = sum(os.path.getsize(p) for p, _ in files) or 1
        done = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
            z.writestr("manifest.json", json.dumps(manifest, indent=1))
            for p, arc in files:
                z.write(p, arc)
                done += os.path.getsize(p)
                if progress:
                    progress(done / total)

    def import_pack(self, zip_path, progress=None):
        dest_root = self.settings.download_folder()
        with zipfile.ZipFile(zip_path) as z:
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("app") != "SecureOfflineTranscriber":
                raise RuntimeError("This is not a Secure Offline Transcriber model pack.")
            items = [(m, f) for m in manifest["models"] for f in m["files"]]
            total = sum(f["size"] for _, f in items) or 1
            done = 0
            for m, f in items:
                target_dir = os.path.join(dest_root, m["folder"])
                os.makedirs(target_dir, exist_ok=True)
                target = os.path.join(target_dir, f["name"])
                with z.open(f"{m['folder']}/{f['name']}") as src, open(target + ".part", "wb") as out:
                    shutil.copyfileobj(src, out, 4 << 20)
                if file_sha256(target + ".part") != f["sha256"]:
                    os.remove(target + ".part")
                    raise RuntimeError(f"{f['name']} in the pack is damaged (SHA-256 mismatch).")
                os.replace(target + ".part", target)
                done += f["size"]
                if progress:
                    progress(done / total)
            for m in manifest["models"]:
                if not m.get("catalog_id") or m["folder"] != m["catalog_id"]:
                    self.add_location(os.path.join(dest_root, m["folder"]))
        return [m["folder"] for m in manifest["models"]]


def file_sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _folder_size(folder):
    total = 0
    for root, _, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def copy_tree(src, dst, progress=None):
    """Copy with progress, then check sizes; raise before anything is deleted."""
    items = []
    for root, _, files in os.walk(src):
        for f in files:
            if f.endswith(".part"):
                continue
            s = os.path.join(root, f)
            items.append((s, os.path.join(dst, os.path.relpath(s, src))))
    total = sum(os.path.getsize(s) for s, _ in items) or 1
    done = 0
    for s, d in items:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        if os.path.getsize(s) != os.path.getsize(d):
            raise RuntimeError(f"Copy check failed for {s}; nothing was deleted.")
        done += os.path.getsize(s)
        if progress:
            progress(done / total)
