"""Headless tests (no model needed). Run: python -m pytest tests"""
import json
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from sot import exporters, postproc, session  # noqa: E402
from sot.engines import detect_type, strip_hotword_echo  # noqa: E402
from sot.settings import DEFAULTS, Settings  # noqa: E402


def S(**over):
    s = Settings(values={})
    for k, v in over.items():
        s.set(k.replace("__", "."), v)
    return s


def test_defaults_match_spec():
    assert DEFAULTS["output.formats"] == ["txt_times", "srt"]
    assert DEFAULTS["output.pattern"] == "{name}_stt_{model}"
    assert DEFAULTS["transcription.script_yue"] == "hk" and DEFAULTS["transcription.script_zh"] == "cn"
    assert DEFAULTS["advanced.carry_previous"] is False


def test_settings_roundtrip(tmp_path):
    s = Settings(path=str(tmp_path / "settings.json"))
    s.set("output.formats", ["docx"])
    s.save()
    s2 = Settings(path=str(tmp_path / "settings.json"))
    assert s2.get("output.formats") == ["docx"]
    s2.export_to(str(tmp_path / "x.json"))
    s3 = Settings(path=str(tmp_path / "other.json"))
    s3.import_from(str(tmp_path / "x.json"))
    assert s3.get("output.formats") == ["docx"]


def test_import_rejects_foreign(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"app": "Other"}))
    with pytest.raises(ValueError):
        Settings(path=str(tmp_path / "s.json")).import_from(str(p))


def test_replacements_whole_word_and_log():
    reps = [{"heard": "Nova Lyte", "correct": "Novalite", "whole": True, "case": False}]
    out, log = postproc.apply_replacements("nova lyte is nice. Nova Lytes", reps)
    assert out == "Novalite is nice. Nova Lytes"
    assert log == [("Nova Lyte", "Novalite", 1)]


def test_chinese_replacement_follows_script():
    reps = [{"heard": "粵語", "correct": "廣東話", "whole": True, "case": False}]
    out, log = postproc.apply_replacements("粤语很好", reps, "cn")  # one entry, either script
    assert out == "广东话很好" and log


def test_filter_known_and_repeats():
    s = S()
    segs = [{"start": i, "end": i + 1, "text": t} for i, t in enumerate(
        ["Hello", "Thank you for watching.", "Again", "Again", "Again", "Again", "!!"])]
    kept, dropped = postproc.filter_segments(segs, s)
    texts = [k["text"] for k in kept]
    assert "Thank you for watching." not in texts and "!!" not in texts
    assert texts.count("Again") == 3
    assert len(dropped) == 3


def test_paragraphs_split_on_pause():
    segs = [{"start": 0, "end": 1, "text": "a"}, {"start": 1.5, "end": 2, "text": "b"},
            {"start": 6, "end": 7, "text": "c"}]
    p = postproc.paragraphs(segs, pause=2.0)
    assert [x["text"] for x in p] == ["a b", "c"] and p[0]["segs"] == [0, 1]


def test_hotword_echo():
    hw = ["Marta Quinteiro", "Novalite"]
    assert strip_hotword_echo("seeing you thinking Marta Quinteiro Novalite", hw) == ("seeing you thinking", True)
    assert strip_hotword_echo("we met Marta Quinteiro.", hw)[1] is False
    assert strip_hotword_echo("going to Novalite", hw)[1] is False


@pytest.mark.parametrize("fmt", list(exporters.FORMATS))
def test_every_format_writes(tmp_path, fmt):
    blocks = [{"start": 1.0, "end": 9.5, "text": "Hello there. This is a fairly long sentence that has to be split "
               "for subtitles, because it is long.", "lang": "en"},
              {"start": 12.0, "end": 14.0, "text": "粵語測試，中文字。", "lang": "yue"}]
    meta = {"stem": "x", "file": "x.mp3", "duration": 20, "language": "en", "language_name": "English",
            "model": "Test", "model_short": "test", "stats_line": "Time 00:00:01", "version": "2.1.0"}
    p = exporters.target_path(str(tmp_path), "x", fmt)
    exporters.write(fmt, p, blocks, meta, S())
    data = open(p, "rb").read()
    assert len(data) > 20
    if fmt in ("txt_times", "txt_plain", "srt", "vtt", "md", "csv"):
        text = data.decode("utf-8-sig")
        assert "粵語測試" in text and "\r\n" in text and "\n\n" not in text.replace("\r\n", "")  # CRLF only
    if fmt == "csv":
        assert data.startswith(b"\xef\xbb\xbf")  # BOM so Excel shows Chinese


