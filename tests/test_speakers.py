"""Speaker identification (version 2.4.0): rules, voice library, model list, exports, summary, the transcript editor
and the window. Real-model checks run when SOT_TEST_SPK names a folder with model.int8.onnx (pyannote
segmentation 3.0), the CAM++ voice model and the published sherpa-onnx samples 1-two-speakers-en.wav and
2-two-speakers-en.wav (https://huggingface.co/csukuangfj/speaker-embedding-models)."""
import json
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sot import exporters, modellist, postproc, speakers as spk, summary, voicelib  # noqa: E402
from sot.settings import Settings  # noqa: E402

SPK_DIR = os.environ.get("SOT_TEST_SPK", "")
SEG = os.path.join(SPK_DIR, "model.int8.onnx")
EMB = os.path.join(SPK_DIR, "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx")
REAL = SPK_DIR and os.path.isfile(SEG) and os.path.isfile(EMB)


def unit(*v):
    return spk.norm(list(v))


# ---------------------------------------------------------------- who said each segment
def test_assign_by_overlap_marks_unsure_and_gaps():
    turns = [{"start": 0, "end": 5, "speaker": "A"}, {"start": 5, "end": 10, "speaker": "B"},
             {"start": 12, "end": 15, "speaker": "A"}]
    items = [{"start": 0.5, "end": 4.5}, {"start": 4, "end": 9}, {"start": 3, "end": 7}, {"start": 10.5, "end": 11.2},
             {"start": 30, "end": 31}]
    out = spk.assign(items, turns)
    assert out[0] == ("A", False)
    assert out[1] == ("B", False)       # 80% B, 20% A: sure
    assert out[2] == ("A", True) or out[2] == ("B", True)  # 50/50: two people, unsure
    assert out[3] == ("B", True)        # between turns, 0.5 s from B: nearest, unsure
    assert out[4] == (None, False)      # nobody near


def test_two_channels_louder_side_decides():
    assert spk.channel_side(20.0) == ("me", False)
    assert spk.channel_side(-20.0) == ("others", False)
    assert spk.channel_side(1.0) == ("me", True)
    assert spk.channel_side(-1.0) == ("others", True)
    assert spk.channel_side(None) == ("others", True)
    turns = [{"start": 10, "end": 20, "speaker": "S1"}]
    items = [{"start": 1, "end": 3}, {"start": 11, "end": 13}, {"start": 14, "end": 15}]
    out = spk.assign_split(items, turns, [40.0, -40.0, 2.0])
    assert out == [(spk.ME, False), ("S1", False), (spk.ME, True)]


def test_link_blocks_never_joins_speakers_of_one_block():
    a, b, c = unit(1, 0, 0), unit(0, 1, 0), unit(0, 0, 1)
    near_a = unit(0.95, 0.05, 0)
    cl = [{"id": "b0:0", "block": 0, "centroid": a, "speech_s": 10}, {"id": "b0:1", "block": 0, "centroid": near_a,
                                                                     "speech_s": 10},
          {"id": "b1:0", "block": 1, "centroid": a, "speech_s": 10}, {"id": "b1:1", "block": 1, "centroid": c,
                                                                     "speech_s": 10},
          {"id": "b2:0", "block": 2, "centroid": b, "speech_s": 0}]
    g = spk.link_blocks(cl)
    assert g["b0:0"] != g["b0:1"]          # the diarizer told them apart in block 0: kept apart
    assert g["b1:0"] in (g["b0:0"], g["b0:1"])
    assert g["b1:1"] != g["b1:0"]
    assert len(set(g.values())) == 4
    forced = spk.link_blocks(cl, num_speakers=2)
    assert len(set(forced.values())) <= 3   # joins go on until 2, as far as the same-block rule allows


def test_library_match_is_one_to_one_and_needs_a_clear_lead():
    people = [{"id": "p1", "name": "Alice", "centroid": unit(1, 0, 0)},
              {"id": "p2", "name": "Bob", "centroid": unit(0, 1, 0)}]
    m = spk.match({"S1": unit(1, 0.1, 0), "S2": unit(0.98, 0.15, 0), "S3": unit(0, 1, 0.05)}, people)
    assert m["S1"]["name"] == "Alice" and m["S1"]["level"] == "high"
    assert "S2" not in m                    # Alice is taken by the closer speaker
    assert m["S3"]["name"] == "Bob"
    twins = [{"id": "p1", "name": "Alice", "centroid": unit(1, 0.02, 0)},
             {"id": "p2", "name": "Ann", "centroid": unit(1, 0, 0.02)}]
    assert spk.match({"S1": unit(1, 0.01, 0.01)}, twins) == {}   # two sound alike: no name rather than a wrong one
    assert spk.match({"S1": unit(1, 0, 1.2)}, people, "strict") == {}   # 0.64 < strict 0.65
    assert spk.match({"S1": unit(1, 0, 1.2)}, people, "normal")["S1"]["level"] == "medium"


