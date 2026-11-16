"""Speaker identification (version 2.4.0): pure functions, no window, no files, no models.

The worker process finds who spoke when ("turns") and one averaged voiceprint per speaker (sot.diarize).
This module then
  - gives each transcript segment (or edited paragraph) a speaker, by the largest time overlap,
  - in a two-channel recording, decides "me" (left, microphone) or "others" (right, computer audio) by loudness,
  - links the speakers of 30-minute blocks of a long recording,
  - matches speakers to the voice library, one-to-one, best pairs first,
  - names speakers ("Speaker 1", ...) and counts their talk time.
A voiceprint is a list of numbers (192 for the CAM++ voice models), compared by cosine similarity."""
import math

ME = "ME"                 # the left channel (microphone) of a two-channel recording
UNSURE_OVERLAP = 0.6      # a segment less than 60% inside one speaker's turns is marked (?)
SECOND_SPEAKER = 0.3      # ... or when a second speaker covers 30% of it (people talking over each other)
NEAREST_GAP_S = 1.0       # a segment between turns takes the nearest turn within this distance, marked (?)
CHANNEL_DB = 3.0          # two-channel files: this much louder on one side decides the side
MIN_EMBED_S = 1.5         # turns shorter than this are not used for voiceprints (too short to tell voices apart)
LINK_SIMILARITY = 0.55    # long recordings: speakers of two blocks are the same person at this similarity or more
STRICTNESS = {"normal": 0.55, "strict": 0.65}  # library match: cosine similarity needed
HIGH_MARGIN = 0.10        # a match this far above the cut-off is "high", else "medium"
LEAD_NEEDED = 0.05        # the best person must lead the second best by this much, else no name is given

# Markers: a shape and a colour per speaker. The name and the shape carry the meaning; the colour helps.
# Colours (Okabe-Ito hues, darkened for light and lightened for dark backgrounds) have at least 4.5:1
# contrast on the window and text backgrounds of both themes, also under protan and deutan simulation.
SHAPES = ["●", "■", "▲", "◆", "▼", "★", "✚", "✱"]  # ● ■ ▲ ◆ ▼ ★ ✚ ✱
COLOURS_LIGHT = ["#0068a3", "#a54900", "#007555", "#a53f78", "#8a6000", "#146b9c", "#746c09", "#666666"]
COLOURS_DARK = ["#21afff", "#ff933f", "#00bd89", "#d591b7", "#e69f00", "#56b4e9", "#f0e442", "#9f9f9f"]


# ------------------------------------------------------------------ vectors
def norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 0 else list(v)


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return -1.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return -1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def centroid(vectors, weights=None):
    """Average of unit-length voiceprints, again unit length. Weights: seconds of speech behind each."""
    vectors = [norm(v) for v in vectors if v]
    if not vectors:
        return []
    weights = weights or [1.0] * len(vectors)
    dim = len(vectors[0])
    acc = [0.0] * dim
    for v, w in zip(vectors, weights):
        if len(v) == dim:
            for i, x in enumerate(v):
                acc[i] += x * w
    return norm(acc)


# ------------------------------------------------------------------ who said each segment
def overlap(a0, a1, b0, b1):
    return max(0.0, min(a1, b1) - max(a0, b0))


def assign(items, turns):
    """items: [{"start", "end"}] (segments or paragraphs). turns: [{"start", "end", "speaker"}].
    Returns [(speaker or None, unsure)] in the same order."""
    turns = sorted(turns, key=lambda t: t["start"])
    out = []
    for it in items:
        s, e = float(it["start"]), float(it["end"])
        dur = max(e - s, 1e-3)
        per = {}
        for t in turns:
            if t["start"] >= e:
                break
            o = overlap(s, e, t["start"], t["end"])
            if o > 0:
                per[t["speaker"]] = per.get(t["speaker"], 0.0) + o
        if per:
            ranked = sorted(per.items(), key=lambda kv: -kv[1])
            spk, best = ranked[0]
            second = ranked[1][1] if len(ranked) > 1 else 0.0
            unsure = best / dur < UNSURE_OVERLAP or second / dur >= SECOND_SPEAKER
            out.append((spk, unsure))
            continue
        near, gap = None, NEAREST_GAP_S
        for t in turns:  # between turns: the nearest one, if close
            g = max(t["start"] - e, s - t["end"], 0.0)
            if g <= gap:
                near, gap = t["speaker"], g
        out.append((near, True) if near else (None, False))
    return out


def channel_side(diff_db):
    """Two-channel recording: left minus right level of a segment, in dB. Returns ("me" | "others", unsure)."""
    if diff_db is None or (isinstance(diff_db, float) and math.isnan(diff_db)):
        return "others", True
    if diff_db >= CHANNEL_DB:
        return "me", False
    if diff_db <= -CHANNEL_DB:
        return "others", False
    return ("me" if diff_db > 0 else "others"), True


def assign_split(items, turns, diffs):
    """Two-channel recording: the louder side decides me or others; others are told apart by the right channel's
    turns. diffs: left minus right level of each item, in dB (None when silent)."""
    others = assign(items, turns)
    out = []
    for (spk, unsure), d in zip(others, diffs):
        side, side_unsure = channel_side(d)
        if side == "me":
            out.append((ME, side_unsure))
        else:
            out.append((spk, unsure or side_unsure) if spk else (None, False))
    return out


