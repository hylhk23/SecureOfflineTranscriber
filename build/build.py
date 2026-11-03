"""Build the portable Windows package of Secure Offline Transcriber (spec section 13).

Runs on Windows (py -3.13 build.py) or on Linux/macOS: everything is downloaded as Windows x64 files.
No PyInstaller, no self-made .exe: the only programs in the package are python.exe / pythonw.exe from
python.org (signed by the Python Software Foundation) and the unmodified ffmpeg.exe in ffmpeg\\.

    python build.py                       -> dist\\SecureOfflineTranscriber-2.1.0-win64-portable.zip
    python build.py --ffmpeg-zip FILE     use an ffmpeg essentials zip you already downloaded
    python build.py --keep-qt             do not trim unused Qt modules
    python build.py --update              take the newest allowed library versions (writes requirements-lock.txt)
"""
import argparse
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)                       # repository root (app\, tests\, build\)
APP_SRC = os.path.join(SRC, "app")

PY_VERSION = "3.13.17"
PY_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
FFMPEG_VERSION = "9.0.2"
FFMPEG_URL = f"https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-{FFMPEG_VERSION}-essentials_build.zip"
FFMPEG_SHA256 = "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba"

ALLOWED_EXE = {"python/python.exe", "python/pythonw.exe", "ffmpeg/ffmpeg.exe"}

# Qt parts a Widgets app never loads (removed only when the smoke test passes on Windows; see README)
QT_TRIM = [r"Qt6Quick.*\.dll", r"Qt6Qml.*\.dll", r"Qt6Designer.*\.dll", r"Qt6Pdf.*\.dll", r"Qt6ShaderTools\.dll",
           r"Qt6LabsQml.*\.dll", r"Qt6Help\.dll", r"Qt6UiTools\.dll", r"opengl32sw\.dll", r"Qt6VirtualKeyboard.*\.dll",
           r"QtQuick.*\.pyd", r"QtQml\.pyd", r"QtDesigner\.pyd", r"QtPdf.*\.pyd", r"QtUiTools\.pyd", r"QtHelp\.pyd",
           r"QtQuick.*\.pyi", r"QtQml\.pyi", r"QtDesigner\.pyi", r"QtPdf.*\.pyi", r"QtUiTools\.pyi", r"QtHelp\.pyi",
           r"Qt6Labs.*\.dll", r"Qt6Lottie.*\.dll", r"pyside6qml.*\.dll", r"QtLabs.*\.py[di]", r"QtQmlFeatures\.py[di]",
           r"Qt6Sql\.dll", r"QtSql\.py[di]", r"Qt6Test\.dll", r"QtTest\.py[di]"]
QT_TRIM_FILES = ["plugins/imageformats/qpdf.dll"]
QT_TRIM_DIRS = ["qml", "translations", "typesystems", "include", "glue", "scripts", "metatypes", "doc",
                "resources", "plugins/qmltooling", "plugins/qmllint", "plugins/designer", "plugins/virtualkeyboard",
                "plugins/platforminputcontexts", "plugins/assetimporters", "plugins/scenegraph",
                "plugins/sqldrivers", "plugins/vectorimageformats"]


def log(*a):
    print(*a, flush=True)