def test_names_stats_samples_and_majority():
    items = [{"start": 0, "end": 4, "spk": "S2"}, {"start": 4, "end": 6, "spk": "S2"}, {"start": 6, "end": 9,
                                                                                         "spk": spk.ME},
             {"start": 9, "end": 10, "spk": "S1", "spk_unsure": True}, {"start": 10, "end": 12, "spk": "S1"}]
    assert spk.order_of_appearance(items) == ["S2", spk.ME, "S1"]
    assert spk.default_names(["S2", spk.ME, "S1"], "Roberta") == {"S2": "Speaker 1", spk.ME: "Roberta",
                                                                  "S1": "Speaker 2"}
    st = spk.talk_stats(items)
    assert st["S2"]["seconds"] == 6 and st["S2"]["turns"] == 1 and math.isclose(st["S2"]["share"], 0.5)
    assert spk.sample_span(items, "S1") == (10.0, 12.0)    # the sure segment, not the longer unsure one
    assert spk.majority({"start": 0, "end": 7}, items) == ("S2", False)
    assert spk.merge(items, "S2", "S1")[3]["spk"] == "S2"


def test_marker_colours_readable_in_both_themes_and_with_colour_weakness():
    from test_core import _contrast
    protan = np.array([[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116,
                                                                                          1.051998]])
    deutan = np.array([[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.011820, 0.042940,
                                                                                         0.968881]])
    assert len(set(spk.SHAPES)) == len(spk.SHAPES) == len(spk.COLOURS_LIGHT) == len(spk.COLOURS_DARK)
    for fg in spk.COLOURS_LIGHT:
        for bg in ("#ffffff", "#f0f0f0"):
            for m in (None, protan, deutan):
                assert _contrast(fg, bg, m) >= 4.5, (fg, bg)
    for fg in spk.COLOURS_DARK:
        for bg in ("#1e1e1e", "#2d2d2d", "#353535"):
            for m in (None, protan, deutan):
                assert _contrast(fg, bg, m) >= 4.5, (fg, bg)


