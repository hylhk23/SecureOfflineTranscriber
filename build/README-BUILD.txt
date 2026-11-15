Secure Offline Transcriber - build kit
======================================

What it makes
  dist\SecureOfflineTranscriber\                       the portable folder
  dist\SecureOfflineTranscriber-portable-win64.zip   the release asset (no version in the name)

How
  1. Install Python 3.10 or newer from python.org (only used to run pip; the package
     itself gets the Python 3.13 embeddable package).
  2. Double-click build\build.bat (or: py -3 build\build.py).
  The build downloads, checks and assembles:
    - python-3.13.17-embed-amd64.zip from python.org
    - Windows x64 wheels for CPython 3.13 listed in requirements-lock.txt
      (binary wheels only: nothing is compiled on your PC)
    - ffmpeg-9.0.2-essentials_build.zip from gyan.dev (SHA-256 pinned); only ffmpeg.exe is kept
    - llama-b12000-bin-win-cpu-x64.zip from github.com/ggml-org/llama.cpp (SHA-256 pinned); only the
      libraries the AI summary needs are kept, in llamacpp\ (no .exe, no ggml-rpc.dll)
  It then removes every .exe except python.exe, pythonw.exe and ffmpeg.exe, removes the
  Qt modules a Widgets program never loads, writes THIRD-PARTY-LICENSES.txt and
  SHA256SUMS.txt, and stops with an error if any other program is found.

Options
  --update        take the newest allowed versions from requirements.txt and rewrite the lock file
  --keep-qt       keep every Qt module (if smoke-test.bat ever fails after trimming)
  --ffmpeg-zip F  use an ffmpeg zip you already have
  --no-zip        build the folder only

Check the result
  dist\SecureOfflineTranscriber\smoke-test.bat         must print SMOKE TEST PASSED
  python -m pytest tests                              (headless tests of the program logic)

No PyInstaller, no self-made .exe: this is what keeps the package clean on VirusTotal.
To sign files, sign nothing here: python.exe/pythonw.exe are already signed by the PSF.

macOS package
  python3 build/build_mac.py makes dist/SecureOfflineTranscriber-portable-macos-arm64.zip:
  Python 3.13 (python-build-standalone), the macOS wheels of the same pins, ffmpeg from the
  imageio-ffmpeg wheel, and the llama.cpp b12000 macOS arm64 libraries (SHA-256 pinned).

English spelling tables
  app\data\spelling.json comes from the English Speller Database: build its scowl.db with
  'make' and run build\tools\make-spelling-data.py <scowl.db> app\data\spelling.json.

Model list
  The downloadable models are described by app\models.json. To publish new or updated
  models, edit it and run build\tools\update-model-list.bat: it fills in commits, sizes
  and SHA-256 from Hugging Face and checks the list. Send users the file; they use
  Options > Transcription > Import model list. Details: build\MODEL-LIST.md
