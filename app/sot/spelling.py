"""English spelling by region: American, British, Canadian, Australian and Hong Kong (British -ise).

Pure logic, no UI: convert() takes text and the word table of the chosen spelling and returns the converted text
with a count of each change. The word tables (app/data/spelling.json) come from the English Speller Database
(ESDB, formerly SCOWL and VarCon) by Kevin Atkinson; build/tools/make-spelling-data.py makes them.

What changes: one-to-one spelling differences (colour/color, organise/organize, centre/center, travelled/traveled,
catalogue/catalog, defence/defense ...). Words whose spelling depends on meaning (practice/practise,
licence/license, cheque/check, tyre/tire, metre/meter, storey/story, programme/program) change only when the words
around them make the meaning clear; otherwise they stay as written. Words are left alone when they look like a
name (capitalised in mid-sentence), are part of a vocabulary term, sit in a web address, e-mail address or file
path, or touch a digit or underscore. Vocabulary, grammar and punctuation are not changed."""
import json
import re
from collections import Counter

VARIANTS = [("us", "American"), ("gb", "British"), ("ca", "Canadian"), ("au", "Australian"), ("hk", "Hong Kong")]
NAMES = dict(VARIANTS)
EXAMPLES = {"us": "color, organize, center", "gb": "colour, organise, centre", "ca": "colour, organize, centre",
            "au": "colour, organise, centre", "hk": "colour, organise, centre"}

# Country or region (ISO 3166-1 alpha-2) -> spelling. Regions without their own English spelling convention are
# not listed: the text stays as the model wrote it there.
REGIONS = {"US": "us", "PR": "us", "GU": "us", "VI": "us", "AS": "us", "MP": "us", "UM": "us",
           "CA": "ca", "GB": "gb", "IE": "gb", "NZ": "gb", "AU": "au", "HK": "hk"}


def variant_for_region(code):
    return REGIONS.get((code or "").strip().upper())


def resolve(choice, country=None, fmt_lang=None, fmt_country=None):
    """The spelling to use for a setting: 'us' / 'gb' / 'ca' / 'au' / 'hk', 'off' (as the model writes) or 'auto'.
    Automatic follows the computer's country or region, then its regional format when that is an English one.
    Returns (variant or None, how: 'chosen' / 'country' / 'format' / 'none' / 'off')."""
    if choice in NAMES:
        return choice, "chosen"
    if choice == "off":
        return None, "off"
    v = variant_for_region(country)
    if v:
        return v, "country"
    if (fmt_lang or "").lower() == "en":
        v = variant_for_region(fmt_country)
        if v:
            return v, "format"
    return None, "none"


# ------------------------------------------------------------------ tables
_cache = {}


def load_tables(path):
    """{variant: {word: replacement}} from spelling.json; Hong Kong uses the British table."""
    if path in _cache:
        return _cache[path]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if data.get("format") != 1 or not isinstance(data.get("targets"), dict):
        raise ValueError("not a spelling table")
    out = {}
    for v, t in data["targets"].items():
        if isinstance(t, dict) and isinstance(t.get("words"), dict):
            out[v] = {str(k): str(w) for k, w in t["words"].items()
                      if isinstance(k, str) and isinstance(w, str) and _PLAIN.match(k) and _PLAIN.match(w)}
    for v, t in data["targets"].items():
        if isinstance(t, dict) and t.get("same_as") in out:
            out[v] = out[t["same_as"]]
    if not all(v in out for v in NAMES):
        raise ValueError("the spelling table lacks a variant")
    _cache[path] = out
    return out


# ------------------------------------------------------------------ context rules
_PLAIN = re.compile(r"^[a-z]+(?:'[a-z]+)?$")
NUMBER_WORDS = set("""one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen
sixteen seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million
half few several dozen""".split())
DET = {"a", "an", "the", "my", "your", "his", "her", "its", "our", "their", "this", "these", "those", "each",
       "every", "any", "no", "another", "same", "both", "all", "some", "many", "two", "three", "four"}

# American: the other spellings of these words are not American in any meaning
FIXED = {
    "us": {"practise": "practice", "practises": "practices", "licence": "license", "licences": "licenses",
           "cheque": "check", "cheques": "checks", "tyre": "tire", "tyres": "tires", "metre": "meter",
           "metres": "meters", "programme": "program", "programmes": "programs", "storey": "story",
           "storeys": "stories", "storeyed": "storied"},
    "ca": {"programme": "program", "programmes": "programs", "tyre": "tire", "tyres": "tires"},
    "au": {"programme": "program", "programmes": "programs"},
}