# ---------------------------------------------------------------- voice library
def test_voice_library_roundtrip_consent_and_limits(tmp_path):
    p = str(tmp_path / "voices.json")
    lib = voicelib.empty()
    with pytest.raises(ValueError):
        voicelib.add_voice(lib, "Alice Chan", [1.0] * 192, 40, "m1")          # a new person needs consent
    a = voicelib.add_voice(lib, "Alice  Chan ", [1.0] * 192, 40, "m1", consent_date="2026-11-16", note="Northwind")
    assert a["name"] == "Alice Chan" and a["consent"] == "2026-11-16"
    for i in range(25):
        voicelib.add_voice(lib, "alice chan", [1.0 + i] + [1.0] * 191, 31, "m1")  # same person (name, model)
    assert len(lib["people"]) == 1 and len(a["samples"]) == voicelib.MAX_SAMPLES
    voicelib.add_voice(lib, "Alice Chan", [1.0] * 192, 40, "m2", consent_date="2026-11-16")  # other model: own entry
    voicelib.save(lib, p)
    assert not [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    back, problems = voicelib.load(p)
    assert not problems and len(back["people"]) == 2
    assert [x["name"] for x in voicelib.for_model(back, "m1")] == ["Alice Chan"]
    c = voicelib.for_model(back, "m1")[0]["centroid"]
    assert math.isclose(sum(x * x for x in c), 1.0, rel_tol=1e-6)


def test_voice_library_checks_every_field(tmp_path):
    good = {"id": "p-0123456789abcdef", "name": "Bob", "model": "m1", "dim": 3, "consent": "2026-11-16",
            "samples": [{"v": [0.1, 0.2, 0.3], "s": 30, "date": "2026-11-16"}]}
    data = {"app": "SecureOfflineTranscriber", "kind": "voice-library", "format": 1, "people": [
        good,
        dict(good, id="p-0123456789abcdef"),                              # repeated id
        dict(good, id="p-1111111111111111", name=""),                     # no name
        dict(good, id="p-2222222222222222", model="../x"),                # bad model name
        dict(good, id="p-3333333333333333", samples=[{"v": [float("nan"), 0, 0], "s": 3}]),  # damaged numbers
        dict(good, id="p-4444444444444444", samples=[{"v": [0.1, 0.2], "s": 3}]),  # wrong size
        dict(good, id="p-5555555555555555", samples=[{"v": "__import__('os')", "s": 3}]),  # text, never run
        dict(good, id="p-6666666666666666", name="Carol", consent=""),    # kept, but flagged
    ]}
    lib, problems = voicelib.validate(data)
    assert [p["name"] for p in lib["people"]] == ["Bob", "Carol"]
    assert any("consent" in x for x in problems) and len(problems) >= 6
    assert voicelib.validate({"app": "x"})[0]["people"] == []
    (tmp_path / "v.json").write_text("{not json", encoding="utf-8")
    lib, problems = voicelib.load(str(tmp_path / "v.json"))
    assert lib["people"] == [] and problems


def test_voice_library_merge_export_import(tmp_path):
    lib = voicelib.empty()
    a = voicelib.add_voice(lib, "Dana", [1, 0, 0], 30, "m1", consent_date="2026-11-01")
    b = voicelib.add_voice(lib, "Dana W", [0.9, 0.1, 0], 30, "m1", consent_date="2026-11-10")
    voicelib.merge(lib, a["id"], b["id"])
    assert len(lib["people"]) == 1 and len(a["samples"]) == 2 and a["consent"] == "2026-11-10"
    n = voicelib.export_people(lib, {a["id"]}, str(tmp_path / "e.json"))
    other = voicelib.empty()
    added, updated, problems = voicelib.import_people(other, str(tmp_path / "e.json"))
    assert (n, added, updated, problems) == (1, 1, 0, [])
    added, updated, _ = voicelib.import_people(other, str(tmp_path / "e.json"))
    assert (added, updated) == (0, 1)


# ---------------------------------------------------------------- model list
def test_builtin_list_has_speaker_models():
    data, _ = modellist.validate(modellist.read(modellist.BUILTIN))
    kinds = {m["id"]: m["kind"] for m in data["speaker_models"]}
    assert kinds == {"pyannote-segmentation-3.0-int8": "segmentation", "3dspeaker-campplus-zh-en": "speaker-embedding",
                     "3dspeaker-campplus-en": "speaker-embedding"}
    assert data["speaker_recommended"] == ["pyannote-segmentation-3.0-int8", "3dspeaker-campplus-zh-en"]
    for m in data["speaker_models"]:
        assert m["repo"].startswith("csukuangfj/") and len(m["revision"]) == 40 and m["license"]


@pytest.mark.parametrize("change,words", [
    (lambda m: m["files"].append(dict(m["files"][0], name="b.onnx")), "exactly one .onnx"),
    (lambda m: m.update(kind="vad"), "kind must be"),
    (lambda m: m.update(revision="main"), "40-character"),
])
def test_bad_speaker_models_are_refused(change, words):
    data = modellist.read(modellist.BUILTIN)
    change(data["speaker_models"][0])
    with pytest.raises(modellist.ListError) as ex:
        modellist.validate(data)
    assert words in str(ex.value)
    data = modellist.read(modellist.BUILTIN)
    data["speaker_recommended"] = ["3dspeaker-campplus-zh-en"]
    with pytest.raises(modellist.ListError):
        modellist.validate(data)


def test_imported_list_without_speaker_models_keeps_the_builtin_ones(tmp_path, monkeypatch):
    data = modellist.read(modellist.BUILTIN)
    data.pop("speaker_models")
    data.pop("speaker_recommended")
    data["list_version"] = "2099.01.01"
    p = tmp_path / "new.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(modellist, "override_path", lambda: str(tmp_path / "models.json"))
    cur = modellist.load()["data"]
    got, added, removed, changed, _ = modellist.import_file(str(p), cur)
    assert len(got["speaker_models"]) == 3 and not removed
    assert modellist.load()["source"] == "imported" and len(modellist.load()["data"]["speaker_models"]) == 3


def test_speaker_models_scan_and_choice(tmp_path):
    from sot.modelmgr import ModelManager
    s = Settings(path=str(tmp_path / "s.json"))
    s.set("transcription.download_folder", str(tmp_path / "dl"))
    mgr = ModelManager(s)
    assert mgr.spk_choice("segmentation") is None
    e = mgr.by_id["pyannote-segmentation-3.0-int8"]
    os.makedirs(mgr.folder_for(e))
    for f in e["files"]:  # files of the right size count as installed (sizes are what the scan checks)
        with open(os.path.join(mgr.folder_for(e), f["name"]), "wb") as fh:
            fh.truncate(f["size"])
    mgr.scan_spk()
    m = mgr.spk_choice("segmentation")
    assert m and m.status == "Installed" and mgr.spk_file(m).endswith("model.int8.onnx")
    assert mgr.spk_choice("speaker-embedding") is None


# ---------------------------------------------------------------- paragraphs, exports, summary
def test_paragraphs_break_where_the_speaker_changes():
    segs = [{"start": 0, "end": 1, "text": "Hi.", "spk": "S1"}, {"start": 1.1, "end": 2, "text": "Hello.", "spk": "S2"},
            {"start": 2.1, "end": 3, "text": "Agenda?", "spk": "S2", "spk_unsure": True}]
    p = postproc.paragraphs(segs)
    assert [(x["spk"], x["text"], x["spk_unsure"]) for x in p] == [("S1", "Hi.", False), ("S2", "Hello. Agenda?", True)]


BLOCKS = [{"start": 0.0, "end": 3.0, "text": "We start with the budget.", "spk": "S1"},
          {"start": 3.5, "end": 6.0, "text": "It is under plan.", "spk": "S2"},
          {"start": 7.0, "end": 8.0, "text": "Noted."}]
META = {"file": "m.mp3", "speakers": {"S1": "Alice Chan", "S2": "Speaker 2"}, "version": "2.4.0"}


@pytest.mark.parametrize("fmt", ["txt_times", "txt_plain", "srt", "vtt", "md", "json", "csv", "docx"])
def test_every_export_carries_the_names(tmp_path, fmt):
    s = Settings(values={})
    p = str(tmp_path / f"t.{fmt}")
    exporters.write(fmt, p, BLOCKS, META, s)
    if fmt == "docx":
        from docx import Document
        text = "\n".join(par.text for par in Document(p).paragraphs)
    else:
        text = open(p, encoding="utf-8-sig").read()
    if fmt == "vtt":
        assert "<v Alice Chan>We start" in text
    elif fmt == "json":
        d = json.loads(text)
        assert d["segments"][0]["speaker"] == "Alice Chan" and "speaker" not in d["segments"][2]
        assert d["speakers"] == [{"id": "S1", "name": "Alice Chan"}, {"id": "S2", "name": "Speaker 2"}]
        assert "centroid" not in text
    elif fmt == "csv":
        assert text.splitlines()[0].endswith(",speaker") and text.splitlines()[1].endswith("Alice Chan")
    elif fmt == "md":
        assert "**Alice Chan:** We start" in text
    else:
        assert "Alice Chan: We start" in text and "Speaker 2: It is" in text
    s.set("speakers.in_exports", False)
    exporters.write(fmt, p, BLOCKS, META, s)
    if fmt != "docx":
        assert "Alice" not in open(p, encoding="utf-8-sig").read()
    s.set("speakers.in_exports", True)
    s.set("speakers.label_style", "brackets")
    if fmt == "txt_times":
        exporters.write(fmt, p, BLOCKS, META, s)
        assert "[Alice Chan] We start" in open(p, encoding="utf-8-sig").read()


def test_summary_gets_names_and_the_rule_for_unknown_speakers():
    lines = summary.transcript_lines([{"start": 61, "text": "I will send it.", "speaker": "Alice Chan"},
                                      {"start": 70, "text": "Fine."}])
    assert lines == ["[00:01:01] Alice Chan: I will send it.", "[00:01:10] Fine."]
    opts = {"lang": "en", "length": "standard", "sections": list(summary.SECTIONS), "times": True, "speakers": True}
    assert "Speaker 2" in summary.section_instructions(opts) and "do not guess" in summary.section_instructions(opts)
    assert "Speaker 2" not in summary.section_instructions(dict(opts, speakers=False))


# ---------------------------------------------------------------- channel tag of Voice Recorder Pro files
def test_probe_reads_tags_and_finds_the_channel_note(monkeypatch):
    from sot import media
    from sot.ui import speakerui
    err = ("Input #0, wav, from 'x.wav':\n  Metadata:\n    title           : Board\n    ICHN            : "
           "L=microphone; R=computer audio\n    encoder         : Voice Recorder Pro 1.0.3\n  Duration: 00:01:00.00, "
           "bitrate: 512 kb/s\n  Stream #0:0: Audio: pcm_s16le ([1][0][0][0] / 0x0001), 16000 Hz, 2 channels, s16, "
           "512 kb/s\n")

    class R:
        returncode, stderr = 1, err
    monkeypatch.setattr(media, "_ffmpeg_or_raise", lambda: "ffmpeg")
    monkeypatch.setattr(media.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(media.os.path, "getsize", lambda p: 1)
    monkeypatch.setattr(media.os.path, "exists", lambda p: True)
    info = media.probe("x.wav")
    assert info["tags"]["ICHN"] == "L=microphone; R=computer audio" and info["tags"]["title"] == "Board"
    assert speakerui.channel_tag(info) and speakerui.is_stereo(info)
    s = Settings(values={})
    assert speakerui.split_default(s, info) is True
    s.set("speakers.channels", "mixed")
    assert speakerui.split_default(s, info) is False
    info2 = dict(info, tags={"TXXX:VRP_CHANNELS": "x"}, audio=[dict(info["audio"][0], channels="mono")])
    s.set("speakers.channels", "split")
    assert speakerui.split_default(s, info2) is False    # mono files are never split


# ---------------------------------------------------------------- the transcript editor
def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_editor_draws_labels_and_reads_text_back_without_them():
    _app()
    from sot.ui.transcript import TranscriptEdit
    ed = TranscriptEdit()
    ed.speaker_names = {"S1": "Alice Chan", "S2": "Speaker 2"}
    ed.set_blocks([dict(b, subs=[{"start": b["start"], "end": b["end"], "c0": 0, "c1": len(b["text"])}])
                   for b in BLOCKS[:2]] + [BLOCKS[2]])
    lines = ed.toPlainText().splitlines()
    assert lines[0] == "[00:00:00] ● Alice Chan: We start with the budget."
    assert lines[2] == "[00:00:07] Noted."
    assert [(b.get("spk"), b["text"]) for b in ed.blocks()] == [("S1", "We start with the budget."),
                                                                 ("S2", "It is under plan."), (None, "Noted.")]
    s0 = ed.sentences()[0]
    assert ed.document().begin().text()[s0["c0"]:s0["c1"]] == "We start with the budget."


def test_rename_and_change_keep_one_space_after_the_label():
    """Regression: after a rename the line read 'Speaker 2:  What' (two spaces). Root cause: the plain space after a
    label merges with the paragraph's text into one fragment, so the label's range was found by fragments and missed
    it. Missed by the first tests because they only read blocks() (which strips spaces), not what the editor draws."""
    _app()
    from sot.ui.transcript import TranscriptEdit
    ed = TranscriptEdit()
    ed.speaker_names = {"S1": "Speaker 1", "S2": "Speaker 2"}
    ed.set_blocks([BLOCKS[0], BLOCKS[1], BLOCKS[2]])
    ed.speaker_names = {"S1": "Alice Chan", "S2": "Bob"}
    ed.refresh_labels()
    ed.refresh_labels()
    third = ed.document().begin().next().next()
    ed.set_block_speaker(third, "S1", True)
    ed.set_block_speaker(ed.document().begin(), "S2")
    lines = ed.toPlainText().splitlines()
    assert lines == ["[00:00:00] ■ Bob: We start with the budget.", "[00:00:03] ■ Bob: It is under plan.",
                     "[00:00:07] ● Alice Chan (?): Noted."]
    assert [b.get("spk") for b in ed.blocks()] == ["S2", "S2", "S1"]
    assert [b["text"] for b in ed.blocks()] == [b["text"] for b in BLOCKS]
    ed.set_block_speaker(third, None)
    assert ed.toPlainText().splitlines()[2] == "[00:00:07] Noted."


# ---------------------------------------------------------------- the window
def _window(tmp_path, monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox
    from sot import worker as W
    from sot.modelmgr import ModelManager
    from sot.ui.mainwindow import MainWindow
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))
    s = Settings(path=str(tmp_path / "settings.json"))
    s.set("transcription.download_folder", str(tmp_path / "dl"))
    s.set("general.data_folder", str(tmp_path / "data"))
    s.set("output.autosave", "off")
    os.makedirs(tmp_path / "data", exist_ok=True)
    mgr = ModelManager(s)
    win = MainWindow(s, mgr, W.Worker(s.data_folder()), "")
    win.info = {"path": str(tmp_path / "m.wav"), "duration": 20.0, "audio": [{"channels": "mono"}], "tags": {}}
    win.segments = [{"start": 0, "end": 3, "text": "Hello all.", "lang": "en"},
                    {"start": 3.2, "end": 6, "text": "Hi, thanks.", "lang": "en"},
                    {"start": 8.5, "end": 11, "text": "Budget first.", "lang": "en"}]
    win.show_blocks()
    win.editor.document().setModified(False)
    return win, s


class _M:
    def __init__(self, key, name):
        self.key, self.name = key, name


def test_window_applies_a_result_names_from_the_library_and_keeps_edits(tmp_path, monkeypatch):
    win, s = _window(tmp_path, monkeypatch)
    lib = voicelib.empty()
    voicelib.add_voice(lib, "Alice Chan", unit(1, 0, 0), 40, "vm", consent_date="2026-11-16")
    voicelib.save(lib, voicelib.path_for(s.data_folder()))
    # the user corrected a word before identifying the speakers: the edit must survive
    cur = win.editor.textCursor()
    cur.movePosition(cur.MoveOperation.End)
    cur.insertText(" Then sales.")
    res = {"split": False, "turns": [{"start": 0, "end": 3.1, "speaker": "S9"}, {"start": 3.1, "end": 6, "speaker": "S4"},
                                     {"start": 8.5, "end": 11, "speaker": "S9"}],
           "speakers": {"S9": {"centroid": unit(1, 0.05, 0), "speech_s": 3}, "S4": {"centroid": unit(0, 1, 0),
                                                                                    "speech_s": 6}},
           "stats": {"speaker_s": 1.0, "blocks": 1}}
    j = {"opts": {"count": 0, "split": False, "left_name": "Me", "use_library": True}, "seg": _M("seg", "seg"),
         "emb": _M("vm", "Voice"), "problems": []}
    win._apply_speakers(res, j)
    assert {k: v["name"] for k, v in win.speakers.items()} == {"S1": "Alice Chan", "S2": "Speaker 2"}
    blocks = win.editor.blocks()
    assert blocks[-1]["text"].endswith("Then sales.")
    assert blocks[0]["spk"] == "S1"
    assert win.speakers["S1"]["level"] and "Speakers: 2 found" in "\n".join(win.cleanup_log)
    assert voicelib.load(voicelib.path_for(s.data_folder()))[0]["people"][0]["last_matched"]
    # the session keeps names, never voiceprints
    win._write_session(str(tmp_path / "t.sot.json"))
    d = json.load(open(tmp_path / "t.sot.json", encoding="utf-8"))
    assert d["speakers"]["S1"]["name"] == "Alice Chan" and "centroid" not in json.dumps(d)
    assert d["blocks"][0]["spk"] == "S1"
    # rename everywhere, Alt+n, merge, clear
    # the first paragraph was edited, so it stays whole and goes to who said most of it, marked unsure
    assert blocks[0]["spk_unsure"] and len(blocks) == 2
    from PySide6.QtGui import QTextCursor
    win.editor.setTextCursor(QTextCursor(win.editor.document().begin()))
    win.set_speaker_by_number(2)        # Alt+2
    assert win.editor.blocks()[0]["spk"] == "S2" and not win.editor.blocks()[0]["spk_unsure"]
    win.rename_speaker("S2", "Bob")
    assert win.editor.toPlainText().splitlines()[0].startswith("[00:00:00] \u25a0 Bob: Hello all.")
    win._merge("S1", "S2")
    assert list(win.speakers) == ["S1"] and "Bob" not in win.editor.toPlainText()
    win.speakers["S1"]["centroid"] = None
    win.refresh_speaker_panel()
    assert not win.spk_panel.isHidden() and not win.spk_panel.remember["S1"].isEnabled()
    win.clear_speaker_labels()
    assert not win.speakers and "Alice" not in win.editor.toPlainText()


def test_window_two_channel_result_and_summary_input(tmp_path, monkeypatch):
    win, s = _window(tmp_path, monkeypatch)
    res = {"split": True, "turns": [{"start": 3, "end": 11, "speaker": "S1"}], "diffs": [30.0, -30.0, -30.0],
           "speakers": {"S1": {"centroid": unit(0, 1, 0), "speech_s": 6}, spk.ME: {"centroid": unit(1, 0, 0),
                                                                                   "speech_s": 3}},
           "stats": {"speaker_s": 1.0, "blocks": 1}}
    j = {"opts": {"count": 2, "split": True, "left_name": "Roberta", "use_library": False}, "seg": _M("seg", "seg"),
         "emb": _M("vm", "Voice"), "problems": []}
    win._apply_speakers(res, j)
    assert win.speakers[spk.ME]["name"] == "Roberta" and win.speakers["S1"]["name"] == "Speaker 1"
    lines = win._summary_input()
    assert lines[0]["speaker"] == "Roberta" and lines[1]["speaker"] == "Speaker 1"
    assert win._summary_options(type("M", (), {"entry": {"family": "qwen3"}})())["speakers"] is True


def test_identify_dialog_consent_dialog_and_library_dialog(tmp_path, monkeypatch):
    _app()
    from PySide6.QtWidgets import QMessageBox
    from sot.ui import speakerui
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.Yes))
    s = Settings(values={})
    info = {"path": "x.mp3", "duration": 60, "audio": [{"channels": "stereo"}],
            "tags": {"VRP_CHANNELS": "L=microphone; R=computer audio"}}
    d = speakerui.IdentifyDialog(None, s, info, 0, [{"id": "p"}], "seg", "voice")
    v = d.values()
    assert v["split"] and v["use_library"] and v["count"] == 0 and v["left_name"] == "Me"
    c = speakerui.ConsentDialog(None, "Alice Chan")
    assert not c.b_save.isEnabled()
    c.agree.setChecked(True)
    assert c.b_save.isEnabled()
    p = str(tmp_path / "voices.json")
    lib = voicelib.empty()
    voicelib.add_voice(lib, "Alice Chan", [1, 0, 0], 30, "vm", consent_date="2026-11-16")
    voicelib.save(lib, p)
    dlg = speakerui.VoiceLibraryDialog(None, p, {"vm": "Voice"})
    assert dlg.table.rowCount() == 1 and dlg.table.item(0, 4).text() == "2026-11-16"
    dlg.table.selectRow(0)
    dlg.delete()
    assert voicelib.load(p)[0]["people"] == []


