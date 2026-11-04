"""Windows integration and diagnostics: environment and privacy checks (FR-ADV-03/04), keep-awake,
'Open with' registration (Should), job journal (FR-ASR-08)."""
import json
import os
import platform
import subprocess
import sys

from . import APP_ID, APP_NAME, VERSION, paths

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"


# ------------------------------------------------------------------ versions
LIBS = [("PySide6", ("PySide6-Essentials", "PySide6")), ("numpy", ("numpy",)),
        ("faster-whisper", ("faster-whisper",)), ("ctranslate2", ("ctranslate2",)),
        ("sherpa_onnx", ("sherpa-onnx", "sherpa_onnx")), ("onnxruntime", ("onnxruntime",)),
        ("sounddevice", ("sounddevice",)), ("OpenCC", ("opencc-python-reimplemented",)),
        ("python-docx", ("python-docx",))]


def lib_versions_list():
    from importlib import metadata
    from . import media
    out = []
    for name, dists in LIBS:
        v = None
        for d in dists:
            try:
                v = metadata.version(d)
                break
            except metadata.PackageNotFoundError:
                continue
        out.append(f"{name} {v or 'not installed'}")
    fv, _ = media.version_info()
    out.append(f"FFmpeg {fv or 'not found'}")
    return out


def build_date():
    p = os.path.join(paths.APP_DIR, "BUILD.txt")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return "source"


# ------------------------------------------------------------------ hardware
def cpu_name():
    if IS_WIN:
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    if IS_MAC:
        try:
            out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
            if out:
                return out
        except Exception:
            pass
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def has_avx2():
    if IS_WIN:
        try:
            import ctypes
            return bool(ctypes.windll.kernel32.IsProcessorFeaturePresent(40))  # PF_AVX2_INSTRUCTIONS_AVAILABLE
        except Exception:
            return None
    if IS_MAC:
        return None  # Apple Silicon has no AVX2; the ARM builds of the libraries are used
    try:
        with open("/proc/cpuinfo") as f:
            return " avx2" in f.read()
    except OSError:
        return None


def windows_version():
    if IS_WIN:
        v = sys.getwindowsversion()
        name = "Windows 11" if v.build >= 22000 else "Windows 10" if v.major == 10 else f"Windows {v.major}.{v.minor}"
        return f"{name} (build {v.build})"
    if IS_MAC:
        return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
    return platform.platform()


def folder_size(folder):
    total = 0
    for root, _, files in os.walk(folder):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def temp_folder(settings):
    return os.path.join(settings.data_folder(), "temp")


def environment_report(settings, manager):
    from .modelmgr import free_disk, human_size, ram_gb
    total, avail = ram_gb()
    lines = [f"{APP_NAME} {VERSION}", f"Program folder: {paths.ROOT}", "",
             f"{'Windows' if IS_WIN else 'System'}: {windows_version()}", f"CPU: {cpu_name()}",
             f"Cores: {os.cpu_count()}",
             f"AVX2: {'yes' if has_avx2() else 'no' if has_avx2() is False else 'not applicable (Apple Silicon)' if IS_MAC else 'unknown'}",
             f"RAM: {total:.1f} GB ({avail:.1f} GB free)",
             f"Model download folder: {settings.download_folder()}",
             f"Free disk on that drive: {human_size(free_disk(settings.download_folder()))}",
             f"Python: {platform.python_version()} ({sys.executable})", ""]
    lines += lib_versions_list()
    lines += ["", "Installed models:"]
    inst = manager.installed()
    if not inst:
        lines.append("  (none)")
    for m in sorted(inst, key=lambda x: x.name):
        lines.append(f"  {m.name} - {m.kind}, {human_size(m.size)}, {m.folder}")
    other = [m for m in manager.models.values() if m.status not in ("Installed", "Not installed")]
    for m in other:
        lines.append(f"  {m.name} - {m.status}")
    return "\n".join(lines)


def listening_ports():
    """TCP/UDP ports this process listens on (should be none: the single-instance lock is a named pipe)."""
    if IS_MAC:
        return _mac_listening_ports()
    if not IS_WIN:
        return None
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10,
                             creationflags=0x08000000).stdout
    except Exception:
        return None
    pid = str(os.getpid())
    ports = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[-1] == pid and ("LISTENING" in parts or parts[0].upper() == "UDP"):
            ports.append(f"{parts[0]} {parts[1]}")
    return ports


