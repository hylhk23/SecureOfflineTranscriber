"""Secure Offline Transcriber - start here.

    python\\pythonw.exe app\\OfflineTranscriber.py [files...]
    python\\python.exe  app\\OfflineTranscriber.py --debug     (console with log output)
    python\\python.exe  app\\OfflineTranscriber.py --smoke-test (start, check the window and imports, quit)
"""
import multiprocessing
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


REQUIRED = ["PySide6", "shiboken6", "numpy", "ctranslate2", "faster_whisper", "onnxruntime", "sherpa_onnx",
            "tokenizers", "sounddevice.py", "_sounddevice_data", "docx", "lxml", "opencc", "pypdf"]


def check_package():
    """Portable package only: name the missing library folders before anything fails on them."""
    root = os.path.dirname(HERE)
    mac = sys.platform == "darwin"
    if mac:  # macOS package: python/lib/python3.13/site-packages and ffmpeg/ffmpeg
        sp = os.path.join(root, "python", "lib", f"python{sys.version_info[0]}.{sys.version_info[1]}", "site-packages")
        ff = os.path.join("ffmpeg", "ffmpeg")
    else:
        sp = os.path.join(root, "python", "Lib", "site-packages")
        ff = os.path.join("ffmpeg", "ffmpeg.exe")
    if not os.path.isdir(os.path.join(root, "python")):
        return True  # running from source with an ordinary Python
    missing = [n for n in REQUIRED if not os.path.exists(os.path.join(sp, n))]
    if not os.path.isfile(os.path.join(root, ff)):
        missing.append(ff)
    if not missing:
        return True
    head = ("Some program files are missing, so Secure Offline Transcriber cannot start:\n\n  "
            + "\n  ".join(missing[:12]) + ("\n  ..." if len(missing) > 12 else ""))
    if mac:
        msg = head + ("\n\nThe zip was not fully unzipped. Delete this folder, unzip the zip again, then run "
                      "verify-files.command to check every file.")
    else:
        msg = head + ("\n\nThe zip was not fully unzipped - often because antivirus blocked a file or unzipping was "
                      "stopped.\n\nDelete this folder, unzip the zip again (7-Zip is the most reliable), then run "
                      "verify-files.bat to check every file. Windows Security > Protection history shows any file it "
                      "blocked.")
    try:
        if mac:
            import subprocess
            subprocess.run(["osascript", "-e", "on run argv", "-e",
                            'display dialog (item 1 of argv) with title "Secure Offline Transcriber" buttons {"OK"} '
                            "with icon stop", "-e", "end run", msg], timeout=600)
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, "Secure Offline Transcriber", 0x10)
    except Exception:
        pass
    print(msg, file=sys.__stderr__ or sys.stderr)
    return False


def main():
    if not check_package():
        return 2
    from sot import APP_ID, APP_NAME, VERSION, paths
    paths.offline_env()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    debug = "--debug" in sys.argv
    smoke = "--smoke-test" in sys.argv

    if os.name == "nt":
        import ctypes
        try:  # own taskbar icon instead of python's
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"NeoTimeConsulting.{APP_ID}.2")
        except Exception:
            pass

    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QIcon
    from PySide6.QtNetwork import QLocalServer, QLocalSocket
    from PySide6.QtWidgets import QApplication

    from sot.settings import Settings
    settings = Settings()
    logs = paths.setup_logging(settings.data_folder())
    import logging
    log = logging.getLogger("main")
    if debug:
        h = logging.StreamHandler(sys.__stdout__ or sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(h)
    log.info("%s %s starting (Python %s, %s)", APP_NAME, VERSION, sys.version.split()[0], sys.executable)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Neo Time Consulting")
    app.setApplicationVersion(VERSION)
    icon_path = os.path.join(HERE, "app.ico")
    app.setWindowIcon(QIcon(icon_path))
    if os.name == "nt" and "windows11" not in [s.lower() for s in __import__("PySide6.QtWidgets", fromlist=["QStyleFactory"]).QStyleFactory.keys()]:
        app.setStyle("Fusion")

    # single instance: a named pipe (no network port); a second start hands its files over
    server_name = f"{APP_ID}-{os.environ.get('USERNAME', os.environ.get('USER', 'user'))}"
    if not smoke:
        sock = QLocalSocket()
        sock.connectToServer(server_name)
        if sock.waitForConnected(300):
            sock.write(("\n".join(os.path.abspath(a) for a in args) or "\x00").encode("utf-8"))
            sock.flush()
            sock.waitForBytesWritten(1000)
            sock.disconnectFromServer()
            log.info("another window is open; files handed over")
            return 0
    server = QLocalServer()
    QLocalServer.removeServer(server_name)
    server.listen(server_name)

    from sot.modelmgr import ModelManager
    from sot.ui.mainwindow import MainWindow
    from sot.worker import Worker
    manager = ModelManager(settings)
    worker = Worker(settings.data_folder())
    win = MainWindow(settings, manager, worker, icon_path)

    def incoming():
        c = server.nextPendingConnection()
        if not c:
            return

        def read():
            data = bytes(c.readAll()).decode("utf-8", "replace").strip("\x00")
            files = [f for f in data.splitlines() if f]
            win.showNormal()
            win.raise_()
            win.activateWindow()
            if files:
                win.open_paths(files)
        c.readyRead.connect(read)
    server.newConnection.connect(incoming)

    win.show()
    if smoke:
        return smoke_test(app, win)
    worker.ensure()  # start the recognition process early so the first job starts quickly

    def after_start():
        win.offer_journal()
        if args:
            win.open_paths([os.path.abspath(a) for a in args])
        elif settings.get("general.open_last") and settings.get("general.recent"):
            last = settings.get("general.recent")[0]
            if os.path.isfile(last):
                win.open_path(last)
        if not manager.installed() and not settings.get("general.first_run_done"):
            win.first_run()
    QTimer.singleShot(300, after_start)
    rc = app.exec()
    log.info("closed")
    return rc


def smoke_test(app, win):
    """Used by the build (Qt trimming) and by Help > Environment checks: every library must import."""
    from PySide6.QtCore import QTimer
    errors = []
    from sot.avstub import install
    install()
    for mod in ("numpy", "faster_whisper", "ctranslate2", "sherpa_onnx", "onnxruntime", "docx", "opencc", "pypdf",
                "sot.engines", "sot.exporters", "sot.names", "sot.ui.options", "sot.ui.dialogs", "sot.ui.namesdlg"):
        try:
            __import__(mod)
        except Exception as ex:
            errors.append(f"{mod}: {ex}")
    try:
        import sounddevice  # noqa: F401
    except Exception as ex:  # PortAudio missing is reported, not fatal
        errors.append(f"sounddevice: {ex}")
    from sot import media
    if not media.find_ffmpeg():
        errors.append("ffmpeg.exe not found")
    from sot.ui.options import OptionsDialog
    dlg = OptionsDialog(win, win.settings, win.manager)
    dlg.show()
    QTimer.singleShot(500, app.quit)
    app.exec()
    dlg.close()
    win.worker.stop()
    out = "SMOKE TEST " + ("PASSED" if not errors else "FAILED:\n  " + "\n  ".join(errors))
    print(out, file=sys.__stdout__ or sys.stdout)
    import logging
    logging.getLogger("main").info(out)
    return 0 if not errors else 1


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