def test_options_speakers_tab_round_trip(tmp_path, monkeypatch):
    from test_core import _options
    app, dlg, s, msgs, _ = _options(tmp_path, monkeypatch, offline=True)
    t = dlg.speakers
    assert t.table.rowCount() == 3 and "Not installed" in t.table.item(0, 4).text()
    t.auto.setChecked(True)
    t.count.setCurrentIndex(t.count.findData(4))
    t.channels.setCurrentIndex(t.channels.findData("split"))
    t.left_name.setText("  Roberta  ")
    t.style_.setCurrentIndex(t.style_.findData("brackets"))
    assert dlg.apply()
    assert (s.get("speakers.auto"), s.get("speakers.count"), s.get("speakers.channels"), s.get("speakers.left_name"),
            s.get("speakers.label_style")) == (True, 4, "split", "Roberta", "brackets")
    assert not t.rec_btn.isEnabled()   # Offline Mode: no downloads


# ---------------------------------------------------------------- real models (published samples)
@pytest.mark.skipif(not REAL, reason="set SOT_TEST_SPK to a folder with the speaker models and samples")
def test_real_models_find_two_speakers_in_the_published_samples():
    from sot import diarize

    class C:
        def is_set(self):
            return False
    for wav in ("1-two-speakers-en.wav", "2-two-speakers-en.wav"):
        path = os.path.join(SPK_DIR, wav)
        import soundfile as sf
        dur = sf.info(path).duration
        job = {"path": path, "track": 0, "duration": dur, "seg_model": SEG, "emb_model": EMB, "threads": 2,
               "num_speakers": 0, "threshold": 0.5, "split": False, "items": []}
        r = diarize.run(job, lambda *a: None, C())
        big = [k for k, v in r["speakers"].items() if v["speech_s"] >= 2]
        assert len(big) == 2, (wav, r["speakers"])
        assert all(len(r["speakers"][k]["centroid"]) == 192 for k in big)
        a, b = (r["speakers"][k]["centroid"] for k in big)
        assert spk.cosine(a, b) < spk.STRICTNESS["normal"]   # the two voices would not be given the same name