def _mac_listening_ports():
    """macOS: lsof lists this process's listening TCP sockets and its UDP sockets (exit code 1 = none)."""
    ports = []
    try:
        for spec in (["-iTCP", "-sTCP:LISTEN"], ["-iUDP"]):
            out = subprocess.run(["lsof", "-nP", "-a", "-p", str(os.getpid())] + spec, capture_output=True,
                                 text=True, timeout=10).stdout
            for line in out.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 9:
                    ports.append(f"{parts[7]} {parts[8]}")
    except Exception:
        return None
    return ports


def privacy_report(settings):
    tmp = temp_folder(settings)
    tmp_files = []
    if os.path.isdir(tmp):
        for root, _, files in os.walk(tmp):
            tmp_files += files
    ports = listening_ports()
    lines = [
        f"Offline Mode: {'ON - no network call is possible' if settings.get('advanced.offline') else 'off (only model downloads use the network, and only when you start one)'}",
        "Telemetry: none (the program has no analytics, crash reporting or update server)",
        f"Settings: {paths.settings_dir()}",
        f"Data folder (logs, temp, job journal): {settings.data_folder()}",
        f"Model download folder: {settings.download_folder()}",
        "Other model locations: " + (", ".join(settings.locations()) or "none"),
        f"Output folder: {settings.output_folder()}",
        f"Temp folder: {tmp} - " + ("empty" if not tmp_files else f"{len(tmp_files)} file(s); Clear temp now removes them"),
        "Listening network ports: " + ("none" if ports == [] else "unknown" if ports is None
                                       else ", ".join(ports)),
        "Audio and transcripts never leave this PC. Logs hold file names and timings, never transcript text.",
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ keep awake (General tab)
_caffeinate = None  # macOS: the caffeinate process that keeps the Mac awake


def keep_awake(on):
    global _caffeinate
    if IS_MAC:
        if on and _caffeinate is None:
            try:
                _caffeinate = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdin=subprocess.DEVNULL,
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError:
                _caffeinate = None
        elif not on and _caffeinate is not None:
            try:
                _caffeinate.terminate()
            except OSError:
                pass
            _caffeinate = None
        return
    if not IS_WIN:
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if on else 0))


# ------------------------------------------------------------------ "Open with" (Should)
EXTS = (".wav", ".mp3", ".mp4", ".mkv", ".m4a")
PROGID = APP_ID + ".media"


def launch_command():
    py = os.path.join(paths.ROOT, "python", "pythonw.exe")
    if not os.path.isfile(py):
        py = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{py}" "{os.path.join(paths.APP_DIR, "OfflineTranscriber.py")}" "%1"'


def register_file_types():
    import winreg
    base = r"Software\Classes"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROGID}") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, f"Media file ({APP_NAME})")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROGID}\DefaultIcon") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, os.path.join(paths.APP_DIR, "app.ico"))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROGID}\shell\open") as k:
        winreg.SetValueEx(k, "FriendlyAppName", 0, winreg.REG_SZ, APP_NAME)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROGID}\shell\open\command") as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, launch_command())
    for ext in EXTS:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{base}\{ext}\OpenWithProgids") as k:
            winreg.SetValueEx(k, PROGID, 0, winreg.REG_NONE, b"")
    _notify_shell()


def unregister_file_types():
    import winreg
    base = r"Software\Classes"
    for ext in EXTS:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{base}\{ext}\OpenWithProgids", 0,
                                winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, PROGID)
        except OSError:
            pass
    for sub in (r"\shell\open\command", r"\shell\open", r"\shell", r"\DefaultIcon", ""):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{base}\{PROGID}{sub}")
        except OSError:
            pass
    _notify_shell()


def file_types_registered():
    if not IS_WIN:
        return False
    import winreg
    try:
        winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROGID}\shell\open\command").Close()
        return True
    except OSError:
        return False


def _notify_shell():
    try:
        import ctypes
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except Exception:
        pass


# ------------------------------------------------------------------ job journal (FR-ASR-08)
def journal_path(settings):
    return os.path.join(settings.data_folder(), "jobs", "current.json")


def journal_write(settings, data):
    p = journal_path(settings)
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(p + ".tmp", p)
    except OSError:
        pass


def journal_read(settings):
    try:
        with open(journal_path(settings), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def journal_clear(settings):
    try:
        os.remove(journal_path(settings))
    except OSError:
        pass


def open_folder(path):
    os.makedirs(path, exist_ok=True)
    if IS_WIN:
        os.startfile(path)
    else:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
