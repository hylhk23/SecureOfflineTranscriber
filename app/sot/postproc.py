"""Clean-up after recognition: invented-text filter, script conversion,
vocabulary replacements, English spelling, filler words, paragraphs."""
import logging
import os
import re
from collections import Counter

from . import paths, spelling, system

log = logging.getLogger("sot")
SPELLING_TABLE = os.path.join(paths.APP_DIR, "data", "spelling.json")

CJK = re.compile(r"[㐀-鿿豈-﫿]")
_converters = {}


def has_cjk(text):
    return bool(CJK.search(text))


def converter(target):
    """OpenCC converter: hk / tw = Traditional, cn = Simplified."""
    conf = {"hk": "s2hk", "tw": "s2tw", "cn": "t2s"}[target]
    if conf not in _converters:
        import opencc
        _converters[conf] = opencc.OpenCC(conf)
    return _converters[conf]


def convert_script(text, target):
    if not target or not has_cjk(text):
        return text
    try:
        return converter(target).convert(text)
    except Exception:
        return text


def script_for(lang, settings):
    if lang == "yue":
        return settings.get("transcription.script_yue")
    if lang == "zh":
        return settings.get("transcription.script_zh")
    return None


def _norm(t):
    return re.sub(r"[\W_]+", " ", t.lower()).strip()


def filter_segments(segs, settings):
    """Drop known invented phrases and runaway repeats; return (kept, dropped[(seg, reason)])."""
    kept, dropped = [], []
    known = [_norm(p) for p in settings.get("advanced.filter_list") if p.strip()] \
        if settings.get("advanced.filter_known") else []
    limit = int(settings.get("advanced.repeat_limit"))
    run_text, run_len = None, 0
    for s in segs:
        n = _norm(s["text"])
        if not n:
            dropped.append((s, "only symbols or punctuation"))
            continue
        if known and any(n == k or (n.startswith(k) and len(n) <= len(k) + 12) for k in known):
            dropped.append((s, "known invented phrase"))
            continue
        if n == run_text:
            run_len += 1
            if run_len > limit:
                dropped.append((s, f"repeated more than {limit} times in a row"))
                continue
        else:
            run_text, run_len = n, 1
        kept.append(s)
    return kept, dropped


def apply_replacements(text, replacements, script=None):
    """Returns (new_text, [(heard, correct, count)]). Chinese entries follow the chosen script."""
    log = []
    for r in replacements:
        heard, correct = (r.get("heard") or "").strip(), r.get("correct") or ""
        if not heard:
            continue
        if script:
            heard, correct = convert_script(heard, script), convert_script(correct, script)
        flags = 0 if r.get("case") else re.IGNORECASE
        pat = re.escape(heard)
        if r.get("whole", True) and not has_cjk(heard):
            pat = r"(?<!\w)" + pat + r"(?!\w)"
        text, n = re.subn(pat, lambda m: correct, text, flags=flags)
        if n:
            log.append((r.get("heard"), r.get("correct"), n))
    return text, log


def english_spelling(settings):
    """The English spelling to write in: (variant or None, how), see spelling.resolve."""
    choice = settings.get("transcription.spelling_en")
    if choice not in spelling.NAMES and choice != "off":
        choice = "auto"
    info = system.region_info() if choice == "auto" else {}
    return spelling.resolve(choice, info.get("country"), info.get("format_lang"), info.get("format_country"))


def automatic_spelling_text(info=None):
    """What Automatic means on this computer: 'Canadian, from Windows country or region: Canada'."""
    info = info if info is not None else system.region_info()
    variant, how = spelling.resolve("auto", info.get("country"), info.get("format_lang"), info.get("format_country"))
    where = f"{info.get('country_source') or 'country or region'}: {system.country_name(info['country'])}" \
        if info.get("country") else ""
    if how == "country":
        return f"{spelling.NAMES[variant]}, from {where}"
    if how == "format":
        return f"{spelling.NAMES[variant]}, from regional format {info.get('format_name')}"
    return "as the model writes" + (f"; {where}" if where else "; no country or region found")


def spelling_description(settings):
    """The English spelling setting in words, for Raw output."""
    variant, how = english_spelling(settings)
    if how == "chosen":
        return spelling.NAMES[variant]
    if how == "off":
        return "as the model writes"
    return f"Automatic ({automatic_spelling_text()})"


def spelling_words(variant):
    """The word table of a spelling, or None if the table cannot be read (the text is then left as written)."""
    if not variant:
        return None
    try:
        return spelling.load_tables(SPELLING_TABLE)[variant]
    except Exception as e:
        if not getattr(spelling_words, "_warned", False):
            log.error("English spelling table not readable (%s): text is left as the model wrote it", e)
            spelling_words._warned = True
        return None