def test_srt_cue_limits(tmp_path):
    long = " ".join(["word"] * 60) + "."
    cues = exporters.build_cues([{"start": 0, "end": 30, "text": long}], chars=42, lines=2, max_dur=7, min_dur=1)
    assert all(len(l) <= 42 for c in cues for l in c["lines"])
    assert all(len(c["lines"]) <= 2 for c in cues)
    assert all(c["end"] - c["start"] <= 7.01 for c in cues)


def test_unique_names(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x")
    assert exporters.unique(str(p), "number").endswith("a (2).txt")
    assert exporters.unique(str(p), "overwrite") == str(p)


def test_session_roundtrip(tmp_path):
    p = str(tmp_path / "s.sot.json")
    session.save(p, {"segments": [{"start": 0, "end": 1, "text": "x"}]})
    assert session.load(p)["segments"][0]["text"] == "x"


def test_detect_type(tmp_path):
    d = tmp_path / "w"
    d.mkdir()
    for n in ("model.bin", "config.json", "tokenizer.json", "vocabulary.txt"):
        (d / n).write_text("x")
    assert detect_type(str(d))[0] == "whisper"
    d2 = tmp_path / "sv"
    d2.mkdir()
    (d2 / "model.int8.onnx").write_text("x")
    kind, missing = detect_type(str(d2))
    assert kind is None and missing == ["tokens.txt"]


def test_boost_quiet_audio():
    from sot.audioproc import boost_quiet_audio
    a = (np.sin(np.linspace(0, 2000, 16000 * 5)) * 0.003).astype(np.float32)
    b, gain = boost_quiet_audio(a, -23.0, 35.0)
    assert 0 < gain <= 35 and np.abs(b).max() <= 1.0


# ---------------------------------------------------------------- model list (models.json)
import copy as _copy  # noqa: E402

from sot import modellist  # noqa: E402


def _builtin():
    return modellist.read(modellist.BUILTIN)


def test_builtin_list_is_valid():
    data, warn = modellist.validate(_builtin())
    assert data["format"] == 2 and len(data["models"]) >= 5
    assert set(data["automatic"]) == {"en", "fr", "yue", "zh"}


@pytest.mark.parametrize("change, words", [
    (lambda d: d["models"][0]["files"][0].__setitem__("name", "../x.dll"), "not allowed"),
    (lambda d: d["models"][0].__setitem__("id", "..\\models"), "id must be"),
    (lambda d: d["models"][0].__setitem__("kind", "vibevoice"), "new kind"),
    (lambda d: d["models"][0].__setitem__("revision", "main"), "40-character"),
    (lambda d: d["models"][0]["files"][0].__setitem__("sha256", ""), "SHA-256"),
    (lambda d: d.__setitem__("min_app_version", "99.0"), "needs program version"),
    (lambda d: d["automatic"].__setitem__("en", ["no-such-model"]), "not in the list"),
    (lambda d: d["models"][1]["files"].pop(), "do not make a"),
])
def test_bad_lists_are_refused(change, words):
    d = _copy.deepcopy(_builtin())
    change(d)
    with pytest.raises(modellist.ListError) as ex:
        modellist.validate(d)
    assert words in str(ex.value)


def test_diff():
    old = _builtin()
    new = _copy.deepcopy(old)
    new["models"] = new["models"][1:]
    new["models"][0]["revision"] = "f" * 40
    added, removed, changed = modellist.diff(old, new)
    assert removed == [old["models"][0]["name"]] and changed == [new["models"][0]["name"]] and not added


def test_old_per_language_setting_follows_the_list(tmp_path):
    from sot.settings import OLD_PER_LANGUAGE
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"app": "SecureOfflineTranscriber", "values":
                             {"transcription.per_language": OLD_PER_LANGUAGE}}))
    assert Settings(path=str(p)).get("transcription.per_language") == {}