PRACTISE_PREV = {"to", "will", "would", "can", "could", "should", "must", "might", "may", "shall", "don't",
                 "didn't", "doesn't", "i", "we", "you", "they", "let's", "never", "always", "often", "cannot",
                 "can't", "won't", "wouldn't", "shouldn't", "couldn't", "please"}
# 'to practice' is a noun after these ('go to practice', 'return to practice')
PRACTISE_NOT_AFTER = {"return", "returning", "returned", "access", "commitment", "key", "guide", "introduction",
                      "approach", "contribution", "barrier", "barriers", "path", "route", "theory", "code", "go",
                      "goes", "going", "went", "gone", "back", "late", "come", "came", "coming", "head", "headed",
                      "heading", "drive", "drove", "walk", "walked", "way", "get", "got"}
LICENCE_PREV = DET | {"driver's", "drivers", "driving", "fishing", "hunting", "liquor", "software", "business",
                      "marriage", "pilot's", "pilot", "gun", "firearms", "firearm", "tv", "television", "learner's",
                      "learner", "provisional", "full", "valid", "expired", "suspended", "revoked", "under",
                      "without", "broadcasting", "music", "export", "import", "trading", "operating", "dog", "radio",
                      "alcohol", "taxi", "premises", "creative", "proprietary", "commercial", "exclusive",
                      "perpetual", "user", "open-source", "temporary", "special", "professional"}
LICENCE_NEXT = {"plate", "plates", "fee", "fees", "number", "numbers", "holder", "holders", "agreement",
                "agreements", "key", "keys", "file", "files", "terms", "renewal", "renewals", "application",
                "applications", "period", "condition", "conditions", "suspension", "test", "tests", "class"}
CHEQUE_PREV = {"blank", "bounced", "bouncing", "personal", "certified", "cashier's", "traveler's", "traveller's",
               "travelers'", "travellers'", "postdated", "post-dated", "uncashed", "dividend", "payroll",
               "stimulus", "refund", "rebate", "royalty", "giro", "bank", "rubber", "pay", "welfare", "void",
               "voided", "cancelled", "canceled"}
CHEQUE_VERBS = {"write", "wrote", "written", "writing", "writes", "cash", "cashed", "cashing", "cashes", "deposit",
                "deposited", "depositing", "deposits", "bounce", "bounced", "endorse", "endorsed", "endorsing"}
CHEQUE_VERBS_DET = CHEQUE_VERBS | {"sign", "signed", "signing", "signs", "mail", "mailed", "mailing"}  # + a / the
CHEQUE_NEXT = {"book", "books", "stub", "stubs"}
PAY = {"pay", "paid", "paying", "pays", "payment", "payments", "accept", "accepted", "accepts", "accepting"}
MONEY_AFTER = re.compile(r"\s+(?:for|of)\s+(?:(?:[A-Z]{1,2})?[$£€¥]\s?\d|\d[\d,.]*\s*(?:dollars?|pounds?|euros?|"
                         r"bucks|grand|yuan|yen|rupees?)\b)", re.IGNORECASE)
TYRE_PREV = {"flat", "spare", "car", "bike", "bicycle", "front", "rear", "winter", "snow", "summer", "all-season",
             "worn", "bald", "rubber", "truck", "tractor", "radial", "tubeless", "burst", "punctured", "racing",
             "slick", "new", "used", "retread", "inner", "outer", "a", "the", "my", "your", "his", "her", "our",
             "their", "its", "each", "every", "four", "two", "these", "those", "both"}
TYRE_NEXT = {"pressure", "pressures", "change", "changes", "changing", "shop", "shops", "tread", "treads", "iron",
             "wear", "rotation", "rotations", "swing", "swings", "track", "tracks", "marks", "fitter", "fitters",
             "fitting", "repair", "repairs", "pump", "gauge", "valve", "valves", "size", "sizes", "dealer",
             "dealers", "company", "companies", "manufacturer", "manufacturers", "industry", "blowout",
             "puncture", "punctures", "rim", "rims", "wall", "walls", "sidewall", "sidewalls"}
METRE_PREV = {"square", "cubic", "per"}
METRE_NEXT = {"tall", "long", "wide", "high", "deep", "thick", "away", "apart", "squared", "per", "above", "below",
              "underground", "across"}