def vocabulary_terms(settings, language=None):
    """Hotwords and replacement words of the active vocabulary set: kept as they are by the spelling change."""
    hot, reps = settings.vocabulary(language)
    return [h for h in hot if isinstance(h, str)] + [r.get("correct") for r in reps
                                                     if isinstance(r.get("correct"), str) and r.get("correct")]


def respell(text, variant, protect=()):
    """English text in the given spelling. Returns (text, Counter of changes)."""
    words = spelling_words(variant)
    if words is None or not text:
        return text, Counter()
    return spelling.convert(text, variant, words, protect)


def remove_fillers(text, fillers):
    for f in fillers:
        f = f.strip()
        if not f:
            continue
        if has_cjk(f):
            text = text.replace(f + "，", "").replace(f + ",", "").replace(f, "")
        else:
            text = re.sub(r"(?<!\w)" + re.escape(f) + r"(?!\w)[,.]?\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", text).strip()


def clean_segments(segs, settings, language_hint=None, spelled=None):
    """Script conversion, replacements and English spelling on each segment. Returns (segments, replacement log);
    spelled, if given (a Counter), counts the spelling changes."""
    _, reps = settings.vocabulary(language_hint)
    variant, _ = english_spelling(settings)
    protect = vocabulary_terms(settings, language_hint) if variant else []
    total = {}
    out = []
    for s in segs:
        s = dict(s)
        if "orig" not in s:
            s["orig"] = s["text"]
        script = script_for(s.get("lang") or language_hint, settings)
        if not script and has_cjk(s["text"]):
            script = settings.get("transcription.script_yue") if (language_hint == "yue") else \
                settings.get("transcription.script_zh")
        text = convert_script(s["text"], script)
        text, log = apply_replacements(text, reps, script)
        for heard, correct, n in log:
            total[(heard, correct)] = total.get((heard, correct), 0) + n
        if variant and (s.get("lang") or language_hint) == "en":
            text, ch = respell(text, variant, protect)
            if spelled is not None:
                spelled.update(ch)
        if settings.get("output.remove_fillers"):
            text = remove_fillers(text, settings.get("output.filler_list"))
        s["text"] = text
        out.append(s)
    return out, [(h, c, n) for (h, c), n in total.items()]


def trim_overlap(prev, new, max_words=4):
    """When a stopped job resumes, the first words can repeat the last ones ('Paul can' | 'can also').
    Returns new without the repeated start."""
    if not prev or not new:
        return new
    if has_cjk(prev[-1:]) and has_cjk(new[:1]):
        for n in range(min(6, len(prev), len(new)), 0, -1):
            if prev[-n:] == new[:n]:
                return new[n:].lstrip("，、 ")
        return new
    norm = lambda w: re.sub(r"[^\w']", "", w.lower())
    pw = [norm(w) for w in prev.split()]
    nw = new.split()
    least = 2 if prev.rstrip()[-1:] in ".!?" else 1  # 'so. So we...' can be real speech: need two words then
    for n in range(min(max_words, len(pw), len(nw)), least - 1, -1):
        if pw[-n:] == [norm(w) for w in nw[:n]] and all(pw[-n:]):
            rest = " ".join(nw[n:]).lstrip(",.;: ")
            return rest[:1].upper() + rest[1:] if rest and prev.rstrip()[-1:] in ".!?" else rest
    return new


def join_text(a, b):
    if not a:
        return b
    if has_cjk(a[-1:]) or has_cjk(b[:1]):
        return a + b
    return a + " " + b


def paragraphs(segs, pause=2.0, max_chars=600):
    """Group segments into paragraphs at pauses or length (Options > Output)."""
    paras = []
    for i, s in enumerate(segs):
        if paras and s["start"] - paras[-1]["end"] < pause and len(paras[-1]["text"]) + len(s["text"]) <= max_chars:
            p = paras[-1]
            p["text"] = join_text(p["text"], s["text"])
            p["end"] = s["end"]
            p["segs"].append(i)
            p["flag"] = p["flag"] or s.get("flag", "")
        else:
            p = {"start": s["start"], "end": s["end"], "text": s["text"], "segs": [i], "flag": s.get("flag", ""),
                 "lang": s.get("lang", ""), "subs": []}
            paras.append(p)
        # where this segment's text sits in the paragraph, for following playback sentence by sentence
        c1 = len(p["text"])
        p["subs"].append({"start": s["start"], "end": s["end"], "flag": s.get("flag", ""),
                          "c0": c1 - len(s["text"]), "c1": c1})
    return paras


def segment_blocks(segs):
    """One block per segment ('Times: each segment'), with the same sentence positions."""
    return [dict(s, subs=[{"start": s["start"], "end": s["end"], "flag": s.get("flag", ""), "c0": 0,
                           "c1": len(s["text"])}]) for s in segs]
