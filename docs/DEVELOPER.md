# Secure Offline Transcriber: developer notes

End-user download and installation: see the main [README](../README.md). User help: [`app/docs/HELP.html`](../app/docs/HELP.html), also opened with **F1** in the app.

## About the code

Secure Offline Transcriber is an offline speech-to-text program for Windows 10 and 11 (64-bit) and for macOS 14 or later on Apple silicon, written in Python with Qt (PySide6). It transcribes recorded audio and video files in English, French, Cantonese and Mandarin with Whisper (through faster-whisper / CTranslate2) and with SenseVoiceSmall, Parakeet and Moonshine (through sherpa-onnx / ONNX Runtime). FFmpeg (`ffmpeg.exe`) reads every audio and video format. From 2.3.0 an optional AI summary is written by a small language model through llama.cpp (called with ctypes), and from 2.3.1 English transcripts can follow American, British, Canadian, Australian or Hong Kong spelling.

---

## 1. Run it

**Portable package (nothing to install, no admin rights).** The `-portable-win64` zip contains its own Python 3.13, all libraries in the `python` folder, `ffmpeg\ffmpeg.exe` and the llama.cpp libraries in `llamacpp\`. Unzip it anywhere and double-click **`run.bat`** (`run-debug.bat` shows the log in a console; `smoke-test.bat` checks that every library loads).

**Source code (this repository).** Needs a full Python 3.13 (64-bit) from python.org.

```bat
py -3.13 -m venv .venv
.venv\Scripts\pip install -r build\requirements.txt
.venv\Scripts\python app\OfflineTranscriber.py
```

Put `ffmpeg.exe` in a folder named `ffmpeg` at the top of the repository, or anywhere on `PATH`. For the AI summary, put the llama.cpp b12000 libraries in a folder named `llamacpp` at the top of the repository (or name the folder in the environment variable `SOT_LLAMA_DIR`). Settings, logs and downloaded models are kept in `data\` and `models\` at the top of the repository (both are ignored by git). The program also runs on Linux for development and testing (`QT_QPA_PLATFORM=offscreen` for headless runs).

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
| `app/sot/postproc.py` | Invented-text filter, replacements, Chinese script conversion (OpenCC), English spelling, paragraphs |
| `app/sot/spelling.py`, `app/data/spelling.json` | English spelling by region: pure converter and its word tables (made by `build/tools/make-spelling-data.py` from the English Speller Database) |
| `app/sot/llm.py` | llama.cpp through ctypes: loading (CPU, or the Metal GPU on a Mac when the model fits), tokens, generation |
| `app/sot/summary.py`, `app/sot/summaryworker.py` | AI summary: prompts, long transcripts in parts, output clean-up; the summary process |
| `app/sot/exporters.py` | The eight export formats |
| `app/sot/modellist.py`, `app/models.json` | The model list: what can be downloaded, the recommended set and the Automatic order |
| `app/sot/modelmgr.py` | Model manager: scanning, downloads with resume and SHA-256 checks, other locations, model packs |
| `app/sot/names.py` | Names from documents: reads docx, xlsx, pdf, eml, txt, csv, html and finds names with rules |
| `app/sot/system.py` | Environment and privacy checks, keep-awake, "Open with" registration, country or region, job journal |
| `app/sot/ui/` | Main window, transcript editor (proofreading), player, Options dialog, names dialog, small dialogs |
| `app/docs/HELP.html` | User help (every menu item and setting) |
| `tests/` | Headless tests (no model needed): `test_core.py`, `test_spelling.py`, `test_mac.py` |
| `build/` | Portable build kit and the model-list tool |

## 3. Tests

```bat
.venv\Scripts\pip install pytest openpyxl
.venv\Scripts\python -m pytest tests
```

The tests cover settings, exports, the invented-text filter, replacements, the model list, names from documents, voice detection, the AI summary, English spelling (with cases from Canadian, Australian and UK government style guides), the macOS summary engine and the help page: a test fails if a setting or a menu item is missing from `app/docs/HELP.html`. Some tests use the downloaded llama.cpp archives in `build/cache-llama` and are skipped without them.

## 4. Build the portable zip

`build\build.bat` (or `py -3 build\build.py`) needs any Python 3.10 or newer and internet. It runs on Windows or Linux, because everything it downloads is a Windows x64 file:

- the Python 3.13 embeddable package from python.org;
- the Windows x64 wheels for CPython 3.13 listed in `build\requirements-lock.txt` (binary wheels only; nothing is compiled);
- the FFmpeg 9.0.2 essentials build from gyan.dev (SHA-256 pinned); only `ffmpeg.exe` is kept;
- the llama.cpp b12000 Windows CPU build from github.com/ggml-org/llama.cpp (SHA-256 pinned); only the libraries the AI summary needs are kept, in `llamacpp\` (no program, no network library).

It then removes every `.exe` except `python.exe`, `pythonw.exe` and `ffmpeg.exe`, leaves out PyAV (faster-whisper's own decoder, not used here) and the Qt modules a Widgets program never loads, writes `THIRD-PARTY-LICENSES.txt` and `SHA256SUMS.txt`, and stops with an error if any other program is found.

| Option | Effect |
| --- | --- |
| `--update` | Take the newest allowed versions from `requirements.txt` and rewrite the lock file |
| `--keep-qt` | Keep every Qt module (if `smoke-test.bat` ever fails after trimming) |
| `--ffmpeg-zip FILE` | Use an FFmpeg zip you already have |
| `--no-zip` | Build the folder only |

**The macOS package.** `python3 build/build_mac.py` (runs on Linux, macOS or Windows) makes `dist/SecureOfflineTranscriber-portable-macos-arm64.zip` with the same app folder and the same pinned libraries as macOS arm64 or universal2 wheels:

- Python 3.13 from the python-build-standalone project (relocatable `install_only` build for `aarch64-apple-darwin`, SHA-256 pinned);
- `ffmpeg`: FFmpeg 9.0 for Apple silicon, the unmodified binary inside the imageio-ffmpeg 0.8.1 wheel, with FFmpeg's licence files;
- the llama.cpp b12000 macOS arm64 build (SHA-256 pinned): seven libraries with the release's links, unmodified, in `llamacpp/` (the program loads them in dependency order);
- launchers `run.command`, `run-debug.command`, `smoke-test.command`, `verify-files.command` and a Mac `README.txt` (from `build/package-mac`).

It keeps only the arm64 part of universal files (as `lipo -thin` does; each part keeps its own signature), removes Qt's developer files, tools and the SQL driver plugins, and then `build/macho_check.py` stops the build if a Mach-O file lacks arm64 code or a signature, links to a library outside the system and the package, or is a program other than `python3.13` and `ffmpeg`. The package needs macOS 14 or later (PySide6 6.12 and ONNX Runtime 1.29); Intel Macs are not supported because ONNX Runtime 1.29 has no x86_64 macOS build.

**Why no PyInstaller.** Programs packed into an `.exe` with PyInstaller share a prebuilt launcher that some antivirus engines flag. The portable package instead runs the official `pythonw.exe`, signed by the Python Software Foundation, on plain `.py` files.

## 5. The model list

Everything Options > Transcription offers for download is described by `app\models.json`. To publish new or updated models, edit it and run `build\tools\update-model-list.bat`: it fills in the Hugging Face commit, every file's size and SHA-256, sets a new `list_version` and checks the list exactly as the program does on import. Users import the file with **Options > Transcription > Import model list...**. Field reference and rules: [`build/MODEL-LIST.md`](../build/MODEL-LIST.md).

A list can only point at huggingface.co, at a fixed commit, with plain file names, and every downloaded file must match its SHA-256. A model of a new kind (other than Whisper, SenseVoiceSmall, Parakeet or Moonshine) needs a new program version. The same file lists the AI summary models (`summary_models`, one GGUF file each); a new chat format needs a new program version.

## 5a. The English spelling tables

`app/data/spelling.json` is made by `python build/tools/make-spelling-data.py <scowl.db> app/data/spelling.json`, where `scowl.db` is built by running `make` in a copy of the English Speller Database (github.com/en-wl/wordlist). The script's header explains which words become rules and which are left to the context rules in `app/sot/spelling.py`.

## 6. Privacy rules in the code

- No network access except model downloads started by the user (`modelmgr.py`); `HF_HUB_OFFLINE` is set before any model library loads. The AI summary runs in its own process on the computer; on Windows llama.cpp's network library is left out, and on a Mac it is present (llama.cpp's libraries link to it) but never given a server address.
- The country or region for Automatic English spelling is read from the operating system on the computer and never sent anywhere.
- Logs (`data\logs`, kept 7 days) hold file names and timings, never transcript text.
- Settings are written atomically; the job journal (`data\jobs`) holds the text of a running job for resume after a crash and is deleted when the job finishes.
