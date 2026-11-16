Secure Offline Transcriber 2.4.0 - portable edition for macOS (Apple silicon)
================================================================================

Offline speech-to-text: time-stamped transcripts from recorded audio and video files
(WAV, MP3, M4A, MP4, MKV and more), processed entirely on this Mac. English, French,
Cantonese and Mandarin. No installation, no account, no internet needed after a model
is downloaded. Optional AI summary of each transcript and speaker identification,
also done on this Mac.
Same program, version, menus and settings as the Windows edition.

WHAT'S NEW IN 2.4.0
-------------------
- Speaker identification (optional): after transcribing, Transcribe > Identify Speakers
  (Ctrl+D) finds who spoke when, on this Mac, and starts each paragraph with the speaker:
  a marker, the name and a colon. Type names in the new Speakers panel beside the
  transcript; a name changes everywhere. Click a name in the transcript, or press Option+1
  to Option+9, to give one paragraph to another speaker. A paragraph marked (?) may belong
  to someone else.
- Two small models do the work (about 30 MB, downloaded once in Options > Speakers):
  pyannote segmentation 3.0 (MIT licence) and the 3D-Speaker CAM++ voice model
  (Apache 2.0). They run with the sherpa-onnx library the program already uses.
- Two-channel recordings (Voice Recorder Pro 1.0.3, Format > Channels > Separate):
  the left channel is you, the right channel the other participants; the louder side
  decides each sentence. The recorder's channel tag is found automatically.
- Voice library (Options > Voice Library): with a person's agreement, save their
  voiceprint (192 numbers, no audio) so later recordings get their name automatically.
  It stays on this Mac, in data/voices.json; delete any entry at any time.
- Exports (text, Word, Markdown, subtitles, JSON, CSV) and the AI summary use the names.
- Options > Speakers: identify automatically after transcribing, number of speakers,
  two-channel handling, voice library matching and the speaker models.

WHAT'S NEW IN 2.3.1
-------------------
- AI summary (optional, off until you turn it on in Options > AI Summary): when a
  transcript is complete, a small language model on this Mac writes a summary, key points,
  decisions, action items and open questions, each with the time it was discussed. It
  appears in an AI summary tab next to Transcript and Raw output; you can keep reading and
  playing the transcript while it is written. Check summaries against the transcript:
  small language models can get details wrong. Click a time in the summary to hear it.
- Six summary models to download in Options > AI Summary: Qwen3-1.7B, Qwen3-4B-Instruct-2507,
  Qwen3.5-2B, Qwen3.5-4B, Gemma 4 E2B and Gemma 4 E4B (1.8 to 5.0 GB; Apache 2.0 licence).
  The summary engine is llama.cpp (folder llamacpp): libraries only, no new program. The
  model runs on the Mac's graphics chip (Metal) when it fits in the memory macOS lets the
  graphics chip use, otherwise on the processor; Options > AI Summary > "Use the graphics
  chip (Mac)" turns this off.
- English spelling for your country: American (color, organize), British (colour,
  organise), Canadian (colour, organize), Australian or Hong Kong (both colour, organise).
  Options > Transcription > English spelling. Automatic, the default, follows the Region
  set in System Settings > General > Language & Region. The transcript, its exports and
  the AI summary follow it; names and your vocabulary terms are kept. Edit > Change English
  Spelling changes a transcript that is already open.
- Options > Transcription and Options > AI Summary have Import model list and Use built-in
  list: one model list file holds both kinds of model.
- With Offline Mode on, the Transcription and AI Summary tabs say in red why downloading
  is not possible and offer a "Turn Offline Mode off" button.
- Fixed: the amber text for "Update available" and for a hotword count near its limit was
  hard to read; it is now clear in light and dark themes.
- Windows and macOS now have the same version number and the same features.

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
llamacpp/             AI summary engine (llama.cpp libraries, unmodified; see SOURCE.txt)
models/               downloaded speech and summary models (none are included)
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
Python code and libraries, including the AI summary engine in llamacpp/ (llama.cpp's
official macOS libraries, unmodified, with their own signatures). To check the files:
double-click verify-files.command.

PRIVACY
-------
Audio, transcripts and AI summaries never leave this Mac. There is no telemetry,
analytics or update server. The network is used only when you start a model download;
Options > Offline Mode blocks even that. Options > Advanced > Privacy check shows the
details. llama.cpp's libraries include its network component (libggml-rpc), because its
other libraries link to it; it connects only to a server address it is given, and this
program never gives it one.

NEW MODELS
----------
The models offered for download come from a model list file (models.json). If you are sent
a newer one, choose Options > Transcription (or AI Summary) > Import model list... (or drop
it on the window). New models appear in the list and models you have show "Update
available" when a newer version is listed. Your installed models keep working.

MOVING TO ANOTHER MAC
---------------------
Copy the whole folder. Models in the models folder come along. For a Mac without
internet, use Options > Transcription > Export model pack / Import model pack.

Help: press F1 in the program (fn+F1 on most Mac keyboards), or open app/docs/HELP.html.
Support this program with a donation: https://software.4u.hk

Copyright (c) 2026 Neo Time Consulting. See LICENSE.txt.
