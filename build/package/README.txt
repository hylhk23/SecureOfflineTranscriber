Secure Offline Transcriber 2.4.0 - portable edition for Windows 10 and 11 (64-bit)
================================================================================

Offline speech-to-text: time-stamped transcripts from recorded audio and video files
(WAV, MP3, M4A, MP4, MKV and more), processed entirely on this PC. English, French,
Cantonese and Mandarin. No installation, no account, no internet needed after a model
is downloaded. Optional AI summary of each transcript and speaker identification,
also done on this PC.

WHAT'S NEW IN 2.4.0
-------------------
- Speaker identification (optional): after transcribing, Transcribe > Identify Speakers
  (Ctrl+D) finds who spoke when, on this PC, and starts each paragraph with the speaker:
  a marker, the name and a colon. Type names in the new Speakers panel beside the
  transcript; a name changes everywhere. Click a name in the transcript, or press Alt+1
  to Alt+9, to give one paragraph to another speaker. A paragraph marked (?) may belong
  to someone else.
- Two small models do the work (about 30 MB, downloaded once in Options > Speakers):
  pyannote segmentation 3.0 (MIT licence) and the 3D-Speaker CAM++ voice model
  (Apache 2.0). They run with the sherpa-onnx library the program already uses.
- Two-channel recordings (Voice Recorder Pro 1.0.3, Format > Channels > Separate):
  the left channel is you, the right channel the other participants; the louder side
  decides each sentence. The recorder's channel tag is found automatically.
- Voice library (Options > Voice Library): with a person's agreement, save their
  voiceprint (192 numbers, no audio) so later recordings get their name automatically.
  It stays on this PC, in data\voices.json; delete any entry at any time.
- Exports (text, Word, Markdown, subtitles, JSON, CSV) and the AI summary use the names.
- Options > Speakers: identify automatically after transcribing, number of speakers,
  two-channel handling, voice library matching and the speaker models.

WHAT'S NEW IN 2.3.1
-------------------
- English spelling for your country: American (color, organize), British (colour,
  organise), Canadian (colour, organize), Australian or Hong Kong (both colour, organise).
  Options > Transcription > English spelling. Automatic, the default, follows the country
  or region set in Windows. The transcript, its exports and the AI summary follow it;
  names and your vocabulary terms are kept. Edit > Change English Spelling changes a
  transcript that is already open. Help (F1) has a new section, English spelling.
- Options > AI Summary has Import model list and Use built-in list, as on the
  Transcription tab: one model list file holds both kinds of model.
- With Offline Mode on, the Transcription and AI Summary tabs say in red why downloading
  is not possible and offer a "Turn Offline Mode off" button.
- Fixed: after Import model list, the AI summary models were missing from Options >
  AI Summary until the program was started again.
- Fixed: the amber text for "Update available" and for a hotword count near its limit was
  hard to read; it is now clear in light and dark themes.
- Windows and macOS now have the same version number and the same features.

WHAT'S NEW IN 2.3.0
-------------------
- AI summary (optional, off until you turn it on in Options > AI Summary): when a
  transcript is complete, a small language model on this PC writes a summary, key points,
  decisions, action items and open questions, each with the time it was discussed. It
  appears in an AI summary tab next to Transcript and Raw output; you can keep reading and
  playing the transcript while it is written.
- Six summary models to download in Options > AI Summary: Qwen3-1.7B, Qwen3-4B-Instruct-2507,
  Qwen3.5-2B, Qwen3.5-4B, Gemma 4 E2B and Gemma 4 E4B (1.8 to 5.0 GB; Apache 2.0 licence).
- The summary engine is llama.cpp (folder llamacpp): libraries only, no new program.
- Help (F1) has a new chapter, AI summary. Check summaries against the transcript: small
  language models can get details wrong. Click a time in the summary to hear that part.
- Fixed (also in 2.2.1): on some long recordings voice detection reported most of the
  speech as silence, so large parts of the transcript were missing (GitHub issue #1). It
  now listens in 30-second windows. If less than 40% of a recording is recognised as
  speech, a warning says so, and Raw output lists the stretches of a minute or more
  without text.
- New safety net (also in 2.2.1), on by default: Options > Advanced > Voice detection >
  "Check stretches without speech". Where no speech was detected for 20 seconds or more,
  the model still listens and keeps only text it is sure of. Silent parts are skipped.
- New for Whisper models (also in 2.2.1): "Voice detection off (Whisper only)" in the same
  place. Whisper then reads the whole recording, silence included: nothing is skipped, but
  it is slower and may invent text in silence, music or noise.

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
llamacpp\             AI summary engine (llama.cpp libraries, unmodified; see SOURCE.txt)
models\               downloaded speech and summary models (none are included)
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
and libraries, including the AI summary engine in llamacpp\ (llama.cpp's official
libraries, unmodified and not signed; its network component is left out). To check any file against SHA256SUMS.txt, open a Command Prompt here:
  certutil -hashfile ffmpeg\ffmpeg.exe SHA256

PRIVACY
-------
Audio, transcripts and AI summaries never leave this PC. There is no telemetry,
analytics or update server. The network is used only when you start a model download; Options > Offline Mode
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
