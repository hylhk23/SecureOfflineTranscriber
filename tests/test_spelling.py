"""English spelling by region (sot/spelling.py, postproc, Options, Edit > Change English Spelling, AI summary).

Expected spellings come from published style guides; each case names its source:
  CA-TIPS  Language Portal of Canada (Translation Bureau, Government of Canada), Writing Tips Plus,
           "spelling: international variations", https://our-languages.canada.ca/en/writing-tips-plus/spelling-international-variations
  CA-BLOG  Language Portal of Canada blog, "Canadian, British and American: It's all English, but the spelling is
           different", https://our-languages.canada.ca/en/blogue-blog/english-spelling-differences-eng
  AU-SM    Australian Government Style Manual, quick guide "Common misspellings and word confusion" and
           "Spelling", https://www.stylemanual.gov.au/style-manual-resources/quick-guides/quick-guide-common-misspellings-and-word-confusion
  UK-GOV   GOV.UK A to Z style guide, https://guidance.publishing.service.gov.uk/writing-to-gov-uk-standards/style-guides/a-to-z-style-guide
  ESDB     English Speller Database README, "Variant Translation" (program: computer program, programme otherwise)
"""
import json
import os
import sys
from collections import Counter

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))

from sot import postproc, spelling, summary, system  # noqa: E402
from sot.settings import Settings  # noqa: E402

TABLE = os.path.join(ROOT, "app", "data", "spelling.json")
T = spelling.load_tables(TABLE)


def conv(text, v, protect=()):
    return spelling.convert(text, v, T[v], protect)[0]


# ------------------------------------------------------------------ word lists from the Canadian table (CA-TIPS)
BRITISH = ("civilise organise specialise fulfil enrol fulfilment enrolment counselled labelled travelled colour "
           "honour centre fibre theatre defence offence pretence anaemia encyclopaedia diarrhoea").split()
AMERICAN = ("civilize organize specialize fulfill enroll fulfillment enrollment counseled labeled traveled color "
            "honor center fiber theater defense offense pretense anemia encyclopedia diarrhea").split()
# Canadian: -ize; fulfill but enrol; fulfillment but enrolment; travelled; -our; -re; -ce except pretense; e for ae
CANADIAN = ("civilize organize specialize fulfill enrol fulfillment enrolment counselled labelled travelled colour "
            "honour centre fibre theatre defence offence pretense anemia encyclopedia diarrhea").split()


def test_canadian_government_word_lists_british_to_american():
    """CA-TIPS table, British column -> American column."""
    assert [conv(w, "us") for w in BRITISH] == AMERICAN


def test_canadian_government_word_lists_american_to_british():
    """CA-TIPS table, American column -> British column, except encyclopedia: ESDB prefers it in British too, so
    it is left as written (both are accepted)."""
    exp = [w if w != "encyclopaedia" else "encyclopedia" for w in BRITISH]
    assert [conv(w, "gb") for w in AMERICAN] == exp


@pytest.mark.parametrize("source", [BRITISH, AMERICAN])
def test_canadian_government_rules_from_either_side(source):
    """CA-TIPS rules 1 to 6 (including the exceptions enrol, enrolment and pretense)."""
    assert [conv(w, "ca") for w in source] == CANADIAN


def test_canadian_blog_examples():
    """CA-BLOG: labour, honour, humour; centre, fibre, sombre, metre; analyze, paralyze, organize; defence,
    offence; travelled, labelled, marshalled; manoeuvre."""
    src = "labor honor humor center fiber somber analyse paralyse organise defense offense traveled labeled " \
          "marshaled maneuver"
    assert conv(src, "ca") == ("labour honour humour centre fibre sombre analyze paralyze organize defence offence "
                               "travelled labelled marshalled manoeuvre")
    assert conv("The track is 400 meters long.", "ca") == "The track is 400 metres long."


def test_australian_and_hong_kong_follow_british_ise():
    for v in ("au", "hk"):
        assert conv("We organized the colors at the center and analyzed the catalog.", v) == \
            "We organised the colours at the centre and analysed the catalogue."
    assert T["hk"] is T["gb"]


def test_uk_government_examples():
    """UK-GOV: 'organise' not 'organize', 'modelling' not 'modeling'; American proper nouns like 4th Mechanized
    Brigade or Pearl Harbor keep American spelling. UK-GOV also prefers 'grams' to 'grammes', but ESDB marks
    'grammes' as a rare variant everywhere, not a regional spelling, so it is left as written (known limitation)."""
    assert conv("We organize the modeling work in grams.", "gb") == "We organise the modelling work in grams."
    assert conv("It weighs 5 grammes.", "gb") == "It weighs 5 grammes."
    s = "The 4th Mechanized Brigade visited Pearl Harbor to organize a memorial."
    assert conv(s, "gb") == "The 4th Mechanized Brigade visited Pearl Harbor to organise a memorial."


