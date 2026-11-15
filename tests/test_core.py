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
    """Both package READMEs (Windows and macOS) and the help page name this version: from 2.3.1 they are the same."""
    from sot import VERSION
    root = os.path.join(os.path.dirname(__file__), "..", "build")
    for sub in ("package", "package-mac"):
        text = open(os.path.join(root, sub, "README.txt"), encoding="utf-8").read()
        assert text.startswith(f"Secure Offline Transcriber {VERSION} - portable edition"), sub
        assert f"WHAT'S NEW IN {VERSION}" in text, sub
    page = open(os.path.join(os.path.dirname(__file__), "..", "app", "docs", "HELP.html"), encoding="utf-8").read()
    assert f"<h1>Secure Offline Transcriber {VERSION}</h1>" in page


# ---------------------------------------------------------------- 2.3.0: AI summary
def test_llm_structs_match_llama_cpp_b12000():
    """Sizes and offsets measured with a C probe compiled against llama.h of release b12000."""
    import ctypes
    from sot import llm
    assert llm.LLAMA_BUILD == "b12000"
    assert ctypes.sizeof(llm.ModelParams) == 80 and ctypes.sizeof(llm.ContextParams) == 168
    assert ctypes.sizeof(llm.Batch) == 56 and ctypes.sizeof(llm.ChainParams) == 1
    off = {"n_gpu_layers": 16, "load_mode": 24, "tensor_split": 40, "progress_callback": 48, "kv_overrides": 64,
           "vocab_only": 72, "load_mtp": 77}
    for k, v in off.items():
        assert getattr(llm.ModelParams, k).offset == v, k
    off = {"n_threads": 28, "n_threads_batch": 32, "flash_attn_type": 52, "yarn_orig_ctx": 80, "cb_eval": 88,
           "type_k": 104, "moe_cache_size": 112, "abort_callback": 120, "abort_callback_data": 128, "embeddings": 136,
           "kv_unified": 141, "samplers": 144, "n_samplers": 152, "ctx_other": 160}
    for k, v in off.items():
        assert getattr(llm.ContextParams, k).offset == v, k
    assert llm.Batch.token.offset == 8 and llm.Batch.logits.offset == 48


def test_chat_formats_match_the_models_templates():
    """The prompts equal what each model's own chat template renders with thinking off."""
    from sot import summary
    exp = {"qwen3": "<|im_start|>system\nSYS<|im_end|>\n<|im_start|>user\nUSER<|im_end|>\n<|im_start|>assistant\n"
                    "<think>\n\n</think>\n\n",
           "qwen3-instruct": "<|im_start|>system\nSYS<|im_end|>\n<|im_start|>user\nUSER<|im_end|>\n"
                             "<|im_start|>assistant\n",
           "qwen3.5": "<|im_start|>system\nSYS<|im_end|>\n<|im_start|>user\nUSER<|im_end|>\n<|im_start|>assistant\n"
                      "<think>\n\n</think>\n\n",
           "gemma4": "<|turn>system\nSYS<turn|>\n<|turn>user\nUSER<turn|>\n<|turn>model\n"}
    for fam, want in exp.items():
        assert summary.build_prompt(fam, "SYS", "USER") == want
    from sot import modellist
    fams = {m["family"] for m in modellist.read(modellist.BUILTIN)["summary_models"]}
    assert fams <= set(summary.FAMILIES)


def test_summary_language_follows_the_transcript():
    from sot.summary import output_language, script_for_output
    assert output_language("same", "en") == "en" and output_language("same", "fr") == "fr"
    assert output_language("same", "yue", "hk") == "zh-hant" and output_language("same", "yue", "cn") == "zh-hans"
    assert output_language("same", "zh", script_zh="cn") == "zh-hans"
    assert output_language("same", "zh", script_zh="tw") == "zh-hant"
    assert output_language("fr", "zh") == "fr"
    assert script_for_output("zh-hant", "tw") == "tw" and script_for_output("zh-hans") == "cn"
    assert script_for_output("en") is None


def test_summary_clean_up():
    from sot.summary import clean_output, times_in
    raw = ("<think>\n\n</think>\n## 決定\n- 預算為八萬蚊（00:00:02）\n- 印五百份 (0:00:30)\n- 印五百份 (0:00:30)\n"
           "沒有提及。\n\n## Decisions\nNone mentioned.<|im_end|>")
    out = clean_output(raw)
    assert "<think>" not in out and "<|im_end|>" not in out
    assert "[00:00:02]" in out and out.count("印五百份") == 1 and "沒有提及" not in out
    assert "None mentioned." in out  # a section with no points keeps its 'none'
    assert times_in(out) == [2, 30]