@pytest.mark.skipif(not REAL, reason="set SOT_TEST_SPK to a folder with the speaker models and samples")
@pytest.mark.parametrize("one_pass", [3600.0, 20.0])
def test_real_models_two_channels_and_long_recording_blocks(tmp_path, monkeypatch, one_pass):
    """A two-channel file made from the published samples: left = speaker A of sample 1 ("me"), right = speaker B of
    sample 1 and the two speakers of sample 2. With one_pass = 20 s the 50 s file is processed in three 17 s blocks,
    as a recording over an hour would be in 30-minute blocks, and the blocks' speakers are linked by voice."""
    import soundfile as sf
    from sot import diarize
    monkeypatch.setattr(diarize, "ONE_PASS_S", one_pass)
    monkeypatch.setattr(diarize, "BLOCK_S", 17.0)
    a, _ = sf.read(os.path.join(SPK_DIR, "1-two-speakers-en.wav"), dtype="float32")
    b, _ = sf.read(os.path.join(SPK_DIR, "2-two-speakers-en.wav"), dtype="float32")
    r = 16000
    left = np.zeros(len(a) + len(b), np.float32)
    right = np.zeros_like(left)
    left[:8 * r] = a[:8 * r]
    right[9 * r:len(a)] = a[9 * r:]
    right[len(a):] = b
    path = str(tmp_path / "split.wav")
    sf.write(path, np.stack([left, right], 1), r)

    class C:
        def is_set(self):
            return False
    items = [[1.6, 3.3], [4.4, 6.4], [9.3, 11.5], [12.2, 14.6], [16.9, 18.7], [36, 40]]
    job = {"path": path, "track": 0, "duration": 50.0, "seg_model": SEG, "emb_model": EMB, "threads": 2,
           "num_speakers": 0, "threshold": 0.5, "split": True, "items": items}
    res = diarize.run(job, lambda *x: None, C())
    assert res["stats"]["blocks"] == (1 if one_pass > 50 else 3)
    out = spk.assign_split([{"start": s, "end": e} for s, e in items], res["turns"], res["diffs"])
    who = [x[0] for x in out]
    assert who[:2] == [spk.ME, spk.ME]                   # the left channel is me
    assert who[2] == who[3] and who[2] not in (spk.ME, None)   # speaker B of sample 1, the same in both sentences
    assert who[4] not in (spk.ME, None, who[2])          # sample 2 is other people
    assert len(res["speakers"][spk.ME]["centroid"]) == 192