def download(url, dest, sha256=None):
    if not os.path.isfile(dest):
        log(f"  downloading {url}")
        tmp = dest + ".part"
        with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        os.replace(tmp, dest)
    if sha256:
        got = sha256_file(dest)
        if got != sha256:
            os.remove(dest)
            sys.exit(f"SHA-256 mismatch for {dest}: {got}")
    return dest


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p, root):
    return os.path.relpath(p, root).replace("\\", "/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(SRC, "dist"))
    ap.add_argument("--cache", default=os.path.join(SRC, "build", "cache"))
    ap.add_argument("--ffmpeg-zip")
    ap.add_argument("--keep-qt", action="store_true")
    ap.add_argument("--no-zip", action="store_true")
    ap.add_argument("--update", action="store_true", help="resolve requirements.txt again instead of the lock file")
    a = ap.parse_args()
    sys.path.insert(0, APP_SRC)
    from sot import VERSION
    name = f"SecureOfflineTranscriber-{VERSION}"
    os.makedirs(a.cache, exist_ok=True)
    os.makedirs(a.out, exist_ok=True)
    stage = os.path.join(a.out, name)
    if os.path.exists(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)

    log("1. Python embeddable package")
    pyzip = download(PY_URL, os.path.join(a.cache, os.path.basename(PY_URL)))
    py = os.path.join(stage, "python")
    with zipfile.ZipFile(pyzip) as z:
        z.extractall(py)
    pth = [f for f in os.listdir(py) if f.endswith("._pth")][0]
    zipname = [f for f in os.listdir(py) if re.match(r"python\d+\.zip$", f)][0]
    with open(os.path.join(py, pth), "w", newline="\r\n") as f:
        f.write(f"{zipname}\n.\nLib\\site-packages\n..\\app\n\n# site is imported so .pth files in site-packages work\nimport site\n")

    log("2. Libraries (Windows x64 wheels for CPython 3.13, binary only)")
    sp = os.path.join(py, "Lib", "site-packages")
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-compile",
           "--target", sp, "--platform", "win_amd64", "--python-version", "3.13", "--implementation", "cp",
           "--only-binary=:all:"]
    lock = os.path.join(HERE, "requirements-lock.txt")
    if os.path.isfile(lock) and not a.update:
        cmd += ["--no-deps", "-r", lock]  # the exact versions of the tested build
    else:
        cmd += ["-r", os.path.join(HERE, "requirements.txt")]
    subprocess.run(cmd, check=True)
    # PyAV is not used (ffmpeg.exe decodes; sot.avstub stands in for faster-whisper's import) and one of its
    # files was blocked by antivirus while unzipping on a user's PC: leave it out.
    for d in os.listdir(sp):
        if d == "av" or d == "av.libs" or re.match(r"av-[\d.]+\.dist-info$", d):
            shutil.rmtree(os.path.join(sp, d))
    pins = []
    for d in sorted(os.listdir(sp), key=str.lower):
        m = re.match(r"(.+)-([^-]+)\.dist-info$", d)
        if m:
            pins.append(f"{m.group(1).replace('_', '-')}=={m.group(2)}")
    with open(lock, "w", newline="\n") as f:
        f.write("# Exact library versions of the last build (python build.py --update re-resolves them)\n"
                + "\n".join(pins) + "\n")

    log("3. Clean-up: scripts, tool programs, developer files")
    for d in ("bin", "Scripts"):  # console-script stubs and duplicate DLLs meant for PATH
        shutil.rmtree(os.path.join(sp, d), ignore_errors=True)
    for root, dirs, files in os.walk(sp, topdown=True):
        for d in [d for d in dirs if d == "__pycache__"]:
            shutil.rmtree(os.path.join(root, d))
            dirs.remove(d)
        for fn in files:
            p = os.path.join(root, fn)
            if fn.lower().endswith((".exe", ".lib", ".pdb")):
                os.remove(p)
    shutil.rmtree(os.path.join(sp, "sherpa_onnx", "include"), ignore_errors=True)
    if not a.keep_qt:
        qt = os.path.join(sp, "PySide6")
        pats = [re.compile(p, re.I) for p in QT_TRIM]
        n = 0
        for fn in os.listdir(qt):
            if any(p.fullmatch(fn) for p in pats):
                os.remove(os.path.join(qt, fn))
                n += 1
        for d in QT_TRIM_DIRS:
            shutil.rmtree(os.path.join(qt, d), ignore_errors=True)
        for f in QT_TRIM_FILES:
            if os.path.isfile(os.path.join(qt, f)):
                os.remove(os.path.join(qt, f))
        log(f"   Qt: removed {n} unused module files and developer folders")

    log("4. Program")
    app = os.path.join(stage, "app")
    shutil.copytree(APP_SRC, app, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(app, "BUILD.txt"), "w") as f:
        f.write(datetime.date.today().isoformat())
    for d in ("data", "models"):
        os.makedirs(os.path.join(stage, d))
    with open(os.path.join(stage, "models", "README.txt"), "w", newline="\r\n") as f:
        f.write("Downloaded speech models are stored here (Options > Transcription > Model download folder).\n")
    with open(os.path.join(stage, "data", "README.txt"), "w", newline="\r\n") as f:
        f.write("Settings, logs, temporary files and the job journal are kept here. Nothing here leaves this PC.\n")

    log("5. FFmpeg (unmodified ffmpeg.exe from gyan.dev essentials build)")
    fz = a.ffmpeg_zip or download(FFMPEG_URL, os.path.join(a.cache, os.path.basename(FFMPEG_URL)), FFMPEG_SHA256)
    if a.ffmpeg_zip and sha256_file(fz) != FFMPEG_SHA256:
        log("   note: this ffmpeg zip is not the pinned 9.0.2 essentials build")
    ff = os.path.join(stage, "ffmpeg")
    os.makedirs(ff)
    with zipfile.ZipFile(fz) as z:
        for n in z.namelist():
            base = n.rsplit("/", 1)[-1]
            if n.endswith("/bin/ffmpeg.exe") or base in ("LICENSE", "README.txt") and n.count("/") == 1:
                target = {"ffmpeg.exe": "ffmpeg.exe", "LICENSE": "LICENSE.txt", "README.txt": "README-ffmpeg-build.txt"}[base]
                with z.open(n) as s, open(os.path.join(ff, target), "wb") as d:
                    shutil.copyfileobj(s, d)
    with open(os.path.join(ff, "SOURCE.txt"), "w", newline="\r\n") as f:
        f.write(f"ffmpeg.exe is FFmpeg {FFMPEG_VERSION}, the essentials build by Gyan Doshi, distributed unmodified.\n"
                f"Download: {FFMPEG_URL}\nSHA-256 of that zip: {FFMPEG_SHA256}\n"
                f"SHA-256 of ffmpeg.exe: {sha256_file(os.path.join(ff, 'ffmpeg.exe'))}\n"
                "It is licensed under the GNU GPL version 3 (see LICENSE.txt). The complete corresponding source code\n"
                f"is available from https://ffmpeg.org/releases/ffmpeg-{FFMPEG_VERSION}.tar.xz and from the build's\n"
                "page https://www.gyan.dev/ffmpeg/builds/ ; on request we provide it for three years.\n"
                "Secure Offline Transcriber runs ffmpeg.exe as a separate program and does not link to it.\n")

    log("6. Launchers and documents")
    for fn in ("run.bat", "run-debug.bat", "create-shortcut.bat", "smoke-test.bat", "verify-files.bat", "README.txt",
               "LICENSE.txt"):
        shutil.copy(os.path.join(HERE, "package", fn), os.path.join(stage, fn))
    write_third_party(stage, sp)

    log("7. Program inventory (antivirus safety, section 13)")
    exes = []
    for root, _, files in os.walk(stage):
        for fn in files:
            if fn.lower().endswith((".exe", ".com", ".scr", ".msi", ".ps1", ".vbs")):
                exes.append(rel(os.path.join(root, fn), stage))
    extra = sorted(set(exes) - ALLOWED_EXE)
    log("   programs:", ", ".join(sorted(exes)))
    if extra:
        sys.exit(f"Unexpected programs in the package: {extra}")

    log("8. SHA256SUMS.txt")
    lines = []
    for root, _, files in os.walk(stage):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            lines.append(f"{sha256_file(p)}  {rel(p, stage)}")
    lines.sort(key=lambda l: l.split("  ", 1)[1])
    with open(os.path.join(stage, "SHA256SUMS.txt"), "w", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")

    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(stage) for f in fs)
    log(f"   package folder: {size / 1e6:.0f} MB")
    if a.no_zip:
        return
    log("9. Zip")
    zp = os.path.join(a.out, f"{name}-win64-portable.zip")
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(stage):
            dirs.sort()
            for fn in sorted(files):
                p = os.path.join(root, fn)
                z.write(p, os.path.join(name, rel(p, stage)))
            if not files and not dirs:
                z.writestr(os.path.join(name, rel(root, stage)) + "/", "")
    log(f"   {zp}: {os.path.getsize(zp) / 1e6:.0f} MB, SHA-256 {sha256_file(zp)}")