def test_long_transcript_is_split_into_parts():
    from sot.summary import split_parts
    lines = [f"[00:00:{i:02d}] word " * 3 for i in range(40)]
    parts = split_parts(lines, [10] * 40, 95)
    assert all(len(p) <= 9 for p in parts) and sum(len(p) for p in parts) == 40
    big = split_parts(["x" * 5000], [1000], 300)
    assert len(big) > 1


class _FakeModel:
    """Stands in for llm.Model: counts characters as tokens, returns canned text."""
    def __init__(self, n_ctx):
        self.n_ctx = n_ctx
        self.prompts = []

    def count(self, text):
        return len(text) // 4

    def generate(self, prompt, max_tokens=0, on_prompt=None, on_text=None, **kw):
        self.prompts.append(prompt)
        if on_prompt:
            on_prompt(1, 1)
        out = "## Summary\nIt went well [00:00:05].\n## Decisions\n- Book the stand [00:01:40]"
        if on_text:
            on_text(out)
        return out, {"prompt_tokens": self.count(prompt), "output_tokens": 20, "prompt_s": 0.1, "gen_s": 0.2,
                     "stopped": False}


def _opts(**kw):
    o = {"family": "qwen3", "lang": "en", "length": "standard", "sections": ["overview", "decisions"], "times": True,
         "instructions": "Our company is Northwind.", "temperature": 0.3, "max_tokens": 300, "file": "m.wav",
         "duration_text": "00:10:00", "transcript_lang_name": "English"}
    o.update(kw)
    return o


def test_summary_single_pass_and_parts():
    import threading
    from sot import summary
    blocks = [{"start": i * 10, "text": "We discussed the budget and the stand for the fair. " * 3} for i in range(60)]
    events = []
    m = _FakeModel(n_ctx=32768)
    text, st = summary.run(m, blocks, _opts(), lambda *e: events.append(e), threading.Event())
    assert st["parts"] == 1 and len(m.prompts) == 1 and "[00:01:40]" in text
    assert "Northwind" in m.prompts[0] and "## Decisions" in m.prompts[0] and "## Key points" not in m.prompts[0]
    m2 = _FakeModel(n_ctx=2048)
    text, st = summary.run(m2, blocks, _opts(), lambda *e: events.append(e), threading.Event())
    assert st["parts"] > 1 and len(m2.prompts) > st["parts"]
    assert "part 1 of" in m2.prompts[0] and "notes on consecutive parts" in m2.prompts[-1]
    assert ("stage", "Writing the summary") in events


def test_summary_models_in_the_list():
    from sot import modellist
    d = modellist.read(modellist.BUILTIN)
    data, _ = modellist.validate(d)
    ids = [m["id"] for m in data["summary_models"]]
    assert ids == ["qwen3-1.7b", "qwen3-4b-instruct-2507", "qwen3.5-2b", "qwen3.5-4b", "gemma-4-e2b", "gemma-4-e4b"]
    assert all(m["license"] == "Apache-2.0" and m["files"][0]["name"].endswith(".gguf") for m in data["summary_models"])
    assert data["summary_recommended"] == {"ram_gb": 12, "below": ["qwen3-1.7b"], "at_or_above": ["qwen3-4b-instruct-2507"]}


@pytest.mark.parametrize("change, words", [
    (lambda d: d["summary_models"][0].__setitem__("family", "llama9"), "chat format"),
    (lambda d: d["summary_models"][0]["files"][0].__setitem__("name", "../x.gguf"), "not allowed"),
    (lambda d: d["summary_models"][0]["files"][0].__setitem__("name", "model.exe"), "not allowed"),
    (lambda d: d["summary_models"][1].__setitem__("revision", "main"), "40-character"),
    (lambda d: d["summary_models"][1].__setitem__("id", "whisper-small"), "used twice"),
    (lambda d: d["summary_recommended"].__setitem__("below", ["nope"]), "not in summary_models"),
])
def test_bad_summary_models_are_refused(change, words):
    d = _copy.deepcopy(_builtin())
    change(d)
    with pytest.raises(modellist.ListError) as ex:
        modellist.validate(d)
    assert words in str(ex.value)