METRE_OF = {"hundreds", "thousands", "tens", "couple", "few", "dozens", "millions", "lot", "lots"}
STOREY_HYPHEN = re.compile(r"(?:\d+|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|"
                           r"twenty|thirty|forty|fifty|sixty|hundred|multi|single|double|triple|split))\s*-\s*$",
                           re.IGNORECASE)
STOREY_NEXT = {"high", "tall", "up", "down", "above", "below", "underground", "building", "buildings", "tower",
               "towers"}
PROGRAMME_PREV = {"tv", "television", "radio", "training", "exchange", "degree", "master's", "masters",
                  "doctoral", "phd", "research", "aid", "nuclear", "space", "work", "study", "loyalty", "rewards",
                  "reward", "vaccination", "immunisation", "immunization", "development", "education",
                  "educational", "outreach", "support", "mentoring", "mentorship", "apprenticeship", "graduate",
                  "undergraduate", "postgraduate", "fitness", "exercise", "treatment", "rehabilitation", "welfare",
                  "housing", "assistance", "funding", "grant", "pilot", "breakfast", "news", "cookery", "cooking",
                  "documentary", "children's", "comedy", "talk", "quiz", "affairs", "weapons", "missile",
                  "investment", "austerity", "reform", "recovery", "stimulus", "internship", "leadership",
                  "residency", "placement", "volunteer", "community", "youth", "health", "screening", "savings",
                  "pension", "scholarship", "bursary", "certificate", "diploma", "mba", "bachelor's", "honours",
                  "honors", "conference", "festival", "concert", "theatre", "theater", "events"}
PROGRAMME_NEXT = {"manager", "managers", "director", "directors", "coordinator", "coordinators", "co-ordinator",
                  "officer", "officers", "leader", "leaders", "management", "participants", "committee", "board"}
PROGRAMME_OF = {"events", "study", "studies", "work", "works", "activities", "reform", "reforms", "action",
                "measures", "talks", "lectures", "visits", "exercises", "music", "concerts"}


def _is_number(w):
    return bool(w) and (w[0].isdigit() or w in NUMBER_WORDS)


def _context(w, v, prev, nxt, before, after):
    """Replacement for a word whose spelling depends on its meaning, or None to leave it.
    prev / nxt: up to two words before / after in the same clause (lower case); before / after: the raw text."""
    if v in FIXED and w in FIXED[v]:
        return FIXED[v][w]
    if v == "us":
        return None
    p1, p2 = (prev[-1] if prev else ""), (prev[-2] if len(prev) > 1 else "")
    n1, n2 = (nxt[0] if nxt else ""), (nxt[1] if len(nxt) > 1 else "")
    if w == "practice" and p1 in PRACTISE_PREV and not (p1 == "to" and p2 in PRACTISE_NOT_AFTER):
        return "practise"
    if w == "practices" and p1 in ("he", "she", "it", "who"):
        return "practises"
    if w in ("license", "licenses") and (n1 in LICENCE_NEXT or p1 in LICENCE_PREV) and p1 not in ("to", "who"):
        return "licence" if w == "license" else "licences"
    if w in ("check", "checks") and not after.startswith("-"):
        if p1 in CHEQUE_PREV or p1 in CHEQUE_VERBS or (p1 in DET and p2 in CHEQUE_VERBS_DET) or \
                (w == "check" and p1 == "by" and p2 in PAY) or n1 in CHEQUE_NEXT or MONEY_AFTER.match(after):
            return "cheque" if w == "check" else "cheques"
    if w in ("tire", "tires") and v != "ca" and n1 != "of" and (p1 in TYRE_PREV or n1 in TYRE_NEXT):
        return "tyre" if w == "tire" else "tyres"
    if w in ("meter", "meters") and (re.search(r"\d\s*-?\s*$", before) or _is_number(p1) or p1 in METRE_PREV or
                                     (p1 == "of" and p2 in METRE_OF) or (p1 == "a" and p2 in ("half", "quarter"))
                                     or n1 in METRE_NEXT):
        return "metre" if w == "meter" else "metres"
    if w in ("story", "stories") and (STOREY_HYPHEN.search(before) or
                                      (w == "stories" and _is_number(p1) and n1 in STOREY_NEXT)):
        return "storey" if w == "story" else "storeys"
    if w in ("program", "programs") and v in ("gb", "hk") and (
            p1 in PROGRAMME_PREV or n1 in PROGRAMME_NEXT or (n1 == "of" and n2 in PROGRAMME_OF)):
        return "programme" if w == "program" else "programmes"
    return None