# ------------------------------------------------------------------ spelling by meaning
@pytest.mark.parametrize("v", ["gb", "au", "ca", "hk"])
def test_licence_noun_license_verb(v):
    """AU-SM and CA-BLOG: licence is the noun, license the verb."""
    assert conv("She renewed her driver's license.", v) == "She renewed her driver's licence."
    assert conv("We license the software to schools.", v) == "We license the software to schools."
    assert conv("They want to license it under the license terms.", v) == \
        "They want to license it under the licence terms."


@pytest.mark.parametrize("v", ["gb", "au", "ca", "hk"])
def test_practice_noun_practise_verb(v):
    """AU-SM and CA-BLOG: practice is the noun, practise the verb."""
    assert conv("You should practice every day.", v) == "You should practise every day."
    assert conv("Good practice matters, and in practice it works.", v) == "Good practice matters, and in practice it works."
    assert conv("She practices law in Sydney.", v) == "She practises law in Sydney."
    assert conv("I have to go to practice after work.", v) == "I have to go to practice after work."
    assert conv("Best practices are listed.", v) == "Best practices are listed."


def test_american_uses_one_form():
    s = "Her licence and his practise; write the cheque; change the tyre; 100 metres; a two-storey programme."
    assert conv(s, "us") == "Her license and his practice; write the check; change the tire; 100 meters; " \
                            "a two-story program."


def test_cheque_only_when_it_pays():
    for v in ("gb", "ca", "au", "hk"):
        assert conv("Please write a check for $200.", v) == "Please write a cheque for $200."
        assert conv("He paid by check and cashed two checks.", v) == "He paid by cheque and cashed two cheques."
        assert conv("Let me check the figures. Run a quick check; check-in is at nine.", v) == \
            "Let me check the figures. Run a quick check; check-in is at nine."


def test_tyre_metre_storey_programme():
    assert conv("The car had a flat tire. Long meetings tire me; I never tire of it.", "gb") == \
        "The car had a flat tyre. Long meetings tire me; I never tire of it."
    assert conv("The car had a flat tire.", "ca") == "The car had a flat tire."      # Canadian: tire
    assert conv("Run 100 meters, read the gas meter, the pool is two meters deep.", "gb") == \
        "Run 100 metres, read the gas meter, the pool is two metres deep."
    assert conv("A two-story house next to a building 40 stories high. She told a story.", "au") == \
        "A two-storey house next to a building 40 storeys high. She told a story."
    # ESDB: computer program, programme otherwise (British); Australian and Canadian write program
    assert conv("The TV program was good but the computer program crashed.", "gb") == \
        "The TV programme was good but the computer program crashed."
    assert conv("The TV programme and the training programme.", "au") == "The TV program and the training program."
    assert conv("The TV programme.", "ca") == "The TV program."


def test_judgment_is_kept_for_legal_use():
    """AU-SM: judgement, but judgment for legal material: neither is changed for Australian or British."""
    for v in ("au", "gb"):
        assert conv("The court's judgment and her judgement.", v) == "The court's judgment and her judgement."
    assert conv("Her judgement.", "us") == "Her judgment."


# ------------------------------------------------------------------ what is never changed
def test_names_and_titles_are_left_alone():
    s = "We met at the Lincoln Center, then the World Trade Center. Center Parcs opened. Labor Day is soon."
    assert conv(s, "gb") == s
    assert conv("Color is important. COLOR matters. The color’s hue is color-coded.", "gb") == \
        "Colour is important. COLOUR matters. The colour’s hue is colour-coded."


def test_vocabulary_terms_addresses_and_codes_are_left_alone():
    s = ("Visit https://example.com/color, mail color@example.com, open C:\\color\\a.txt or color_2.txt, "
         "run color2 and ColorPicker; the Color Run is fun; ask the Favorite Foods team.")
    assert conv(s, "gb", protect=["Favorite Foods"]) == s
    assert conv("We use the favorite tool and the organize button.", "gb", protect=["organize button"]) == \
        "We use the favourite tool and the organize button."