def test_summary_model_choice_by_memory(monkeypatch, tmp_path):
    from sot import modelmgr
    s = Settings(values={"transcription.download_folder": str(tmp_path)})
    mgr = modelmgr.ModelManager(s)
    for gb, rec in ((8.0, "qwen3-1.7b"), (16.0, "qwen3-4b-instruct-2507")):
        monkeypatch.setattr(modelmgr, "ram_gb", lambda gb=gb: (gb, gb / 2))
        assert mgr.recommended_llm() == [rec] and mgr.llm_order()[0] == rec
    mgr.scan_llm()
    assert mgr.llm_choice() is None
    e = mgr.by_id["gemma-4-e2b"]
    folder = tmp_path / "gemma-4-e2b"
    folder.mkdir()
    with open(folder / e["files"][0]["name"], "wb") as f:  # a sparse file of the listed size counts as installed
        f.truncate(e["files"][0]["size"])
    mgr.scan_llm()
    m = mgr.llm_choice()
    assert m.key == "gemma-4-e2b" and mgr.llm_file(m).endswith(".gguf")
    assert all(x.kind != "llm" for x in mgr.scan().values())  # never offered for transcription


def test_summary_files_and_view(tmp_path):
    from sot import exporters
    from sot.summary import to_plain
    md = "## Summary\nA **short** meeting.\n\n## Action items\n- Ana: book the stand [00:01:40]\n* Ben: brochure"
    meta = {"file": "m.wav", "summary_model": "Qwen3-1.7B", "summary_date": "2026-11-05 10:00"}
    for fmt in ("md", "txt", "docx"):
        exporters.write_summary(fmt, str(tmp_path / f"s.{fmt}"), md, meta, S())
        assert (tmp_path / f"s.{fmt}").stat().st_size > 50
    txt = (tmp_path / "s.txt").read_text(encoding="utf-8")
    assert "**" not in txt and "- Ben: brochure" in txt and "Qwen3-1.7B" in txt
    assert to_plain(md).startswith("Summary")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from sot.ui.summaryview import SummaryView, to_html
    assert 'href="time:100"' in to_html(md) and "<li>" in to_html(md) and "<b>short</b>" in to_html(md)
    v = SummaryView()
    got = []
    v.time_clicked.connect(got.append)
    from PySide6.QtCore import QUrl
    v._anchor(QUrl("time:100"))
    assert got == [100.0]


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


# ---------------------------------------------------------------- stretches without speech, voice detection off
import threading  # noqa: E402

from sot import audioproc, engines  # noqa: E402