CONTEXT_WORDS = {"practice", "practices", "license", "licenses", "check", "checks", "tire", "tires", "meter",
                 "meters", "story", "stories", "program", "programs"} | {w for f in FIXED.values() for w in f}

# ------------------------------------------------------------------ conversion
TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*")
SKIP = re.compile(r"(?:https?://|www\.)\S+|[\w.+-]+@[\w-]+\.[\w.]+|\S*[\\/]\S*")
CTX_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)*|\d[\d,.]*")   # words and numbers around a word
CLAUSE = re.compile(r"[.!?;:,()\[\]\"“”—–\n]")


def _sentence_start(text, a):
    j = a - 1
    while j >= 0 and text[j] in " \t\r\"'“‘([":
        j -= 1
    return j < 0 or text[j] in ".!?…\n"


def _case(src, dst):
    if src.islower():
        return dst
    if src.isupper():
        return dst.upper() if len(src) > 1 else None
    if src[0].isupper() and src[1:].islower():
        return dst[0].upper() + dst[1:]
    return None   # mixed case: a name or a code


def _protected_spans(text, protect):
    spans = []
    for term in protect:
        term = (term or "").strip()
        if not term or not TOKEN.search(term):
            continue
        for m in re.finditer(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", text, re.IGNORECASE):
            spans.append((m.start(), m.end()))
    for m in SKIP.finditer(text):
        spans.append((m.start(), m.end()))
    return spans


def convert(text, variant, words, protect=()):
    """Text in the given spelling. words: that spelling's table ({word: replacement}, see load_tables).
    protect: vocabulary terms to leave as they are. Returns (text, Counter({(old, new): count}))."""
    changes = Counter()
    if not text or variant not in NAMES:
        return text, changes
    spans = _protected_spans(text, protect)
    toks = list(TOKEN.finditer(text))
    out, last = [], 0
    for i, m in enumerate(toks):
        a, b = m.span()
        src = m.group()
        if any(s < b and a < e for s, e in spans):
            continue
        if (a and (text[a - 1].isdigit() or text[a - 1] == "_")) or (b < len(text) and
                                                                     (text[b].isdigit() or text[b] == "_")):
            continue
        key = src.lower().replace("’", "'")
        if src[0].isupper() and not src.isupper():
            start_of_sentence = _sentence_start(text, a)
            nxt_cap = i + 1 < len(toks) and toks[i + 1].group()[0].isupper() and toks[i + 1].group() != "I" and \
                not CLAUSE.search(text[b:toks[i + 1].start()])
            if not start_of_sentence or nxt_cap:
                continue     # a name, a title or a heading: leave it
        new = None
        if key in CONTEXT_WORDS:
            before = text[max(0, a - 40):a]
            after = text[b:b + 40]
            clause_b = CLAUSE.split(text[max(0, a - 80):a])[-1]
            clause_a = CLAUSE.split(text[b:b + 80])[0]
            prev = [t.lower().replace("’", "'") for t in CTX_TOKEN.findall(clause_b)][-2:]
            nxt = [t.lower().replace("’", "'") for t in CTX_TOKEN.findall(clause_a)][:2]
            new = _context(key, variant, prev, nxt, before, after)
        else:
            new = words.get(key)
            if new is None and key.endswith("'s") and key[:-2] in words:   # possessive of a listed word
                new = words[key[:-2]] + key[-2:]
        if not new or new == key:
            continue
        cased = _case(src, new)
        if cased is None:
            continue
        if "’" in src:
            cased = cased.replace("'", "’")
        out.append(text[last:a])
        out.append(cased)
        last = b
        changes[(key, new)] += 1
    if not changes:
        return text, changes
    out.append(text[last:])
    return "".join(out), changes


def summary_line(changes, limit=12):
    """'colour → color ×3, centre → center' for Raw output."""
    items = sorted(changes.items(), key=lambda kv: (-kv[1], kv[0]))
    parts = [f"{a} → {b}" + (f" ×{n}" if n > 1 else "") for (a, b), n in items[:limit]]
    more = len(items) - limit
    return ", ".join(parts) + (f" and {more} more" if more > 0 else "")
