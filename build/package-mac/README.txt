Secure Offline Transcriber 2.2.1 - portable edition for macOS (Apple silicon)
================================================================================

Offline speech-to-text: time-stamped transcripts from recorded audio and video files
(WAV, MP3, M4A, MP4, MKV and more), processed entirely on this Mac. English, French,
Cantonese and Mandarin. No installation, no account, no internet needed after a model
is downloaded. Same program, menus and settings as the Windows edition.

WHAT'S NEW IN 2.2.1
-------------------
- Fixed: on some long recordings voice detection reported most of the speech as silence,
  so large parts of the transcript were missing (GitHub issue #1). It now listens in
  30-second windows. If less than 40% of a recording is recognised as speech, a warning
  says so, and Raw output lists the stretches of a minute or more without text.
- New safety net, on by default: Options > Advanced > Voice detection > "Check stretches
  without speech". Where no speech was detected for 20 seconds or more, the model still
  listens and keeps only text it is sure of. Silent parts are skipped.
- New for Whisper models: "Voice detection off (Whisper only)" in the same place. Whisper
  then reads the whole recording, silence included: nothing is skipped, but it is slower
  and may invent text in silence, music or noise.

WHAT'S NEW IN 2.2.0
-------------------
- First edition for macOS (Apple silicon). The help (F1) has a section, On a Mac.

NEEDS
-----
A Mac with Apple silicon (M1, M2, M3, M4 or newer) and macOS 14 Sonoma or later.
Macs with an Intel processor are not supported: one of the speech libraries (ONNX Runtime)
is no longer built for them. To check: Apple menu > About This Mac > Chip.

START
-----
The program is not from the App Store, so macOS blocks files that came from the internet
(or by AirDrop or e-mail) until you allow them. Unzipping in Terminal avoids that:

1. Open Terminal (Applications > Utilities > Terminal).
2. Type these lines, pressing Return after each (change Downloads if the zip is elsewhere):
     cd ~/Downloads
     unzip -q SecureOfflineTranscriber-portable-macos-arm64.zip
     SecureOfflineTranscriber/run.command
3. Optional: move the SecureOfflineTranscriber folder somewhere you keep programs first,
   for example your home folder. Do not put it inside Applications if you are not an
   administrator: the program keeps its settings in its own folder.
4. First start: download the recommended model (Whisper, 0.5 to 1.6 GB depending on
   your Mac's memory; tick the box to add SenseVoiceSmall for Cantonese and Mandarin),
   or add a folder where you already keep models.

From then on, double-click run.command in the SecureOfflineTranscriber folder.
A Terminal window opens for a moment; you can close it once the program has started.

If you unzipped by double-clicking the zip in Finder, macOS may say the program "cannot be
opened" or "Apple could not verify" it. run.command offers to fix this; if macOS will not
even open run.command, type this once in Terminal (then double-click run.command again):
     xattr -dr com.apple.quarantine ~/Downloads/SecureOfflineTranscriber
(change the path if the folder is elsewhere; it removes only the "downloaded from the
internet" mark from this folder).

KEYS ON A MAC
-------------
Where the help says Ctrl, press Command: Command+O opens a recording, Command+click on a word
plays its sentence. F5 to F9 need the fn key on most Mac keyboards (fn+F9 plays or pauses).

UPDATE
------
1. Unzip the new zip (as in START).
2. Before deleting the old folder, copy its data folder (settings and vocabulary) and
   its models folder (downloaded models) into the new SecureOfflineTranscriber folder,
   replacing the empty ones.
Transcripts in Documents/SOT Transcripts are not affected.

REMOVE
------
1. Quit the program.
2. Move the SecureOfflineTranscriber folder to the Bin.
Your transcripts stay in Documents/SOT Transcripts. (Only if the program folder was
read-only were settings kept in ~/Library/Application Support/SecureOfflineTranscriber;
delete that too.) The Windows "Open with" registration does not exist on a Mac, so there is
nothing to unregister.

FILES
-----
run.command           start the program
run-debug.command     start with the log shown in Terminal, for troubleshooting
smoke-test.command    check that every library loads; closes by itself
verify-files.command  check every file against SHA256SUMS.txt after unzipping
app/                  the program (Python source code, readable) and docs/HELP.html
python/               Python 3.13 for macOS (python-build-standalone, relocatable)
ffmpeg/ffmpeg         reads audio and video files (FFmpeg 9.0, unmodified)
models/               downloaded speech models (none are included)
data/                 settings, logs (kept 7 days), temporary files
LICENSE.txt, THIRD-PARTY-LICENSES.txt, SHA256SUMS.txt

SAFETY
------
This package contains two programs:
  python/bin/python3.13 - CPython 3.13 built by the python-build-standalone project
                          (github.com/astral-sh/python-build-standalone)
  ffmpeg/ffmpeg         - FFmpeg 9.0 for Apple silicon, as shipped in the imageio-ffmpeg
                          package on PyPI (see ffmpeg/SOURCE.txt)
Both, and every library file, are signed ad hoc (as Apple silicon requires) but not
notarized by Apple, which is why macOS asks before running them. Everything else is
Python code and libraries. To check the files: double-click verify-files.command.

PRIVACY
-------
Audio and transcripts never leave this Mac. There is no telemetry, analytics or update
server. The network is used only when you start a model download; Options > Offline Mode
blocks even that. Options > Advanced > Privacy check shows the details.

MOVING TO ANOTHER MAC
---------------------
Copy the whole folder. Models in the models folder come along. For a Mac without
internet, use Options > Transcription > Export model pack / Import model pack.

Help: press F1 in the program (fn+F1 on most Mac keyboards), or open app/docs/HELP.html.
Support this program with a donation: https://software.4u.hk

Copyright (c) 2026 Neo Time Consulting. See LICENSE.txt.