# ---------------------------------------------------------------- 2.1.2
def test_help_documents_every_setting():
    """Every user setting has a row in docs/HELP.html with id="set-<key>" (values, default, implications)."""
    from sot.settings import DEFAULTS
    internal = {"general.recent", "general.geometry", "general.last_options_tab", "general.first_run_done",
                "transcription.location_cache", "advanced.offline_auto_done"}
    page = open(os.path.join(ROOT, "app", "docs", "HELP.html"), encoding="utf-8").read()
    missing = [k for k in DEFAULTS if k not in internal and f'id="set-{k}"' not in page]
    assert not missing, missing


def test_help_lists_every_menu_item():
    page = open(os.path.join(ROOT, "app", "docs", "HELP.html"), encoding="utf-8").read()
    src = open(os.path.join(ROOT, "app", "sot", "ui", "mainwindow.py"), encoding="utf-8").read()
    import re as _re
    items = set(_re.findall(r'self\._act\(m, "([^"\\]+)', src))
    items = {i.replace("&", "").replace("…", "").strip() for i in items}
    rows = [set(_re.findall(r"\w+", r.lower())) for r in _re.findall(r"<tr.*?</tr>", page, _re.S)]
    missing = [i for i in items if not any(set(_re.findall(r"\w+", i.lower())) <= r for r in rows)]
    assert not missing, missing


def test_title_has_version():
    from sot import APP_TITLE, VERSION
    assert APP_TITLE == f"Secure Offline Transcriber ({VERSION})"


def test_trim_overlap():
    from sot.postproc import trim_overlap
    assert trim_overlap("so then Paul can", "can also tell them") == "also tell them"
    assert trim_overlap("I think so.", "So we go there") == "So we go there"
    assert trim_overlap("Hello there", "General Kenobi") == "General Kenobi"


def test_paragraph_sentence_positions():
    segs = [{"start": 0, "end": 1, "text": "Hello there."}, {"start": 1.2, "end": 2, "text": "How are you?", "flag": "check"}]
    p = postproc.paragraphs(segs)[0]
    assert [p["text"][s["c0"]:s["c1"]] for s in p["subs"]] == ["Hello there.", "How are you?"]
    assert p["subs"][1]["flag"] == "check"


def test_noise_filters_and_decode_command(monkeypatch):
    from sot import media
    assert media.noise_filters("off") == [] and "afftdn" in ",".join(media.noise_filters("light"))
    seen = {}

    class R:
        returncode, stdout, stderr = 0, b"\x00" * 8, b""
    monkeypatch.setattr(media, "_ffmpeg_or_raise", lambda: "ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", lambda cmd, **k: seen.setdefault("cmd", cmd) and R())
    media.decode("x.mp3", 0, 12.5, 30, "left", "strong")
    cmd = " ".join(seen["cmd"])
    assert "-ss 12.500" in cmd and "pan=mono|c0=c0,highpass=f=100,afftdn" in cmd and "-ac 1" in cmd


def test_names_from_text_and_tables():
    from sot import names
    rows = [["First name", "Last name", "Company"], ["Marta", "Quinteiro", "Câmara Municipal de Évora"]]
    text = ("Dear Paul, we meet Helena Brandt and Teodoro Valente of BrightNode at the Embassy of Canada.\n"
            "From: \"Quinteiro, Marta\" <marta.quinteiro@example.org>")
    found = {c["text"]: c for c in names.extract(text, rows)}
    for n in ("Marta Quinteiro", "Helena Brandt", "Teodoro Valente", "Embassy of Canada", "Câmara Municipal de Évora",
              "BrightNode"):
        assert n in found and found[n]["checked"], n
    assert "Dear Paul" not in found and "First name" not in found
    assert names.merge_hotwords(["Novalite", "Marta Quinteiro"], ["marta quinteiro", "BrightNode"]) == ["marta quinteiro", "BrightNode", "Novalite"]


def test_names_read_xlsx_and_docx(tmp_path):
    import openpyxl
    from docx import Document
    from sot import names
    wb = openpyxl.Workbook()
    wb.active.append(["Name", "Organisation"])
    wb.active.append(["Iris Okafor", "Northwind Trade Agency"])
    wb.save(tmp_path / "a.xlsx")
    text, rows = names.read_document(str(tmp_path / "a.xlsx"))
    assert rows[1] == ["Iris Okafor", "Northwind Trade Agency"]
    d = Document()
    d.add_paragraph("Meeting with Nadia Ferro.")
    d.save(tmp_path / "b.docx")
    text, rows = names.read_document(str(tmp_path / "b.docx"))
    assert "Nadia Ferro" in text
    with pytest.raises(names.DocumentError):
        names.read_document(str(tmp_path / "c.msg"))