def test_different_words_and_both_correct_forms_are_left_alone():
    s = "The aluminum airplane. My mom did math. They learned and learnt; while and whilst; toward the disk."
    for v in ("us", "gb", "ca", "au"):
        assert conv(s, v) == s
    assert conv("Ensure the insurance covers the enquiry and the inquiry.", "us") == \
        "Ensure the insurance covers the enquiry and the inquiry."


def test_informal_spellings_are_not_regional():
    assert conv("Open thru the nite.", "gb") == "Open thru the nite."


def test_changes_are_counted():
    out, ch = spelling.convert("color, colour, Color. Color and center", "gb", T["gb"])
    assert out == "colour, colour, Color. Colour and centre"    # the third looks like a name: capital mid-sentence
    assert ch == Counter({("color", "colour"): 2, ("center", "centre"): 1})
    assert spelling.summary_line(ch) == "color → colour ×2, center → centre"
    many = Counter({(f"w{i}", f"x{i}"): 1 for i in range(15)})
    assert spelling.summary_line(many, limit=3).endswith("and 12 more")
    assert spelling.convert("", "gb", T["gb"]) == ("", Counter())
    assert spelling.convert("color", "xx", T["gb"])[0] == "color"


# ------------------------------------------------------------------ automatic choice
@pytest.mark.parametrize("code,exp", [("US", "us"), ("PR", "us"), ("CA", "ca"), ("GB", "gb"), ("IE", "gb"),
                                      ("NZ", "gb"), ("AU", "au"), ("HK", "hk"), ("TW", None), ("JP", None),
                                      ("DE", None), ("", None), (None, None)])
def test_region_mapping(code, exp):
    assert spelling.variant_for_region(code) == exp


def test_resolve_order():
    assert spelling.resolve("ca", "US") == ("ca", "chosen")
    assert spelling.resolve("off", "CA") == (None, "off")
    assert spelling.resolve("auto", "CA") == ("ca", "country")
    assert spelling.resolve("auto", "TW", "en", "GB") == ("gb", "format")      # English regional format
    assert spelling.resolve("auto", "TW", "zh", "TW") == (None, "none")       # no English convention
    assert spelling.resolve("auto", None, "en", "US") == ("us", "format")
    assert spelling.resolve("auto", "DE", "de", "DE") == (None, "none")


def _settings(tmp_path, **vals):
    s = Settings(path=str(tmp_path / "settings.json"))
    for k, v in vals.items():
        s.set(k.replace("__", "."), v)
    return s


def test_english_spelling_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "region_info", lambda refresh=False: {
        "country": "CA", "country_source": "Windows country or region", "format_lang": "en",
        "format_country": "US", "format_name": "en_US"})
    s = _settings(tmp_path)
    assert s.get("transcription.spelling_en") == "auto"
    assert postproc.english_spelling(s) == ("ca", "country")
    assert postproc.spelling_description(s) == "Automatic (Canadian, from Windows country or region: Canada)"
    s.set("transcription.spelling_en", "au")
    assert postproc.spelling_description(s) == "Australian"
    s.set("transcription.spelling_en", "off")
    assert postproc.english_spelling(s) == (None, "off")
    s.set("transcription.spelling_en", "nonsense")     # a damaged settings file: Automatic
    assert postproc.english_spelling(s) == ("ca", "country")
    assert postproc.automatic_spelling_text({"country": "TW", "country_source": "macOS Region",
                                             "format_lang": "zh", "format_country": "TW"}) == \
        "as the model writes; macOS Region: Taiwan"
    assert postproc.automatic_spelling_text({"country": None, "format_lang": "en", "format_country": "GB",
                                             "format_name": "en_GB"}) == "British, from regional format en_GB"
    assert postproc.automatic_spelling_text({"country": None}) == "as the model writes; no country or region found"


def test_windows_country_reading(monkeypatch):
    """GetUserDefaultGeoName first; GetUserGeoID + GetGeoInfoW on older Windows; a UN M.49 code is not a country."""
    import ctypes

    class Fn:
        def __init__(self, f):
            self.f = f

        def __call__(self, *a):
            return self.f(*a)

    def kernel(name=None, geo=244, iso="US", has_name=True):
        class K:
            pass
        k = K()
        if has_name:
            def gn(buf, n):
                buf.value = name
                return len(name) + 1
            k.GetUserDefaultGeoName = Fn(gn)
        k.GetUserGeoID = Fn(lambda c: geo)

        def gi(g, t, buf, n, lang):
            buf.value = iso
            return 3
        k.GetGeoInfoW = Fn(gi)
        return k

    class W:
        pass
    for k, exp in ((kernel("CA"), "CA"), (kernel("419", iso="MX"), "MX"), (kernel(has_name=False, iso="hk"), "HK"),
                   (kernel(has_name=False, geo=-1), None)):
        w = W()
        w.kernel32 = k
        monkeypatch.setattr(ctypes, "windll", w, raising=False)
        assert system._win_country() == exp