def test_gap_pieces_cut_quiet_and_skip_silence():
    sr = audioproc.SR
    rng = np.random.default_rng(3)
    a = np.zeros(200 * sr, np.float32)
    a[: 10 * sr] = 0.1 * rng.standard_normal(10 * sr)            # speech 0-10 s (detected)
    a[10 * sr: 80 * sr] = 0.05 * rng.standard_normal(70 * sr)    # 70 s nobody detected: must be checked
    for k in range(10, 80, 7):                                    # with short pauses to cut at
        a[k * sr: k * sr + sr // 5] = 0
    a[190 * sr:] = 0.1 * rng.standard_normal(10 * sr)            # speech 190-200 s; 80-190 s is digital silence
    speech = [(0, 10 * sr), (190 * sr, 200 * sr)]
    pieces, info = audioproc.gap_pieces(a, speech, min_gap_s=20, max_piece_s=25)
    assert info["stretches"] == 1 and abs(info["checked_s"] - 180) < 0.01
    assert abs(info["silent_s"] - 110) < 25                       # the silence is skipped, not transcribed
    assert pieces[0][0] == 10 * sr and all(e - b <= 25 * sr for b, e in pieces)
    assert all(b < 81 * sr for b, _ in pieces)                    # nothing starts in the silent part
    for b, e in pieces[:-1]:                                     # cuts land in the pauses, not in the noise
        assert np.abs(a[e - 800:e + 800]).max() < 0.2 or e >= 80 * sr
    # short stretches are left alone
    assert audioproc.gap_pieces(a, [(0, 10 * sr), (25 * sr, 200 * sr)], min_gap_s=20)[0] == []


def test_keep_gap_text():
    assert engines.keep_gap_text("我哋今日要傾下個季度嘅預算", 4.0) == (True, "")
    assert engines.keep_gap_text("We need to look at the budget again.", 3.0) == (True, "")
    assert not engines.keep_gap_text("The.", 5.0)[0]
    assert not engines.keep_gap_text("嗯", 2.0)[0]
    assert "repeats" in engines.keep_gap_text("謝謝 謝謝 謝謝 謝謝 謝謝 謝謝 謝謝 謝謝 謝謝 謝謝", 20.0)[1]
    assert "fast" in engines.keep_gap_text("one two three four five six seven eight nine ten eleven twelve", 1.0)[1]
    assert not engines.keep_gap_text("La la la", 3.0, "<|BGM|>")[0]
    assert engines.keep_gap_text("We need to look at the budget again tomorrow morning.", 4.0, "<|BGM|>")[0]


class _R:
    def __init__(self, text, event="<|Speech|>"):
        self.text, self.lang, self.emotion, self.event, self.tokens = text, "<|en|>", "<|NEUTRAL|>", event, [1, 2]


def test_sherpa_checks_stretches_in_time_order(monkeypatch):
    sr = audioproc.SR
    a = np.zeros(120 * sr, np.float32)
    a[30 * sr: 100 * sr] = 0.05 * np.random.default_rng(1).standard_normal(70 * sr)
    eng = engines.SherpaEngine("sensevoice")
    eng._build = lambda lang: None
    monkeypatch.setattr(audioproc, "speech_pieces", lambda audio, s, m: [(0, 5 * sr), (110 * sr, 115 * sr)])

    def decode(chunk):
        n = len(chunk) / sr
        return _R("Hello there." if n <= 5 else ("Missed speech found here." if n > 10 else "The."))
    eng._decode = decode
    ev = threading.Event()
    out = list(eng.transcribe(a, 0.0, "en", Settings(values={}), [], ev, lambda t: None))
    starts = [x["start"] for x in out]
    assert starts == sorted(starts) and out[0]["text"] == "Hello there." and out[-1]["text"] == "Hello there."
    found = [x for x in out if "Missed" in x["text"]]
    assert found and all("(stretch without detected speech)" in x["raw"] for x in found)
    st = eng.stats
    assert st["gap_stretches"] == 1 and st["gap_kept"] == len(found) and st["speech_s"] > 10 + 20
    off = engines.SherpaEngine("sensevoice")
    off._build, off._decode = eng._build, decode
    out2 = list(off.transcribe(a, 0.0, "en", Settings(values={"advanced.gap_check": False}), [], ev, lambda t: None))
    assert [x["text"] for x in out2] == ["Hello there.", "Hello there."] and "gap_stretches" not in off.stats


class _Seg:
    def __init__(self, start, end, text, nsp=0.05, lp=-0.3, seek=0):
        self.start, self.end, self.text, self.no_speech_prob, self.avg_logprob = start, end, text, nsp, lp
        self.compression_ratio, self.temperature, self.tokens, self.seek = 1.2, 0.0, [1, 2, 3], seek


class _Info:
    language, language_probability = "en", 0.99

    def __init__(self, after):
        self.duration_after_vad = after


class _FakeWhisper:
    def __init__(self):
        self.calls = []
        self.model = type("M", (), {"is_multilingual": True})()
        self.hf_tokenizer = type("T", (), {"token_to_id": staticmethod(lambda t: 1)})()

    def transcribe(self, audio, **kw):
        self.calls.append(kw)
        if kw["vad_filter"]:  # main pass: speech at 0-10 s and 110-120 s; the stretch end time leaks over the gap
            return iter([_Seg(0, 30.0, "First line."), _Seg(110, 118, "Last line.")]), _Info(20.0)
        n = len(audio) / audioproc.SR
        return iter([_Seg(0, n + 3, "Words the detector missed.") if n > 10 else
                     _Seg(0, n, "You", nsp=0.9)]), _Info(n)


def test_whisper_checks_stretches_and_can_switch_detection_off(monkeypatch):
    sr = audioproc.SR
    a = np.zeros(120 * sr, np.float32)
    a[12 * sr: 105 * sr] = 0.05 * np.random.default_rng(2).standard_normal(93 * sr)
    monkeypatch.setattr(audioproc, "windowed_speech_timestamps",
                        lambda audio, opts=None, sr=16000, **k: [{"start": 0, "end": 10 * sr}, {"start": 110 * sr, "end": 120 * sr}])
    eng = engines.WhisperEngine()
    eng.model = _FakeWhisper()
    ev = threading.Event()
    out = list(eng.transcribe(a, 0.0, "en", Settings(values={}), [], ev, lambda t: None))
    texts = [x["text"] for x in out]
    assert texts[0] == "First line." and texts[-1] == "Last line." and "Words the detector missed." in texts
    assert out[0]["end"] <= 10.0 + 0.01                            # clipped at the skipped stretch
    assert all(b["start"] >= a_["end"] - 0.01 for a_, b in zip(out, out[1:]))
    assert all(not c["vad_filter"] and not c["condition_on_previous_text"] for c in eng.model.calls[1:])
    assert eng.stats["gap_kept"] >= 1 and eng.stats["speech_s"] > 20
    # voice detection off: one pass over everything, no stretch check
    eng2 = engines.WhisperEngine()
    eng2.model = _FakeWhisper()
    list(eng2.transcribe(a, 0.0, "en", Settings(values={"advanced.vad_off": True}), [], ev, lambda t: None))
    assert len(eng2.model.calls) == 1 and eng2.model.calls[0]["vad_filter"] is False and eng2.stats["vad_off"]


def test_vad_result_reused_for_the_same_audio(monkeypatch):
    sr = audioproc.SR
    a = np.random.default_rng(4).standard_normal(70 * sr).astype(np.float32) * 0.01
    vad = audioproc._silero()
    calls = []
    monkeypatch.setattr(vad, "get_speech_timestamps", lambda x, o, s: calls.append(len(x)) or [])
    audioproc._last["key"] = None
    opts = vad.VadOptions(threshold=0.5)
    audioproc.windowed_speech_timestamps(a, opts)
    n = len(calls)
    audioproc.windowed_speech_timestamps(a, opts)
    assert len(calls) == n                                        # second call: no new detection
    audioproc.windowed_speech_timestamps(a, vad.VadOptions(threshold=0.4))
    assert len(calls) > n                                         # other options: detected again


# ---------------------------------------------------------------- Options: offline warning, AI Summary model list
def _contrast(fg, bg, m=None):
    def lin(c):
        c = np.array(c, float) / 255
        return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    hexc = lambda h: [int(h[i:i + 2], 16) for i in (1, 3, 5)]
    a, b = lin(hexc(fg)), lin(hexc(bg))
    if m is not None:  # colour-vision simulation (Machado et al. 2009, severity 1.0) in linear RGB
        a, b = np.clip(m @ a, 0, 1), np.clip(m @ b, 0, 1)
    la, lb = (float(x @ [0.2126, 0.7152, 0.0722]) for x in (a, b))
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_warning_colours_readable_in_both_themes_and_with_colour_weakness():
    """Red warnings (2.3.1) and amber cautions. Regression (2.3.1): 'Update available' and the hotword counters used
    #c07000, 3.3:1 on the light window and 3.7:1 on the dark table; it was chosen before contrast was tested, and no
    test looked at colours outside the new warning."""
    from sot.ui.options import CAUTION_DARK, CAUTION_LIGHT, WARN_DARK, WARN_LIGHT
    protan = np.array([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]])
    deutan = np.array([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940, 0.968881]])
    ui = os.path.join(ROOT, "app", "sot", "ui")
    for fn in os.listdir(ui):
        if fn.endswith(".py"):
            assert "#c07000" not in open(os.path.join(ui, fn), encoding="utf-8").read(), fn
    for fg, bgs in ((WARN_LIGHT, ("#ffffff", "#f0f0f0")), (WARN_DARK, ("#1e1e1e", "#2d2d2d", "#353535")),
                    (CAUTION_LIGHT, ("#ffffff", "#f0f0f0")), (CAUTION_DARK, ("#1e1e1e", "#2d2d2d", "#353535"))):
        for bg in bgs:
            for m in (None, protan, deutan):
                assert _contrast(fg, bg, m) >= 4.5, (fg, bg)


