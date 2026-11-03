"""Make or update a model list (models.json) for Secure Offline Transcriber.

You edit the list by hand (add a model, change a repo, remove one), then run this tool. It looks each model up
on huggingface.co and fills in what is tedious and must be exact: the commit (revision), every file's size
and SHA-256, the model size, and a new list_version. Then it checks the whole list exactly as the program does
on import, and shows what changed. Give the result to users: Options > Transcription > Import model list...

    py -3 update-model-list.py                       update ..\\..\\app\\models.json in place (backup: .bak)
    py -3 update-model-list.py my-list.json          update another file
    py -3 update-model-list.py --refresh whisper-small sensevoice-small-int8
                                                     move these models to the newest commit of their repo
    py -3 update-model-list.py --refresh-all         move every model to the newest commit
    py -3 update-model-list.py --out new.json        write somewhere else
    py -3 update-model-list.py --check               only check the list, change nothing

In a new or changed entry you may write  "revision": "main"  and  "files": ["model.bin", "config.json", ...]
(names only); the tool replaces them with the commit id and full file details.
Needs Python 3.9+ and internet. Uses only Hugging Face's public API; nothing is uploaded.
"""
import argparse
import copy
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.normpath(os.path.join(HERE, "..", "..", "app"))
HF = "https://huggingface.co"
UA = {"User-Agent": "SecureOfflineTranscriber-model-list-tool"}


def get_json(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return json.load(r)


def resolve_commit(repo, rev):
    data = get_json(f"{HF}/api/models/{repo}/revision/{urllib.parse.quote(rev or 'main', safe='')}")
    return data["sha"]


def tree(repo, sha):
    items = get_json(f"{HF}/api/models/{repo}/tree/{sha}?recursive=true")
    return {i["path"]: i for i in items if i.get("type") == "file"}


def sha256_url(url):
    h = hashlib.sha256()
    n = 0
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r:
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def fill(m, refresh):
    """Fill revision and file details of one entry. Returns a short note of what changed, or ''."""
    repo = m["repo"]
    rev = str(m.get("revision", "") or "main")
    pinned = re.fullmatch(r"[0-9a-f]{40}", rev) is not None
    names = [f if isinstance(f, str) else f.get("name") for f in m.get("files", [])]
    incomplete = any(isinstance(f, str) or not f.get("size") or not f.get("sha256") for f in m.get("files", []))
    if pinned and not refresh and not incomplete:
        return ""
    sha = resolve_commit(repo, "main" if refresh else rev)
    if pinned and sha == rev and not incomplete:
        return ""
    files = tree(repo, sha)
    out = []
    for fn in names:
        if fn not in files:
            raise SystemExit(f"{m['id']}: {fn} is not in {repo} at {sha[:10]}. Files there: "
                             + ", ".join(sorted(files)[:30]))
        info = files[fn]
        if info.get("lfs"):
            out.append({"name": fn, "size": info["lfs"]["size"], "sha256": info["lfs"]["oid"]})
        else:  # small files are not in LFS: download and hash them
            digest, size = sha256_url(f"{HF}/{repo}/resolve/{sha}/{fn}")
            out.append({"name": fn, "size": size, "sha256": digest})
    note = "new commit " + sha[:10] if pinned and sha != rev else "filled in at " + sha[:10]
    m["revision"] = sha
    m["files"] = out
    m["size"] = sum(f["size"] for f in out)
    return note


def next_version(old):
    today = datetime.date.today().strftime("%Y.%m.%d")
    if not str(old).startswith(today):
        return today
    parts = str(old).split(".")
    n = int(parts[3]) + 1 if len(parts) > 3 else 2
    return f"{today}.{n}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", nargs="?", default=os.path.join(APP, "models.json"))
    ap.add_argument("--out")
    ap.add_argument("--refresh", nargs="*", default=[], metavar="ID")
    ap.add_argument("--refresh-all", action="store_true")
    ap.add_argument("--version", help="list_version to write (default: today's date)")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    sys.path.insert(0, APP)
    from sot import modellist

    with open(a.file, encoding="utf-8-sig") as f:
        data = json.load(f)
    before = copy.deepcopy(data)
    if a.check:
        try:
            checked, warn = modellist.validate(data)
        except modellist.ListError as ex:
            print("NOT USABLE:\n" + str(ex))
            return 1
        print(f"OK: model list {checked['list_version']}, {len(checked['models'])} models.")
        for w in warn:
            print("note:", w)
        return 0

    ids = {m.get("id") for m in data.get("models", [])}
    for r in a.refresh:
        if r not in ids:
            raise SystemExit(f"--refresh {r}: no model with that id in the list.")
    changes = []
    for m in data["models"]:
        print(f"  {m['id']:<30} ", end="", flush=True)
        note = fill(m, a.refresh_all or m["id"] in a.refresh)
        print(note or "unchanged")
        if note:
            changes.append(f"{m['id']}: {note}")
    data.setdefault("app", "SecureOfflineTranscriber")
    data.setdefault("format", modellist.FORMAT)
    structural = json.dumps({k: v for k, v in before.items() if k != "list_version"}, sort_keys=True) != \
        json.dumps({k: v for k, v in data.items() if k != "list_version"}, sort_keys=True)
    if a.version:
        data["list_version"] = a.version
    elif changes or structural or not before.get("list_version"):
        data["list_version"] = next_version(before.get("list_version", ""))
    try:
        checked, warn = modellist.validate(data)
    except modellist.ListError as ex:
        print("\nThe list is NOT usable yet - nothing was written:\n" + str(ex))
        return 1
    for w in warn:
        print("note:", w)
    builtin = modellist.read(modellist.BUILTIN)
    added, gone, changed = modellist.diff(builtin, checked)
    out = a.out or a.file
    if out == a.file and os.path.isfile(out):
        shutil.copy(out, out + ".bak")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"\nWrote {out}: model list {data['list_version']}, {len(checked['models'])} models.")
    print(f"Compared with the list built into the program ({builtin.get('list_version')}):")
    for label, names in (("new", added), ("updated", changed), ("removed", gone)):
        if names:
            print(f"  {label + ':':<9} " + ", ".join(names))
    if not (added or changed or gone):
        print("  no model added, updated or removed")
    print("Give this file to users: Options > Transcription > Import model list... (or drop it on the window).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