def majority(block, segs):
    """An edited paragraph keeps its text: it takes the speaker who said most of the segments inside its time.
    Returns (speaker or None, unsure)."""
    s, e = float(block["start"]), float(block["end"])
    per = {}
    unsure = False
    for sg in segs:
        o = overlap(s, e, float(sg["start"]), float(sg["end"]))
        if o > 0 and sg.get("spk"):
            per[sg["spk"]] = per.get(sg["spk"], 0.0) + o
            unsure = unsure or bool(sg.get("spk_unsure"))
    if not per:
        return None, False
    ranked = sorted(per.items(), key=lambda kv: -kv[1])
    total = sum(per.values())
    return ranked[0][0], unsure or ranked[0][1] / total < UNSURE_OVERLAP


# ------------------------------------------------------------------ long recordings: link the blocks
def link_blocks(clusters, num_speakers=0, similarity=LINK_SIMILARITY):
    """clusters: [{"id", "block", "centroid", "speech_s"}] from separately processed 30-minute blocks.
    Joins clusters of different blocks that sound alike (never two of the same block, which the diarizer
    already told apart). With num_speakers > 0 joins until that many remain. Returns {cluster id: group number}."""
    groups = [{"ids": [c["id"]], "blocks": {c["block"]}, "vecs": [c["centroid"]], "w": [max(c["speech_s"], 0.1)]}
              for c in clusters if c.get("centroid")]
    while len(groups) > 1:
        best, pair = -2.0, None
        cents = [centroid(g["vecs"], g["w"]) for g in groups]
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                if groups[i]["blocks"] & groups[j]["blocks"]:
                    continue
                sim = cosine(cents[i], cents[j])
                if sim > best:
                    best, pair = sim, (i, j)
        if pair is None:
            break
        if num_speakers > 0:
            if len(groups) <= num_speakers:
                break
        elif best < similarity:
            break
        i, j = pair
        g, h = groups[i], groups.pop(j)
        g["ids"] += h["ids"]
        g["blocks"] |= h["blocks"]
        g["vecs"] += h["vecs"]
        g["w"] += h["w"]
    out = {}
    for n, g in enumerate(groups):
        for cid in g["ids"]:
            out[cid] = n
    for c in clusters:  # a cluster without a voiceprint keeps a group of its own
        if c["id"] not in out:
            out[c["id"]] = len(set(out.values()))
    return out


# ------------------------------------------------------------------ the voice library
def match(speakers, people, strictness="normal"):
    """speakers: {speaker id: voiceprint}. people: [{"id", "name", "centroid"}] (same voice model only).
    One person per speaker and one speaker per person, best pairs first. Returns
    {speaker id: {"person": id, "name": name, "score": similarity, "level": "high" | "medium"}}."""
    cut = STRICTNESS.get(strictness, STRICTNESS["normal"])
    pairs = []
    for sid, v in speakers.items():
        if not v:
            continue
        scores = sorted(((cosine(v, p["centroid"]), p) for p in people if p.get("centroid")), key=lambda x: -x[0])
        for k, (sc, p) in enumerate(scores):
            if sc < cut:
                break
            runner = scores[k + 1][0] if k + 1 < len(scores) else -1.0
            if k == 0 and sc - runner < LEAD_NEEDED:
                break  # two people sound alike to this speaker: give no name rather than a wrong one
            pairs.append((sc, sid, p))
    pairs.sort(key=lambda x: -x[0])
    out, used = {}, set()
    for sc, sid, p in pairs:
        if sid in out or p["id"] in used:
            continue
        out[sid] = {"person": p["id"], "name": p["name"], "score": round(sc, 3),
                    "level": "high" if sc >= cut + HIGH_MARGIN else "medium"}
        used.add(p["id"])
    return out


# ------------------------------------------------------------------ names, order, statistics
def order_of_appearance(items):
    seen = []
    for it in items:
        s = it.get("spk")
        if s and s not in seen:
            seen.append(s)
    return seen


def default_names(order, me_name="Me"):
    """'Speaker 1', 'Speaker 2' ... in order of first appearance; the left channel is called me_name."""
    out, n = {}, 0
    for s in order:
        if s == ME:
            out[s] = me_name or "Me"
        else:
            n += 1
            out[s] = f"Speaker {n}"
    return out


def talk_stats(items):
    """{speaker: {"seconds", "turns", "share"}}: talk time, number of turns (runs of the same speaker), share of
    all speech."""
    st = {}
    prev = None
    for it in items:
        s = it.get("spk")
        if not s:
            prev = None
            continue
        d = st.setdefault(s, {"seconds": 0.0, "turns": 0, "share": 0.0})
        d["seconds"] += max(0.0, float(it["end"]) - float(it["start"]))
        if s != prev:
            d["turns"] += 1
        prev = s
    total = sum(d["seconds"] for d in st.values()) or 1.0
    for d in st.values():
        d["share"] = d["seconds"] / total
    return st


def marker(index):
    """(shape, light colour, dark colour) of the n-th speaker (0-based)."""
    i = index % len(SHAPES)
    return SHAPES[i], COLOURS_LIGHT[i], COLOURS_DARK[i]


def label(name, style="colon"):
    return f"[{name}]" if style == "brackets" else f"{name}:"


def sample_span(items, spk, longest=8.0):
    """(start, end) of the clearest stretch of one speaker for the Play button: the longest sure segment."""
    best = None
    for it in items:
        if it.get("spk") != spk:
            continue
        d = float(it["end"]) - float(it["start"])
        score = d - (100 if it.get("spk_unsure") else 0)
        if best is None or score > best[0]:
            best = (score, float(it["start"]), float(it["end"]))
    if not best:
        return None
    return best[1], min(best[2], best[1] + longest)


def rename_map(names, old, new):
    out = dict(names)
    out[old] = new
    return out


def merge(items, keep, drop):
    """Every segment of speaker `drop` becomes `keep`."""
    return [dict(it, spk=keep) if it.get("spk") == drop else it for it in items]