def test_region_info_never_fails(monkeypatch):
    monkeypatch.setattr(system, "IS_WIN", True)
    monkeypatch.setattr(system, "_win_country", lambda: 1 / 0)
    info = system.region_info(refresh=True)
    assert info["country"] is None and "error" in info
    monkeypatch.setattr(system, "IS_WIN", False)
    system.region_info(refresh=True)


# ------------------------------------------------------------------ the table file
def test_table_file_is_valid_and_checked(tmp_path):
    data = json.load(open(TABLE, encoding="utf-8"))
    assert data["format"] == 1 and "Kevin Atkinson" in data["notice"]
    for v in ("us", "gb", "ca", "au"):
        words = data["targets"][v]["words"]
        assert len(words) > 5000
        assert all(spelling._PLAIN.match(k) and spelling._PLAIN.match(w) and k != w for k, w in words.items())
        assert not set(words) & spelling.CONTEXT_WORDS           # meaning-dependent words only by the rules
    assert data["targets"]["hk"] == {"same_as": "gb"}
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"format": 1, "targets": {"us": {"words": {"color": "colour"}}}}), encoding="utf-8")
    with pytest.raises(ValueError):
        spelling.load_tables(str(bad))
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"format": 1, "targets": {
        v: {"words": {"color": "colour", "bad key!": "x", "os.system": "y"}} for v in ("us", "gb", "ca", "au")} |
        {"hk": {"same_as": "gb"}}}), encoding="utf-8")
    assert spelling.load_tables(str(odd))["gb"] == {"color": "colour"}   # fields checked one by one
    wrong = tmp_path / "wrong.json"
    wrong.write_text(json.dumps({"format": 2, "targets": {}}), encoding="utf-8")
    with pytest.raises(ValueError):
        spelling.load_tables(str(wrong))