def test_speakers_panel_leaves_no_old_widgets_behind():
    """Regression: after the panel was filled again (it is refreshed several times while speakers are identified),
    the first row showed the old name box and detail text underneath the new ones. Root cause: replacing a cell
    widget only schedules the old one for deletion, and until the event loop deletes it, it is still drawn; the
    panel now empties the table first, which removes the old widgets at once. Missed because tests checked the
    engine and the editor, not what the panel draws; found by looking at a rendered window."""
    app = _app()
    from PySide6.QtWidgets import QComboBox
    from sot.ui.speakerui import SpeakersPanel
    p = SpeakersPanel()
    row = lambda i, n: {"id": f"S{i}", "name": n, "shape": "●", "light": "#0068a3", "dark": "#21afff",
                        "detail": "Not in the voice library", "seconds": 40.0, "share": 0.5, "turns": 1,
                        "can_save": True, "why_not": ""}
    p.set_rows([row(1, "Speaker 1"), row(2, "Speaker 2")], [], 30, False)
    p.set_rows([row(2, "Speaker 2"), row(1, "Alice Chan")], ["Alice Chan"], 30, False)
    app.processEvents()  # as in the window before the event loop runs again: old widgets must already be gone
    combos = [c for c in p.table.findChildren(QComboBox)]
    assert len(combos) == 2 and sorted(c.currentText() for c in combos) == ["Alice Chan", "Speaker 2"]
    assert p.table.item(0, 2).text() == "50%\n1 turn"


def test_model_paths_outside_the_code_page_use_the_short_form(monkeypatch):
    from sot import diarize
    assert diarize.model_path("C:\\\\models\\\\a.onnx") == "C:\\\\models\\\\a.onnx"
    monkeypatch.setattr(diarize.os, "name", "nt")
    import ctypes

    class K:
        def __init__(self, short):
            self.short = short

        def GetShortPathNameW(self, path, buf, n):
            buf.value = self.short
            return len(self.short)
    monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": K("C:\\\\MODELS~1\\\\a.onnx")})(), raising=False)
    assert diarize.model_path("C:\\\\模型\\\\a.onnx") == "C:\\\\MODELS~1\\\\a.onnx"
    monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": K("")})(), raising=False)
    with pytest.raises(RuntimeError) as ex:
        diarize.model_path("C:\\\\模型\\\\a.onnx")
    assert "English letters only" in str(ex.value)