def _options(tmp_path, monkeypatch, offline):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox
    from sot import modellist
    from sot.modelmgr import ModelManager
    from sot.ui import options as O
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(modellist, "override_path", lambda: str(tmp_path / "models.json"))
    msgs = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: msgs.append(a[2]) or QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: msgs.append("WARN " + a[2]) or QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    s = Settings(path=str(tmp_path / "settings.json"))
    s.set("transcription.download_folder", str(tmp_path / "dl"))
    s.set("advanced.offline", offline)
    s.save()
    mgr = ModelManager(s)
    mgr.scan()
    dlg = O.OptionsDialog(None, s, mgr, 1)
    return app, dlg, s, msgs, QFileDialog


def _dl_buttons(tab, col):
    out = []
    for r in range(tab.table.rowCount()):
        w = tab.table.cellWidget(r, col)
        out += [b for b in w.findChildren(type(tab.rec_btn)) if b.text() in ("Download", "Resume", "Update")] if w else []
    return out


def test_offline_warning_on_both_model_tabs_and_turn_off(tmp_path, monkeypatch):
    from sot.ui.options import WARN_DARK, WARN_LIGHT
    app, dlg, s, msgs, _ = _options(tmp_path, monkeypatch, offline=True)
    for tab, col in ((dlg.trans, 6), (dlg.summary, 4)):
        note = tab.offline_note
        assert not note.isHidden()
        html = note.text.text()
        assert "Download is disabled because Offline Mode is on" in html and "font-weight:bold" in html
        assert WARN_LIGHT in html or WARN_DARK in html
        assert "Turn Offline Mode off" in html and note.button.text() == "Turn Offline Mode off"
        assert not tab.rec_btn.isEnabled() and not any(b.isEnabled() for b in _dl_buttons(tab, col))
    dlg.summary.offline_note.button.click()  # acts at once, like the main window's Options > Offline Mode tick
    assert s.get("advanced.offline") is False and Settings(path=s.path).get("advanced.offline") is False
    assert dlg.work.get("advanced.offline") is False and dlg.advanced.value("advanced.offline") is False
    for tab, col in ((dlg.trans, 6), (dlg.summary, 4)):
        assert tab.offline_note.isHidden() and tab.rec_btn.isEnabled()
        assert _dl_buttons(tab, col) and all(b.isEnabled() for b in _dl_buttons(tab, col))
    dlg.advanced.put("advanced.offline", True)  # the Advanced tab tick takes effect on Apply
    assert dlg.apply()
    assert not dlg.trans.offline_note.isHidden() and not dlg.summary.offline_note.isHidden()
    assert app is not None


