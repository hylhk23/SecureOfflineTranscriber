# Secure Offline Transcriber: developer notes

End-user download and installation: see the main [README](../README.md). User help: [`app/docs/HELP.html`](../app/docs/HELP.html), also opened with **F1** in the app.

## About the code

Secure Offline Transcriber is an offline speech-to-text program for Windows 10 and 11 (64-bit), written in Python with Qt (PySide6). It transcribes recorded audio and video files in English, French, Cantonese and Mandarin with Whisper (through faster-whisper / CTranslate2) and with SenseVoiceSmall, Parakeet and Moonshine (through sherpa-onnx / ONNX Runtime). FFmpeg (`ffmpeg.exe`) reads every audio and video format.

---

## 1. Run it

**Portable package (nothing to install, no admin rights).** The `-portable-win64` zip contains its own Python 3.13, all libraries in the `python` folder and `ffmpeg\ffmpeg.exe`. Unzip it anywhere and double-click **`run.bat`** (`run-debug.bat` shows the log in a console; `smoke-test.bat` checks that every library loads).

**Source code (this repository).** Needs a full Python 3.13 (64-bit) from python.org.

```bat
py -3.13 -m venv .venv
.venv\Scripts\pip install -r build\requirements.txt
.venv\Scripts\python app\OfflineTranscriber.py
```

Put `ffmpeg.exe` in a folder named `ffmpeg` at the top of the repository, or anywhere on `PATH`. Settings, logs and downloaded models are kept in `data\` and `models\` at the top of the repository (both are ignored by git). The program also runs on Linux for development and testing (`QT_QPA_PLATFORM=offscreen` for headless runs).

Command-line switches of `app\OfflineTranscriber.py`: `--debug` (log to the console), `--smoke-test` (load every library, open the window and the Options dialog, then exit with 0 or 1), and file paths to open.

## 2. Project layout

| Path | What it is |
| --- | --- |
| `app/OfflineTranscriber.py` | Entry point: start-up checks, single-instance lock (a named pipe, no network port), smoke test |
| `app/sot/settings.py` | All settings with defaults (`DEFAULTS`), atomic save, export and import |
| `app/sot/media.py` | `ffmpeg.exe` wrapper: probe, decode to 16 kHz float32, noise filters, PCM stream for the player |
| `app/sot/audioproc.py` | Volume normalisation and Silero voice detection (from faster-whisper) |
| `app/sot/engines.py` | Whisper and sherpa-onnx engines; model-type detection from the files in a folder |
| `app/sot/worker.py` | Recognition in a separate process (`multiprocessing`, spawn), 30-minute blocks, resume from a start time |
| `app/sot/postproc.py` | Invented-text filter, replacements, Chinese script conversion (OpenCC), paragraphs |
| `app/sot/exporters.py` | The eight export formats |
| `app/sot/modellist.py`, `app/models.json` | The model list: what can be downloaded, the recommended set and the Automatic order |
| `app/sot/modelmgr.py` | Model manager: scanning, downloads with resume and SHA-256 checks, other locations, model packs |
| `app/sot/names.py` | Names from documents: reads docx, xlsx, pdf, eml, txt, csv, html and finds names with rules |
| `app/sot/system.py` | Environment and privacy checks, keep-awake, "Open with" registration, job journal |
| `app/sot/ui/` | Main window, transcript editor (proofreading), player, Options dialog, names dialog, small dialogs |
| `app/docs/HELP.html` | User help (every menu item and setting) |
| `tests/test_core.py` | Headless tests (no model needed) |
| `build/` | Portable build kit and the model-list tool |

## 3. Tests

```bat
.venv\Scripts\pip install pytest openpyxl
.venv\Scripts\python -m pytest tests
```

The tests cover settings, exports, the invented-text filter, replacements, the model list, names from documents and the help page: a test fails if a setting or a menu item is missing from `app/docs/HELP.html`.

## 4. Build the portable zip

`build\build.bat` (or `py -3 build\build.py`) needs any Python 3.10 or newer and internet. It runs on Windows or Linux, because everything it downloads is a Windows x64 file:

- the Python 3.13 embeddable package from python.org;
- the Windows x64 wheels for CPython 3.13 listed in `build\requirements-lock.txt` (binary wheels only; nothing is compiled);
- the FFmpeg 9.0.2 essentials build from gyan.dev (SHA-256 pinned); only `ffmpeg.exe` is kept.

It then removes every `.exe` except `python.exe`, `pythonw.exe` and `ffmpeg.exe`, leaves out PyAV (faster-whisper's own decoder, not used here) and the Qt modules a Widgets program never loads, writes `THIRD-PARTY-LICENSES.txt` and `SHA256SUMS.txt`, and stops with an error if any other program is found.

| Option | Effect |
| --- | --- |
| `--update` | Take the newest allowed versions from `requirements.txt` and rewrite the lock file |
| `--keep-qt` | Keep every Qt module (if `smoke-test.bat` ever fails after trimming) |
| `--ffmpeg-zip FILE` | Use an FFmpeg zip you already have |
| `--no-zip` | Build the folder only |

**Why no PyInstaller.** Programs packed into an `.exe` with PyInstaller share a prebuilt launcher that some antivirus engines flag. The portable package instead runs the official `pythonw.exe`, signed by the Python Software Foundation, on plain `.py` files.

## 5. The model list

Everything Options > Transcription offers for download is described by `app\models.json`. To publish new or updated models, edit it and run `build\tools\update-model-list.bat`: it fills in the Hugging Face commit, every file's size and SHA-256, sets a new `list_version` and checks the list exactly as the program does on import. Users import the file with **Options > Transcription > Import model list...**. Field reference and rules: [`build/MODEL-LIST.md`](../build/MODEL-LIST.md).

A list can only point at huggingface.co, at a fixed commit, with plain file names, and every downloaded file must match its SHA-256. A model of a new kind (other than Whisper, SenseVoiceSmall, Parakeet or Moonshine) needs a new program version.

## 6. Privacy rules in the code

- No network access except model downloads started by the user (`modelmgr.py`); `HF_HUB_OFFLINE` is set before any model library loads.
- Logs (`data\logs`, kept 7 days) hold file names and timings, never transcript text.
- Settings are written atomically; the job journal (`data\jobs`) holds the text of a running job for resume after a crash and is deleted when the job finishes.