# ---------------------------------------------------------------- 2.1.4: English first
def test_recommended_set_is_english_first(monkeypatch):
    from sot import modelmgr
    mgr = modelmgr.ModelManager(Settings(values={}))
    for gb, base in ((8.0, "whisper-small"), (16.0, "whisper-large-v3-turbo")):
        monkeypatch.setattr(modelmgr, "ram_gb", lambda gb=gb: (gb, gb / 2))
        assert mgr.recommended() == [base]
        assert mgr.recommended(cantonese_mandarin=True) == [base, "sensevoice-small-int8"]
    assert "en" in mgr.catalog[0]["languages"]


def test_bad_cantonese_mandarin_entry_is_refused():
    d = _copy.deepcopy(_builtin())
    d["recommended"]["cantonese_mandarin"] = ["no-such-model"]
    with pytest.raises(modellist.ListError) as e:
        modellist.validate(d)
    assert "not in the list" in str(e.value)


def test_help_quick_guide_starts_with_english():
    import re as _re
    html = open(os.path.join(os.path.dirname(__file__), "..", "app", "docs", "HELP.html"), encoding="utf-8").read()
    guide = html[html.index('id="choose"'):]
    first = _re.search(r"<tr><td>(.*?)</td>", guide).group(1)
    assert first.startswith("English")


def test_first_run_offers_cantonese_mandarin():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from sot.ui.dialogs import FirstRunDialog
    app = QApplication.instance() or QApplication([])
    dlg = FirstRunDialog(None, "486 MB", "Whisper small", "I also transcribe Cantonese or Mandarin: add SenseVoiceSmall (240 MB)")
    assert dlg.extra is not None and not dlg.wants_cantonese_mandarin()
    dlg.extra.setChecked(True)
    assert dlg.wants_cantonese_mandarin()
    assert app is not None


# ---------------------------------------------------------------- macOS package
def test_mac_memory_and_ports_parsing(monkeypatch):
    import subprocess as _sp
    from sot import modelmgr, system
    vm = ("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
          "Pages free:                               100000.\nPages active:                             300000.\n"
          "Pages inactive:                           50000.\nPages speculative:                        10000.\n")
    lsof = ("COMMAND   PID USER   FD   TYPE DEVICE SIZE/OFF NODE NAME\n"
            "python3 4242 me   7u  IPv4 0x1      0t0  TCP 127.0.0.1:5000 (LISTEN)\n")

    class R:
        def __init__(self, out):
            self.stdout = out
    monkeypatch.setattr(_sp, "run", lambda cmd, **kw: R(vm if cmd[0] == "vm_stat" else lsof if "-iTCP" in cmd else ""))
    monkeypatch.setattr(os, "sysconf", lambda k: 16384 if k == "SC_PAGE_SIZE" else 1048576)
    total, avail = modelmgr._mac_ram()
    assert round(total) == 16 and round(avail, 2) == round(160000 * 16384 / 2 ** 30, 2)
    assert system._mac_listening_ports() == ["TCP 127.0.0.1:5000"]


def test_mac_fallback_folder(monkeypatch):
    from sot import paths
    monkeypatch.setattr(sys, "platform", "darwin")
    assert paths.fallback_dir().endswith(os.path.join("Library", "Application Support", "SecureOfflineTranscriber"))


def test_package_readmes_match_version():
    from sot import VERSION
    root = os.path.join(os.path.dirname(__file__), "..", "build")
    for sub in ("package", "package-mac"):
        text = open(os.path.join(root, sub, "README.txt"), encoding="utf-8").read()
        assert text.startswith(f"Secure Offline Transcriber {VERSION} - portable edition")
        assert f"WHAT'S NEW IN {VERSION}" in text


