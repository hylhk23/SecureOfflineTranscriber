"""Everything that touches ffmpeg.exe (FR-PKG-04): header report, decoding, player stream.

ffmpeg.exe runs with no console window, stdin closed and -nostdin."""
import os
import re
import shutil
import subprocess
import sys

import numpy as np

from . import paths

SAMPLE_RATE = 16000
MEDIA_EXTS = (".wav", ".mp3", ".mp4", ".mkv")
_ffmpeg = None


def find_ffmpeg():
    global _ffmpeg
    if _ffmpeg and os.path.isfile(_ffmpeg):
        return _ffmpeg
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    for folder in (os.path.join(paths.ROOT, "ffmpeg"), os.path.join(paths.ROOT, "ffmpeg", "bin"),
                   os.path.join(paths.APP_DIR, "ffmpeg")):
        cand = os.path.join(folder, exe)
        if os.path.isfile(cand):
            _ffmpeg = cand
            return cand
    _ffmpeg = shutil.which("ffmpeg")
    return _ffmpeg


class FFmpegMissing(RuntimeError):
    pass


def _popen_kwargs():
    kw = {"stdin": subprocess.DEVNULL}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kw


def _ffmpeg_or_raise():
    exe = find_ffmpeg()
    if not exe:
        raise FFmpegMissing("ffmpeg.exe not found. It belongs in the ffmpeg folder of the program.")
    return exe


def version_info():
    """(version, licence) of the ffmpeg build, for the About box."""
    exe = find_ffmpeg()
    if not exe:
        return None, None
    try:
        out = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True,
                             errors="replace", timeout=15, **_popen_kwargs()).stdout
    except (OSError, subprocess.SubprocessError):
        return None, None
    m = re.search(r"ffmpeg version (\S+)", out)
    lic = "GPL-3.0" if "--enable-gpl" in out and "--enable-version3" in out else (
        "GPL-2.0" if "--enable-gpl" in out else "LGPL-2.1")
    return (m.group(1) if m else "?"), lic


def _hms(s):
    h, m, sec = s.split(":")
    return int(h) * 3600 + int(m) * 60 + float(sec)


def probe(path):
    """Read the header with 'ffmpeg -i' (no ffprobe.exe ships). Returns a dict."""
    exe = _ffmpeg_or_raise()
    r = subprocess.run([exe, "-hide_banner", "-nostdin", "-i", path], capture_output=True, text=True,
                       errors="replace", timeout=60, **_popen_kwargs())
    err = r.stderr
    info = {"path": path, "size": os.path.getsize(path) if os.path.exists(path) else 0,
            "format": "", "duration": 0.0, "audio": [], "video": False}
    m = re.search(r"Input #0, ([^,]+(?:,[^,]+)*?), from", err)
    if m:
        info["format"] = m.group(1).split(",")[0].strip()
    m = re.search(r"Duration: (\d+:\d+:\d+(?:\.\d+)?)", err)
    if m:
        info["duration"] = _hms(m.group(1))
    for sm in re.finditer(r"Stream #0:(\d+)(?:\[[^\]]*\])?(?:\((\w+)\))?[^:]*: (Audio|Video): ([^\n]*)", err):
        idx, lang, kind, rest = sm.groups()
        if kind == "Video":
            if "attached pic" not in rest:
                info["video"] = True
            continue
        codec = rest.split(",")[0].split()[0] if rest else "?"
        rate = re.search(r"(\d+) Hz", rest)
        ch = re.search(r"Hz, ([^,]+)", rest)
        info["audio"].append({"index": int(idx), "lang": lang or "", "codec": codec,
                              "rate": int(rate.group(1)) if rate else 0,
                              "channels": ch.group(1).strip() if ch else ""})
    if not info["audio"]:
        msg = err.strip().splitlines()[-1] if err.strip() else "unknown error"
        raise RuntimeError(f"No audio track found ({msg}).")
    return info


def info_line(info):
    """'MP3 · 13:59 · 48 kHz stereo · 13 MB'"""
    a = info["audio"][0]
    parts = [ext_label(info), fmt_clock(info["duration"])]
    if a["rate"]:
        parts.append(f"{a['rate'] / 1000:g} kHz {a['channels']}".strip())
    size = info["size"]
    parts.append(f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB")
    if len(info["audio"]) > 1:
        parts.append(f"{len(info['audio'])} audio tracks")
    return " · ".join(parts)


def ext_label(info):
    ext = os.path.splitext(info["path"])[1].lstrip(".").upper()
    return ext or info["format"].upper()


def fmt_clock(seconds, ms=False):
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int(seconds % 3600 // 60)
    s = seconds % 60
    if ms:
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return f"{h:02d}:{m:02d}:{int(s):02d}"


# Noise reduction (Options > Advanced > Audio): FFmpeg's own filters, nothing to download.
#   highpass  removes rumble (air conditioning, traffic, table knocks) below the speech band
#   afftdn    FFT noise reduction that learns the steady background noise as it goes (tn=1)
NOISE_FILTERS = {
    "off": [],
    "light": ["highpass=f=80", "afftdn=nr=12:nf=-40:tn=1"],
    "strong": ["highpass=f=100", "afftdn=nr=24:nf=-35:tn=1"],
}


def noise_filters(level):
    return list(NOISE_FILTERS.get(level or "off", []))


def decode(path, track=0, start=0.0, duration=None, stereo="average", denoise="off"):
    """Decode one audio track (or part of it) to 16 kHz mono float32 via a pipe (no temp file)."""
    exe = _ffmpeg_or_raise()
    cmd = [exe, "-hide_banner", "-nostdin", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", path, "-map", f"0:a:{track}", "-vn", "-sn", "-dn"]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    filters = noise_filters(denoise)
    if stereo == "left":
        filters = ["pan=mono|c0=c0"] + filters
    elif stereo == "right":
        filters = ["pan=mono|c0=c1"] + filters
    if filters:
        cmd += ["-af", ",".join(filters)]
    cmd += ["-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **_popen_kwargs())
    if p.returncode != 0 and not p.stdout:
        detail = p.stderr.decode("utf-8", "replace").strip()[-300:]
        raise RuntimeError(f"ffmpeg could not read the file. {detail}")
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def sha256_file(path, chunk=4 << 20):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class PcmStream:
    """ffmpeg.exe streaming 16-bit stereo PCM from a position, for the player (FR-UI-04)."""
    RATE = 48000
    CHANNELS = 2

    def __init__(self, path, track=0, start=0.0, speed=1.0, denoise="off"):
        """speed: 0.5-2.0, pitch kept (atempo). denoise: hear the noise reduction used for recognition."""
        exe = _ffmpeg_or_raise()
        cmd = [exe, "-hide_banner", "-nostdin", "-v", "error"]
        if start > 0:
            cmd += ["-ss", f"{start:.3f}"]
        cmd += ["-i", path, "-map", f"0:a:{track}", "-vn"]
        filters = noise_filters(denoise)
        if abs(speed - 1.0) > 1e-3:
            filters.append(f"atempo={speed:.3f}")
        if filters:
            cmd += ["-af", ",".join(filters)]
        cmd += ["-ac", str(self.CHANNELS), "-ar", str(self.RATE), "-f", "s16le", "-"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     bufsize=0, **_popen_kwargs())

    def read(self, nbytes):
        try:
            return self.proc.stdout.read(nbytes)
        except (OSError, ValueError):
            return b""

    def close(self):
        try:
            self.proc.kill()
        except OSError:
            pass
        try:
            self.proc.stdout.close()
        except (OSError, AttributeError):
            pass