def test_ai_summary_tab_imports_model_list(tmp_path, monkeypatch):
    from sot import modellist
    app, dlg, s, msgs, QFileDialog = _options(tmp_path, monkeypatch, offline=True)
    assert "summary models" in dlg.summary.list_label.text() and not dlg.summary.b_builtin.isEnabled()
    data = modellist.read(modellist.BUILTIN)
    extra = json.loads(json.dumps(data["summary_models"][0]))
    extra["id"], extra["name"] = "qwen3-1.7b-test", "Qwen3-1.7B test copy"
    data["summary_models"].append(extra)
    data["list_version"] = "2099.1.1"
    f = tmp_path / "new list.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (str(f), "")))
    btn = [b for b in dlg.summary.findChildren(type(dlg.summary.rec_btn)) if b.text().startswith("Import model list")]
    assert len(btn) == 1
    btn[0].click()  # works while offline: no network is needed
    assert "Qwen3-1.7B test copy" in msgs[-1] and "AI summary models" in msgs[-1]
    names = [dlg.summary.table.item(r, 0).text() for r in range(dlg.summary.table.rowCount())]
    assert "Qwen3-1.7B test copy" in names
    assert "2099.1.1" in dlg.summary.list_label.text() and "2099.1.1" in dlg.trans.list_label.text()
    assert dlg.summary.b_builtin.isEnabled() and dlg.trans.b_builtin.isEnabled()
    # a summary model of an unknown chat format is refused and the list in use stays
    data["summary_models"][-1]["family"] = "newformat"
    data["list_version"] = "2099.1.2"
    f.write_text(json.dumps(data), encoding="utf-8")
    btn[0].click()
    assert msgs[-1].startswith("WARN ") and "chat format" in msgs[-1] and "2099.1.1" in dlg.summary.list_label.text()
    dlg.summary.b_builtin.click()
    assert "built into the program" in dlg.summary.list_label.text() and not dlg.summary.b_builtin.isEnabled()
    assert "Qwen3-1.7B test copy" not in [dlg.summary.table.item(r, 0).text() for r in range(dlg.summary.table.rowCount())]
    assert app is not None