# ---------------------------------------------------------------- issue #1: voice detection in 30-s windows
def _drifting_vad(monkeypatch, good_s=60.0):
    """A stand-in for Silero whose state drifts: it finds speech only in the first good_s seconds of whatever
    audio it is given (the behaviour reported in issue #1). Records every call."""
    from faster_whisper import vad
    calls = []

    def fake(audio, vad_options=None, sampling_rate=16000, **kw):
        calls.append(len(audio))
        end = min(len(audio), int(good_s * sampling_rate))
        return [{"start": 0, "end": end}] if end > 0 else []
    monkeypatch.setattr(vad, "get_speech_timestamps", fake)
    return calls


def test_windowed_vad_recovers_speech_lost_to_drift(monkeypatch):
    from sot import audioproc
    from faster_whisper.vad import VadOptions
    calls = _drifting_vad(monkeypatch)
    audio = np.zeros(600 * 16000, np.float32)  # 10 minutes, "speech" throughout
    whole = __import__("faster_whisper").vad.get_speech_timestamps(audio, VadOptions())
    assert sum(t["end"] - t["start"] for t in whole) == 60 * 16000  # the drift: 60 s of 600 s
    calls.clear()
    ts = audioproc.windowed_speech_timestamps(audio, VadOptions())
    assert len(calls) == 20 and max(calls) == 31 * 16000  # 30-s windows with 1 s before each
    assert ts == [{"start": 0, "end": 600 * 16000}]  # all of it, joined across the window boundaries


def test_windowed_vad_offsets_cap_and_short_audio(monkeypatch):
    from sot import audioproc
    from faster_whisper import vad
    from faster_whisper.vad import VadOptions
    seen = []

    def fake(audio, vad_options=None, sampling_rate=16000, **kw):  # speech from 10 s to 12 s of each window
        seen.append(len(audio))
        return [{"start": 10 * 16000, "end": 12 * 16000}] if len(audio) > 12 * 16000 else []
    monkeypatch.setattr(vad, "get_speech_timestamps", fake)
    ts = audioproc.windowed_speech_timestamps(np.zeros(90 * 16000, np.float32), VadOptions())
    assert [(t["start"] / 16000, t["end"] / 16000) for t in ts] == [(10, 12), (39, 41), (69, 71)]
    seen.clear()
    audioproc.windowed_speech_timestamps(np.zeros(20 * 16000, np.float32), VadOptions())
    assert seen == [20 * 16000]  # short audio: one call, as before
    _drifting_vad(monkeypatch, good_s=1000)
    ts = audioproc.windowed_speech_timestamps(np.zeros(100 * 16000, np.float32), VadOptions(max_speech_duration_s=15))
    assert max(t["end"] - t["start"] for t in ts) <= 15 * 16000 and ts[-1]["end"] == 100 * 16000


def test_speech_pieces_use_windows(monkeypatch):
    from sot import audioproc
    _drifting_vad(monkeypatch)
    pieces = audioproc.speech_pieces(np.zeros(300 * 16000, np.float32), S(), 15)
    assert sum(e - b for b, e in pieces) == 300 * 16000 and max(e - b for b, e in pieces) <= 15 * 16000


def test_whisper_uses_windowed_vad():
    """Whisper's transcribe() and detect_language() look the name up in faster_whisper.transcribe."""
    import faster_whisper
    import faster_whisper.transcribe as fwt
    from sot import audioproc
    assert faster_whisper.__version__ == "1.2.1"  # a new version: check where it calls the VAD, then update this
    src = open(fwt.__file__, encoding="utf-8").read()
    assert src.count("get_speech_timestamps(audio, vad_parameters)") >= 2
    audioproc.install_windowed_vad()
    assert fwt.get_speech_timestamps is audioproc.windowed_speech_timestamps
    audioproc.install_windowed_vad()  # twice is harmless
    assert fwt.get_speech_timestamps is audioproc.windowed_speech_timestamps


def test_low_speech_warning_and_silent_stretches():
    from sot import audioproc
    segs = [{"start": 5, "end": 300}, {"start": 960, "end": 1000}]
    assert audioproc.silent_stretches(segs, 1800) == [(300.0, 960.0), (1000.0, 1800.0)]
    assert audioproc.silent_stretches(segs, 1030) == [(300.0, 960.0)]
    assert round(audioproc.low_speech(308, 1800), 2) == 0.17  # the issue's first block
    assert audioproc.low_speech(1667, 1800) is None
    assert audioproc.low_speech(10, 60) is None  # short files are not judged
