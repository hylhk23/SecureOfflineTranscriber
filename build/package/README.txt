Secure Offline Transcriber 2.1.4 - portable edition for Windows 10 and 11 (64-bit)
================================================================================

Offline speech-to-text: time-stamped transcripts from recorded audio and video files
(WAV, MP3, M4A, MP4, MKV and more), processed entirely on this PC. English, French,
Cantonese and Mandarin. No installation, no account, no internet needed after a model
is downloaded.

WHAT'S NEW IN 2.1.4
-------------------
- English first: the recommended download is now Whisper only (Whisper small, 486 MB,
  or Whisper large-v3-turbo, 1.6 GB, on PCs with 12 GB of memory or more). To add
  SenseVoiceSmall for Cantonese and Mandarin (240 MB), tick the box at the first start,
  or set the language to Cantonese or Mandarin in Options > Transcription.
- The model list and the help now start with the models for English.

START
-----
1. Before unzipping, right-click the zip > Properties > tick "Unblock" > OK.
   (Windows marks downloaded files; unblocking avoids SmartScreen prompts.)
2. Unzip with 7-Zip or Windows (Extract All) to a folder you can write to, e.g. D:\Tools\SecureOfflineTranscriber
   (not C:\Program Files - the program keeps its settings in its own folder).
3. Optional: double-click verify-files.bat - it checks every file arrived complete.
4. Double-click run.bat.  create-shortcut.bat adds desktop and Start-menu shortcuts.
5. First start: download the recommended model (Whisper, 0.5 to 1.6 GB depending on
   your PC's memory; tick the box to add SenseVoiceSmall for Cantonese and Mandarin),
   or add a folder where you already keep models (for example version 2.0's
   local_whisper folders).

UPDATE
------
1. Download and extract the new zip.
2. Before deleting the old folder, copy its data folder (settings and vocabulary) and
   its models folder (downloaded models) into the new SecureOfflineTranscriber folder,
   replacing the empty ones.
Transcripts in Documents\SOT Transcripts are not affected.

REMOVE
------
1. Close the program.
2. If you registered it under Options > Advanced > File types, click Unregister first.
   Otherwise Windows "Open with" keeps listing the program after its folder is gone.
3. If you created shortcuts, run create-shortcut.bat /remove.
4. Delete the SecureOfflineTranscriber folder.
Your transcripts stay in Documents\SOT Transcripts. (Only if the program folder was
read-only were settings kept in %LOCALAPPDATA%\SecureOfflineTranscriber; delete that too.)

FILES
-----
run.bat               start the program (no console window)
run-debug.bat         start with a console showing the log, for troubleshooting
smoke-test.bat        check that every library loads; closes by itself
create-shortcut.bat   desktop and Start-menu shortcuts (/remove deletes them)
verify-files.bat      check every file against SHA256SUMS.txt after unzipping
app\                  the program (Python source code, readable) and docs\HELP.html
python\               Python 3.13 embeddable package from python.org
ffmpeg\ffmpeg.exe     reads audio and video files (unmodified FFmpeg build)
models\               downloaded speech models (none are included)
data\                 settings, logs (kept 7 days), temporary files
LICENSE.txt, THIRD-PARTY-LICENSES.txt, SHA256SUMS.txt

ANTIVIRUS SAFETY
----------------
This package contains only three programs:
  python\python.exe and python\pythonw.exe - from python.org, digitally signed by the
                                           Python Software Foundation
  ffmpeg\ffmpeg.exe                        - FFmpeg essentials build by gyan.dev,
                                           unmodified (not signed; see ffmpeg\SOURCE.txt)
There is no self-made .exe, no installer and no packer. Everything else is Python code
and libraries. To check any file against SHA256SUMS.txt, open a Command Prompt here:
  certutil -hashfile ffmpeg\ffmpeg.exe SHA256

PRIVACY
-------
Audio and transcripts never leave this PC. There is no telemetry, analytics or update
server. The network is used only when you start a model download; Options > Offline Mode
blocks even that. Options > Advanced > Privacy check shows the details.

NEW MODELS
----------
The models offered for download come from a model list file (models.json). If you are sent
a newer one, choose Options > Transcription > Import model list... (or drop it on the window).
New models appear in the list and models you have show "Update available" when a newer
version is listed. Your installed models keep working.

MOVING TO ANOTHER PC
--------------------
Copy the whole folder. Models in the models folder come along. For a PC without
internet, use Options > Transcription > Export model pack / Import model pack.

Help: press F1 in the program, or open app\docs\HELP.html.
Support this program with a donation: https://software.4u.hk

Copyright (c) 2026 Neo Time Consulting. See LICENSE.txt.
