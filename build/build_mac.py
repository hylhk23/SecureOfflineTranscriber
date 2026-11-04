"""Build the portable macOS package (Apple silicon) of Secure Offline Transcriber.

Runs on Linux, macOS or Windows (python3 build_mac.py): everything is downloaded as macOS arm64 files.
The package mirrors the Windows one: the same app folder, the same pinned libraries (requirements-lock.txt),
a relocatable Python and an unmodified ffmpeg, plain readable launchers (.command), SHA256SUMS.txt.

    python3 build_mac.py        -> dist/SecureOfflineTranscriber-portable-macos-arm64.zip

Sources (all checked by SHA-256):
    Python 3.13: python-build-standalone (Astral), install_only build for aarch64-apple-darwin
    libraries:   PyPI wheels for macOS arm64 / universal2, CPython 3.13, binary only
    ffmpeg:      the macOS arm64 binary inside the imageio-ffmpeg wheel on PyPI, unmodified
"""
import argparse
import datetime
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from build import APP_SRC, SRC, download, log, rel, sha256_file  # noqa: E402
import macho_check  # noqa: E402

PBS_TAG = "20261026"
PY_VERSION = "3.13.18"
PY_FILE = f"cpython-{PY_VERSION}+{PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz"
PY_URL = ("https://github.com/astral-sh/python-build-standalone/releases/download/"
          f"{PBS_TAG}/cpython-{PY_VERSION}%2B{PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz")
PY_SHA256 = "e5af2fde6a937a0e0c795a0c595c4efb70578d57463a2657c819992b9b9acbac"  # from the release's SHA256SUMS
FFMPEG_WHEEL = "imageio_ffmpeg-0.8.1-py3-none-macosx_12_0_arm64.whl"
FFMPEG_MEMBER = "imageio_ffmpeg/binaries/ffmpeg-macos-aarch64-v9.0"
FFMPEG_VERSION = "9.0"
FFMPEG_DOCS = {  # FFmpeg's own licence files for the 9.0 release (github.com/FFmpeg/FFmpeg, tag n9.0)
    "COPYING.GPLv2": "https://raw.githubusercontent.com/FFmpeg/FFmpeg/n9.0/COPYING.GPLv2",
    "LICENSE.md": "https://raw.githubusercontent.com/FFmpeg/FFmpeg/n9.0/LICENSE.md",
}
PLATFORM = "macosx_14_0_arm64"   # PySide6 6.12 and onnxruntime 1.29 need macOS 14 or later
ALLOWED_PROGRAMS = {"python/bin/python3.13", "ffmpeg/ffmpeg"}
# Qt developer files and tools a Widgets program never uses (the Qt libraries themselves are all kept)
QT_DEV_DIRS = ["include", "typesystems", "glue", "scripts", "doc", "metatypes", "Qt/qml", "Qt/translations",
               "Qt/metatypes", "Qt/libexec", "Qt/include", "Qt/doc",
               # plugins the Windows build also removes; the SQL drivers link to libraries most Macs lack
               "Qt/plugins/sqldrivers", "Qt/plugins/qmltooling", "Qt/plugins/designer", "Qt/plugins/qmllint",
               "Qt/plugins/platforminputcontexts", "Qt/plugins/vectorimageformats", "Qt/plugins/assetimporters",
               "Qt/plugins/scenegraph"]


def _strip_python(py):
    """Keep python3.13 (and its python3 / python links); scripts with build-machine paths go."""
    b = os.path.join(py, "bin")
    for fn in os.listdir(b):
        if fn not in ("python3.13", "python3", "python"):
            os.remove(os.path.join(b, fn))
    sp = os.path.join(py, "lib", "python3.13", "site-packages")
    for fn in os.listdir(sp):
        if fn.startswith("pip"):
            shutil.rmtree(os.path.join(sp, fn))


