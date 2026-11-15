"""Build app/data/spelling.json, the English spelling tables, from the English Speller Database (ESDB, formerly
SCOWL and VarCon, https://github.com/en-wl/wordlist, used at commit 1e5b7d3a, 24 June 2026).

Usage:  python make-spelling-data.py <scowl.db built by running 'make' in the ESDB folder> <output .json>

ESDB puts the spellings of one word with one meaning and one part of speech in a group, and marks each spelling
with a spelling code (A American, B British -ise, Z British -ize, C Canadian, D Australian) and a variant level
(0 = preferred). For each target spelling:
  * the replacement is the target's preferred form in the group, when the group has exactly one;
  * a spelling is replaced only if some spelling code accepts it (variant level 0 to 2): a regional spelling,
    not an informal or rare one (nite, thru, cosey are left alone);
  * a spelling the target accepts (variant level 0 to 2) is left alone;
  * a word that would get different replacements in different groups (practice, the noun, stays; practice, the
    verb, becomes practise in British) is left alone; the program's context rules handle the common ones;
  * EXCLUDE lists pairs that are different words, sound different or split by meaning in a way the data does not
    show; KEEP lists spellings a target also accepts in a common use (British legal 'judgment').
Words up to ESDB size 70 are used (35 to 70: common to standard dictionary words); the generated list is not a
word list larger than 80, so the UK Advanced Cryptics Dictionary notice is not required, but THIRD-PARTY-LICENSES.txt
carries it anyway, with the ESDB, Australian data and WordNet notices."""
import json
import re
import sqlite3
import sys
from collections import defaultdict

MAX_SIZE = 70
TARGETS = {"us": "A", "gb": "B", "ca": "C", "au": "D"}
ACCEPTED_LEVEL = 2     # 0 preferred, 1 '.', 2 '=': the target accepts these as they are
EXCLUDE = {
    # different words or different pronunciation
    "aluminum", "aluminium", "aluminums", "aluminiums", "airplane", "aeroplane", "airplanes", "aeroplanes",
    "math", "maths", "mom", "mum", "moms", "mums", "mommy", "mummy", "mommies", "mummies", "specialty", "speciality",
    "specialties", "specialities", "ton", "tonne", "tons", "tonnes", "sled", "sledge", "sledding", "sledging",
    "whiskey", "whisky", "whiskeys", "whiskies",
    # irregular past forms: both are used in British, so they are not spelling differences to correct
    "burned", "burnt", "learned", "learnt", "dreamed", "dreamt", "spelled", "spelt", "spoiled", "spoilt",
    "smelled", "smelt", "leaped", "leapt", "spilled", "spilt", "dwelled", "dwelt", "knelt", "kneeled",
    "dived", "dove", "pleaded", "pled", "sneaked", "snuck", "proved", "proven", "lit", "lighted",
    # one form in one region has two meanings with different spellings elsewhere
    "kerb", "curb", "kerbs", "curbs", "disc", "disk", "discs", "disks", "draft", "draught", "drafts", "draughts",
    "drafty", "draughty", "annex", "annexe", "annexes", "racket", "racquet", "rackets", "racquets", "jail", "gaol",
    "jails", "gaols", "dialog", "dialogue", "dialogs", "dialogues", "analog", "analogue", "analogs", "analogues",
    "inquiry", "enquiry", "inquiries", "enquiries", "inquire", "enquire", "inquired", "enquired", "inquiring",
    "enquiring", "inquires", "enquires", "ensure", "insure", "ensured", "insured", "ensures", "insures",
    "ensuring", "insuring", "naught", "nought", "naughts", "noughts", "while", "whilst", "among", "amongst",
    "amid", "amidst", "toward", "towards", "afterward", "afterwards", "backward", "backwards", "forward",
    "forwards", "upward", "upwards", "downward", "downwards", "onward", "onwards", "anyway", "anyways",
    # handled by the program's context rules (sot/spelling.py)
    "practice", "practise", "practices", "practises", "license", "licence", "licenses", "licences", "check",
    "cheque", "checks", "cheques", "meter", "metre", "meters", "metres", "tire", "tyre", "tires", "tyres",
    "program", "programme", "programs", "programmes", "story", "storey", "stories", "storeys", "storied",
    "storeyed",
}
# spellings a target also accepts in a common use, so they are never changed for it
KEEP = {"gb": {"judgment", "judgments"}, "au": {"judgment", "judgments"}}

WORD = re.compile(r"^[a-z]+(?:'[a-z]+)?$")


def main(db, out):
    c = sqlite3.connect(db)
    rows = c.execute("""select group_id, pos, spelling, variant_level, lower(word), min(size)
                          from _scowl_main
                         where spelling != '_'
                         group by group_id, pos, spelling, variant_level, lower(word)""").fetchall()
    by_gp = defaultdict(list)
    for g, p, sp, lv, w, sz in rows:
        if WORD.match(w):
            by_gp[(g, p)].append((sp, lv, w, sz))
    data = {"format": 1,
            "source": "English Speller Database (ESDB, formerly SCOWL and VarCon), "
                      "https://github.com/en-wl/wordlist, commit 1e5b7d3a (24 June 2026)",
            "notice": "Copyright 2000-2026 by Kevin Atkinson; Australian data copyright 2016 by Benjamin Titze. "
                      "See THIRD-PARTY-LICENSES.txt in the program folder.",
            "targets": {}}
    for name, code in TARGETS.items():
        maps = defaultdict(set)
        accepted = set()
        for (g, p), entries in by_gp.items():
            if min(e[3] for e in entries) > MAX_SIZE:
                continue
            codes = {e[0] for e in entries}
            eff = code                      # B implies Z; Z (or B) implies C; B implies D
            if code == "C" and "C" not in codes:
                eff = "Z" if "Z" in codes else "B"
            if code == "D" and "D" not in codes:
                eff = "B"
            accepted |= {e[2] for e in entries if e[0] == eff and e[1] <= ACCEPTED_LEVEL}
            pref = {e[2] for e in entries if e[0] == eff and e[1] == 0}
            if len(pref) != 1:
                continue
            t = next(iter(pref))
            regional = {e[2] for e in entries if e[1] <= ACCEPTED_LEVEL}   # accepted by some spelling code
            for sp, lv, w, sz in entries:
                if w in regional and sz <= MAX_SIZE:
                    maps[w].add(t)
        words = {}
        for w, ts in maps.items():
            if w in EXCLUDE or w in accepted or w in KEEP.get(name, ()) or len(ts) != 1:
                continue
            t = next(iter(ts))
            if t != w and t not in EXCLUDE:
                words[w] = t
        data["targets"][name] = {"words": dict(sorted(words.items()))}
        print(name, len(words), "words", file=sys.stderr)
    data["targets"]["hk"] = {"same_as": "gb"}   # Hong Kong: British -ise spelling
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
