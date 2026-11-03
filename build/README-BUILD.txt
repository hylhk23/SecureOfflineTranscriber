Secure Offline Transcriber - build kit
======================================

What it makes
  dist\SecureOfflineTranscriber-2.1.0\                 the portable folder
  dist\SecureOfflineTranscriber-2.1.0-win64-portable.zip

How
  1. Install Python 3.10 or newer from python.org (only used to run pip; the package
     itself gets the Python 3.13 embeddable package).
  2. Double-click build\build.bat (or: py -3 build\build.py).
  The build downloads, checks and assembles:
    - python-3.13.17-embed-amd64.zip from python.org
    - Windows x64 wheels for CPython 3.13 listed in requirements-lock.txt
      (binary wheels only: nothing is compiled on your PC)
    - ffmpeg-9.0.2-essentials_build.zip from gyan.dev (SHA-256 pinned); only ffmpeg.exe is kept
  It then removes every .exe except python.exe, pythonw.exe and ffmpeg.exe, removes the
  Qt modules a Widgets program never loads, writes THIRD-PARTY-LICENSES.txt and
  SHA256SUMS.txt, and stops with an error if any other program is found.

Options
  --update        take the newest allowed versions from requirements.txt and rewrite the lock file
  --keep-qt       keep every Qt module (if smoke-test.bat ever fails after trimming)
  --ffmpeg-zip F  use an ffmpeg zip you already have
  --no-zip        build the folder only

Check the result
  dist\SecureOfflineTranscriber-2.1.0\smoke-test.bat   must print SMOKE TEST PASSED
  python -m pytest tests                              (headless tests of the program logic)

No PyInstaller, no self-made .exe: this is what keeps the package clean on VirusTotal.
To sign files, sign nothing here: python.exe/pythonw.exe are already signed by the PSF.

Model list
  The downloadable models are described by app\models.json. To publish new or updated
  models, edit it and run build\tools\update-model-list.bat: it fills in commits, sizes
  and SHA-256 from Hugging Face and checks the list. Send users the file; they use
  Options > Transcription > Import model list. Details: build\MODEL-LIST.md