def write_third_party_mac(stage, sp, header):
    """THIRD-PARTY-LICENSES.txt with the macOS header (Unix line endings); same content rules as build.py."""
    out = [open(header, encoding="utf-8").read().rstrip(), ""]
    for d in sorted(os.listdir(sp), key=str.lower):
        if not d.endswith(".dist-info"):
            continue
        meta = open(os.path.join(sp, d, "METADATA"), encoding="utf-8", errors="replace").read()
        nm = re.search(r"^Name: (.+)$", meta, re.M).group(1).strip()
        ver = re.search(r"^Version: (.+)$", meta, re.M).group(1).strip()
        lic = (re.search(r"^License-Expression: (.+)$", meta, re.M) or re.search(r"^License: (.+)$", meta, re.M))
        out += ["=" * 78, f"{nm} {ver}" + (f"  -  {lic.group(1).strip()}" if lic and len(lic.group(1)) < 80 else ""),
                "=" * 78]
        texts = []
        for root, _, files in os.walk(os.path.join(sp, d)):
            for fn in sorted(files):
                if re.match(r"(LICEN[CS]E|COPYING|NOTICE|AUTHORS)", fn, re.I):
                    texts.append(open(os.path.join(root, fn), encoding="utf-8", errors="replace").read().strip())
        out.append("\n\n".join(texts) if texts else "(licence named above; no licence file in the package)")
        out.append("")
    with open(os.path.join(stage, "THIRD-PARTY-LICENSES.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))


def _zip_tree(stage, zp, top):
    """Zip with Unix permissions (programs stay executable) and symbolic links kept as links."""
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, dirs, files in os.walk(stage):
            dirs.sort()
            for name in sorted(files) + sorted(d for d in dirs if os.path.islink(os.path.join(root, d))):
                p = os.path.join(root, name)
                arc = f"{top}/{rel(p, stage)}"
                if os.path.islink(p):
                    zi = zipfile.ZipInfo(arc)
                    zi.create_system = 3
                    zi.external_attr = (stat.S_IFLNK | 0o755) << 16
                    z.writestr(zi, os.readlink(p))
                else:
                    zi = zipfile.ZipInfo.from_file(p, arc)
                    zi.create_system = 3
                    zi.compress_type = zipfile.ZIP_DEFLATED
                    with open(p, "rb") as f:
                        z.writestr(zi, f.read(), compresslevel=9)
            if not files and not dirs:
                zi = zipfile.ZipInfo(f"{top}/{rel(root, stage)}/")
                zi.create_system = 3
                zi.external_attr = (stat.S_IFDIR | 0o755) << 16
                z.writestr(zi, "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(SRC, "dist"))
    ap.add_argument("--cache", default=os.path.join(HERE, "cache-mac"))
    a = ap.parse_args()
    sys.path.insert(0, APP_SRC)
    from sot import VERSION
    name = "SecureOfflineTranscriber"
    os.makedirs(a.cache, exist_ok=True)
    stage = os.path.join(a.out, "macos", name)
    if os.path.exists(stage):
        shutil.rmtree(stage)
    os.makedirs(stage)

    log(f"1. Python {PY_VERSION} for Apple silicon (python-build-standalone {PBS_TAG})")
    pyt = download(PY_URL, os.path.join(a.cache, PY_FILE), PY_SHA256)
    with tarfile.open(pyt) as t:
        t.extractall(stage, filter="tar")  # the archive's top folder is python/
    py = os.path.join(stage, "python")
    _strip_python(py)

    log("2. Libraries (macOS arm64 / universal2 wheels for CPython 3.13, binary only, same pins as Windows)")
    sp = os.path.join(py, "lib", "python3.13", "site-packages")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--no-compile",
                    "--target", sp, "--platform", PLATFORM, "--python-version", "3.13", "--implementation", "cp",
                    "--only-binary=:all:", "--no-deps", "-r", os.path.join(HERE, "requirements-lock.txt")],
                   check=True)

    log("3. Clean-up: scripts, developer files, Qt tools")
    shutil.rmtree(os.path.join(sp, "bin"), ignore_errors=True)
    for root, dirs, files in os.walk(sp, topdown=True):
        for d in [d for d in dirs if d == "__pycache__" or d.endswith(".app")]:
            shutil.rmtree(os.path.join(root, d))
            dirs.remove(d)
    shutil.rmtree(os.path.join(sp, "sherpa_onnx", "include"), ignore_errors=True)
    for d in QT_DEV_DIRS:
        shutil.rmtree(os.path.join(sp, "PySide6", d), ignore_errors=True)
    # stand-alone tool programs shipped inside the wheels (rcc, uic, lupdate, sherpa-onnx command-line tools)
    removed = []
    for root, _, files in os.walk(sp):
        for fn in files:
            p = os.path.join(root, fn)
            info = None if os.path.islink(p) else macho_check.inspect(p)
            if info and info["filetype"] == "executable":
                os.remove(p)
                removed.append(rel(p, sp))
    log(f"   removed tool programs: {', '.join(sorted(removed)) or 'none'}")

    log("   keeping only the Apple silicon (arm64) part of universal files, like lipo -thin")
    thinned = saved = 0
    for root, _, files in os.walk(stage):
        for fn in files:
            p = os.path.join(root, fn)
            if os.path.islink(p):
                continue
            with open(p, "rb") as f:
                head = f.read(4)
            if len(head) < 4 or int.from_bytes(head, "big") not in (macho_check.FAT_MAGIC, macho_check.FAT_MAGIC_64):
                continue
            with open(p, "rb") as f:
                data = f.read()
            arm = [d for c, d in (macho_check._slices(data) or []) if c == macho_check.CPU_ARM64]
            if arm:  # the arm64 slice is a complete, separately signed Mach-O file
                mode = os.stat(p).st_mode
                with open(p, "wb") as f:
                    f.write(arm[0])
                os.chmod(p, mode)
                thinned += 1
                saved += len(data) - len(arm[0])
    log(f"   {thinned} universal files thinned, {saved / 1e6:.0f} MB less")

    log("4. Program")
    app = os.path.join(stage, "app")
    shutil.copytree(APP_SRC, app, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(app, "BUILD.txt"), "w") as f:
        f.write(datetime.date.today().isoformat())
    for d in ("data", "models"):
        os.makedirs(os.path.join(stage, d))
    with open(os.path.join(stage, "models", "README.txt"), "w") as f:
        f.write("Downloaded speech models are stored here (Options > Transcription > Model download folder).\n")
    with open(os.path.join(stage, "data", "README.txt"), "w") as f:
        f.write("Settings, logs, temporary files and the job journal are kept here. Nothing here leaves this Mac.\n")

    log(f"5. FFmpeg {FFMPEG_VERSION} for Apple silicon (unmodified, from the imageio-ffmpeg wheel)")
    whl = os.path.join(a.cache, FFMPEG_WHEEL)
    if not os.path.isfile(whl):
        subprocess.run([sys.executable, "-m", "pip", "download", "--quiet", "--disable-pip-version-check",
                        "--no-deps", "--only-binary=:all:", "--platform", "macosx_12_0_arm64",
                        "--python-version", "3.13", "--implementation", "cp", "-d", a.cache,
                        "imageio-ffmpeg==0.8.1"], check=True)
    ff = os.path.join(stage, "ffmpeg")
    os.makedirs(ff)
    with zipfile.ZipFile(whl) as z:
        with z.open(FFMPEG_MEMBER) as s, open(os.path.join(ff, "ffmpeg"), "wb") as d:
            shutil.copyfileobj(s, d)
    os.chmod(os.path.join(ff, "ffmpeg"), 0o755)
    for fn, url in FFMPEG_DOCS.items():
        shutil.copy(download(url, os.path.join(a.cache, f"ffmpeg-n9.0-{fn}")), os.path.join(ff, fn))
    with open(os.path.join(ff, "SOURCE.txt"), "w") as f:
        f.write(f"ffmpeg is FFmpeg {FFMPEG_VERSION} for macOS on Apple silicon, distributed unmodified.\n"
                f"It is the binary {FFMPEG_MEMBER} from the imageio-ffmpeg 0.8.1 package on PyPI:\n"
                f"  https://pypi.org/project/imageio-ffmpeg/0.8.1/  ({FFMPEG_WHEEL},\n"
                f"  SHA-256 {sha256_file(whl)})\n"
                f"SHA-256 of ffmpeg: {sha256_file(os.path.join(ff, 'ffmpeg'))}\n"
                "It was configured with --enable-gpl, so it is licensed under the GNU GPL version 2 or later\n"
                "(see COPYING.GPLv2 and LICENSE.md). The complete corresponding source code is available from\n"
                f"https://ffmpeg.org/releases/ffmpeg-{FFMPEG_VERSION}.tar.xz ; on request we provide it for three years.\n"
                "Secure Offline Transcriber runs ffmpeg as a separate program and does not link to it.\n")

    log("6. Launchers and documents")
    pm = os.path.join(HERE, "package-mac")
    for fn in ("run.command", "run-debug.command", "smoke-test.command", "verify-files.command", "README.txt",
               "LICENSE.txt"):
        shutil.copy(os.path.join(pm, fn), os.path.join(stage, fn))
        if fn.endswith(".command"):
            os.chmod(os.path.join(stage, fn), 0o755)
    write_third_party_mac(stage, sp, os.path.join(pm, "THIRD-PARTY-HEADER.txt"))

    log("7. Program inventory and Mach-O check (Apple silicon, signed, no outside libraries)")
    n, problems, exes, minos = macho_check.check_tree(stage)
    log(f"   {n} Mach-O files; programs: {', '.join(exes)}; highest 'minimum macOS' mark {minos[0]}.{minos[1]}"
        " (PySide6 marks its binding files with the macOS it was built on; its wheels target macOS 14)")
    extra = sorted(set(exes) - ALLOWED_PROGRAMS)
    if extra:
        sys.exit(f"Unexpected programs in the package: {extra}")
    if problems:
        sys.exit("Mach-O problems:\n  " + "\n  ".join(problems[:40]))

    log("8. SHA256SUMS.txt")
    lines = []
    for root, _, files in os.walk(stage):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            if not os.path.islink(p):
                lines.append(f"{sha256_file(p)}  {rel(p, stage)}")
    lines.sort(key=lambda l: l.split("  ", 1)[1])
    with open(os.path.join(stage, "SHA256SUMS.txt"), "w", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(stage) for f in fs
               if not os.path.islink(os.path.join(r, f)))
    log(f"   package folder: {size / 1e6:.0f} MB, version {VERSION}")

    log("9. Zip")
    zp = os.path.join(a.out, f"{name}-portable-macos-arm64.zip")
    _zip_tree(stage, zp, name)
    log(f"   {zp}: {os.path.getsize(zp) / 1e6:.0f} MB, SHA-256 {sha256_file(zp)}")


if __name__ == "__main__":
    main()