def write_third_party(stage, sp):
    """THIRD-PARTY-LICENSES.txt: every library's licence file, plus model licences (FR-HELP-04)."""
    out = [open(os.path.join(HERE, "package", "THIRD-PARTY-HEADER.txt"), encoding="utf-8").read().rstrip(), ""]
    for d in sorted(os.listdir(sp), key=str.lower):
        if not d.endswith(".dist-info"):
            continue
        meta = open(os.path.join(sp, d, "METADATA"), encoding="utf-8", errors="replace").read()
        nm = re.search(r"^Name: (.+)$", meta, re.M).group(1).strip()
        ver = re.search(r"^Version: (.+)$", meta, re.M).group(1).strip()
        lic = (re.search(r"^License-Expression: (.+)$", meta, re.M) or re.search(r"^License: (.+)$", meta, re.M))
        out.append("=" * 78)
        out.append(f"{nm} {ver}" + (f"  -  {lic.group(1).strip()}" if lic and len(lic.group(1)) < 80 else ""))
        out.append("=" * 78)
        texts = []
        for root, _, files in os.walk(os.path.join(sp, d)):
            for fn in sorted(files):
                if re.match(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", fn, re.I):
                    texts.append(open(os.path.join(root, fn), encoding="utf-8", errors="replace").read().strip())
        out.append("\n\n".join(texts) if texts else "(licence named above; no licence file in the package)")
        out.append("")
    with open(os.path.join(stage, "THIRD-PARTY-LICENSES.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    main()