def test_unreadable_table_leaves_text(monkeypatch, tmp_path):
    monkeypatch.setattr(postproc, "SPELLING_TABLE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(postproc.spelling_words, "_warned", False, raising=False)
    assert postproc.respell("color", "gb") == ("color", Counter())
    assert postproc.spelling_words(None) is None


# ------------------------------------------------------------------ in the clean-up after recognition
def test_clean_segments_english_only(tmp_path, monkeypatch):
    monkeypatch.setattr(system, "region_info", lambda refresh=False: {"country": "GB"})
    s = _settings(tmp_path)
    s.set("vocabulary.sets", {"Default": {"scope": "all", "hotwords": ["Color Lab"],
                                         "replacements": [{"heard": "favourite food", "correct": "Favorite Foods"}]}})
    s.set("vocabulary.active", "Default")
    segs = [{"start": 0, "end": 2, "text": "We organized the color plan with the Color Lab."},
            {"start": 2, "end": 4, "text": "Le centre de la ville.", "lang": "fr"},
            {"start": 4, "end": 6, "text": "I like favourite food at the center."}]
    spelled = Counter()
    out, reps = postproc.clean_segments(segs, s, "en", spelled)
    assert [x["text"] for x in out] == ["We organised the colour plan with the Color Lab.", "Le centre de la ville.",
                                        "I like Favorite Foods at the centre."]
    assert out[0]["orig"] == segs[0]["text"]
    assert spelled == Counter({("organized", "organised"): 1, ("color", "colour"): 1, ("center", "centre"): 1})
    s.set("transcription.spelling_en", "us")
    out, _ = postproc.clean_segments([{"start": 0, "end": 1, "text": "Le centre", "lang": "fr"}], s, "fr")
    assert out[0]["text"] == "Le centre"                     # French text is never respelt
    s.set("transcription.spelling_en", "off")
    out, _ = postproc.clean_segments(segs[:1], s, "en")
    assert out[0]["text"] == segs[0]["text"]


def test_summary_prompt_asks_for_the_spelling():
    p = summary.system_prompt("en", "au")
    assert "Use Australian English spelling (for example: colour, organise, centre)." in p
    assert "spelling" not in summary.system_prompt("en", None)
    assert "spelling" not in summary.system_prompt("fr", "au")
    opts = {"family": "qwen3", "lang": "en", "spelling": "ca", "sections": ["overview"], "length": "short",
            "times": False, "instructions": "", "file": "a.wav", "duration_text": "00:01:00",
            "transcript_lang_name": "English"}
    assert "Canadian English spelling" in summary.single_prompt(opts, ["[00:00:01] Hello."])


# ------------------------------------------------------------------ what the window shows
def _window(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QInputDialog
    from sot.modelmgr import ModelManager
    from sot.ui.mainwindow import MainWindow
    from sot.worker import Worker
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(system, "region_info", lambda refresh=False: {
        "country": "CA", "country_source": "Windows country or region", "format_lang": "en",
        "format_country": "CA", "format_name": "en_CA"})
    s = _settings(tmp_path)
    s.set("general.data_folder", str(tmp_path / "data"))
    s.set("general.output_folder", str(tmp_path / "out"))
    s.set("general.first_run_done", True)
    s.set("transcription.download_folder", str(tmp_path / "dl"))
    w = MainWindow(s, ModelManager(s), Worker(str(tmp_path / "data")), os.path.join(ROOT, "app", "app.ico"))
    return app, w, s, QInputDialog


def test_options_shows_automatic_choice(tmp_path, monkeypatch):
    app, w, s, _ = _window(tmp_path, monkeypatch)
    from sot.ui import options as O
    dlg = O.OptionsDialog(w, s, w.manager, 1)
    tab = dlg.trans
    texts = [tab.spell.itemText(i) for i in range(tab.spell.count())]
    assert texts[0] == "Automatic (Canadian, from Windows country or region: Canada)"
    assert texts[1:] == ["American (color, organize, center)", "British (colour, organise, centre)",
                         "Canadian (colour, organize, centre)", "Australian (colour, organise, centre)",
                         "Hong Kong (colour, organise, centre)", "As the model writes"]
    tab.spell.setCurrentIndex(tab.spell.findData("hk"))
    tab.collect(dlg.work)
    assert dlg.work.get("transcription.spelling_en") == "hk"
    dlg.close()
    w.close()


def test_edit_change_english_spelling_redraws_transcript(tmp_path, monkeypatch):
    app, w, s, QInputDialog = _window(tmp_path, monkeypatch)
    w.segments = [{"start": 0.0, "end": 3.0, "text": "We organized the color plan at the Lincoln Center."},
                  {"start": 3.5, "end": 6.0, "text": "Please write a check for $200 and let me check it."}]
    w.job_meta = {"language": "en"}
    w.show_blocks()
    w.update_actions()
    assert w.a_spelling.isEnabled()
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: ("British (colour, organise, centre)", True)))
    w.change_spelling()
    shown = w.editor.toPlainText()
    assert "We organised the colour plan at the Lincoln Center." in shown
    assert "write a cheque for $200 and let me check it." in shown
    assert [x["text"] for x in w.segments][0] == "We organised the colour plan at the Lincoln Center."
    assert w.editor.document().isModified()
    assert any(l.startswith("English spelling changed to British (Edit menu): 3 words") for l in w.cleanup_log)
    w.change_spelling("us")
    assert "We organized the color plan" in w.editor.toPlainText()
    assert "write a check for $200" in w.editor.toPlainText()
    w.change_spelling("us")
    assert "already follows American spelling" in w.stage_label.text()
    w.editor.document().setModified(False)
    w.close()


def test_edit_change_spelling_leaves_other_languages(tmp_path, monkeypatch):
    app, w, s, _ = _window(tmp_path, monkeypatch)
    w.segments = [{"start": 0.0, "end": 3.0, "text": "Le centre de la ville.", "lang": "fr"}]
    w.job_meta = {"language": "fr"}
    w.show_blocks()
    w.change_spelling("us")
    assert "Le centre de la ville." in w.editor.toPlainText()
    assert "no English text" in w.stage_label.text()
    w.editor.document().setModified(False)
    w.close()


def test_edit_change_spelling_also_changes_english_summary(tmp_path, monkeypatch):
    app, w, s, _ = _window(tmp_path, monkeypatch)
    w.segments = [{"start": 0.0, "end": 3.0, "text": "The color plan."}]
    w.job_meta = {"language": "en"}
    w.show_blocks()
    w.summary = {"text": "## Summary\nThe team will organize the color plan. [00:00:01]", "language": "en",
                 "model": {"name": "Test"}, "stats": {}}
    w._show_summary()
    w.change_spelling("au")
    assert w.summary["text"] == "## Summary\nThe team will organise the colour plan. [00:00:01]"
    assert "organise the colour plan" in w.summary_view.text()
    assert w.summary["edited"]
    w.editor.document().setModified(False)
    w.summary_modified = False
    w.close()
