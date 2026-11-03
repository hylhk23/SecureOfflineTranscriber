"""Export formats (section 9). All text is UTF-8 (CSV with BOM) and CRLF line endings (FR-OUT-01)."""
import csv
import datetime
import io
import json
import math
import os
import re

from .postproc import has_cjk

FORMATS = {
    "txt_times": ("Text with times", ".txt"),
    "txt_plain": ("Plain text", "_plain.txt"),
    "srt": ("Subtitles (SRT)", ".srt"),
    "vtt": ("WebVTT", ".vtt"),
    "docx": ("Word", ".docx"),
    "md": ("Markdown", ".md"),
    "json": ("JSON", ".json"),
    "csv": ("CSV", ".csv"),
}


def fmt_time(t, style="hh:mm:ss"):
    t = max(0.0, float(t))
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    if style == "mm:ss":
        return f"{h * 60 + m:02d}:{int(s):02d}"
    if style == "hh:mm:ss.mmm":
        return f"{h:02d}:{m:02d}:{s:06.3f}"
    return f"{h:02d}:{m:02d}:{int(s):02d}"


def srt_time(t, sep=","):
    t = max(0.0, float(t))
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def safe_name(s):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", s).strip(" .") or "transcript"


def base_name(meta, pattern):
    now = datetime.datetime.now()
    name = pattern
    for k, v in {"{name}": meta.get("stem", "transcript"), "{date}": now.strftime("%Y-%m-%d"),
                 "{time}": now.strftime("%H%M"), "{model}": meta.get("model_short", "model"),
                 "{lang}": meta.get("language", "")}.items():
        name = name.replace(k, v)
    return safe_name(name)


def unique(path, policy):
    """'number' adds (2), (3)...; 'overwrite' keeps path; 'ask' is handled by the window."""
    if policy == "overwrite" or not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    if root.endswith("_plain") and ext == ".txt":
        pass
    n = 2
    while os.path.exists(f"{root} ({n}){ext}"):
        n += 1
    return f"{root} ({n}){ext}"


def target_path(folder, base, fmt):
    return os.path.join(folder, base + FORMATS[fmt][1])


def _write_text(path, text, encoding):
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(path, "w", encoding=encoding, newline="") as f:
        f.write(text)


def header_lines(meta, with_stats):
    lines = [f"File: {meta.get('file', '')}", f"Date: {meta.get('date', '')}",
             f"Length: {fmt_time(meta.get('duration', 0))}", f"Language: {meta.get('language_name', '')}",
             f"Model: {meta.get('model', '')}"]
    if with_stats and meta.get("stats_line"):
        lines.append(f"Statistics: {meta['stats_line']}")
    return lines


# ------------------------------------------------------------------ cues for subtitles
def split_sentences(text):
    parts = re.split(r"(?<=[.!?。！？;；,，])\s*", text)
    return [p for p in parts if p.strip()]


def wrap_lines(text, width):
    if has_cjk(text) and " " not in text:
        return [text[i:i + width] for i in range(0, len(text), width)]
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def build_cues(blocks, chars=42, lines=2, max_dur=7.0, min_dur=1.0):
    """Split each block at punctuation into cues of at most `lines` x `chars` and `max_dur`
    seconds; times shared out by character count."""
    cues = []
    limit = chars * lines
    for b in blocks:
        text = b["text"].strip()
        if not text:
            continue
        dur = max(0.01, b["end"] - b["start"])
        pieces, cur = [], ""
        for sent in split_sentences(text) or [text]:
            joiner = "" if has_cjk(sent) else " "
            if cur and len(cur) + len(sent) + 1 > limit:
                pieces.append(cur)
                cur = sent
            else:
                cur = (cur + joiner + sent).strip() if cur else sent
            while len(cur) > limit:
                cut = cur.rfind(" ", 0, limit) if not has_cjk(cur) else limit
                cut = cut if cut > 0 else limit
                pieces.append(cur[:cut].strip())
                cur = cur[cut:].strip()
        if cur:
            pieces.append(cur)
        # also respect max duration: split pieces further if the block is long
        total = sum(len(p) for p in pieces) or 1
        t = b["start"]
        for p in pieces:
            pdur = dur * len(p) / total
            n = max(1, math.ceil(pdur / max_dur - 1e-6))
            sub = [p] if n == 1 else _split_even(p, n)
            for sp in sub:
                sd = pdur * len(sp) / max(1, len(p))
                end = min(b["end"], t + max(sd, min_dur))
                cues.append({"start": t, "end": max(end, t + 0.3), "lines": wrap_lines(sp, chars)[:lines + 1]})
                t += sd
    for i in range(len(cues) - 1):  # no overlaps
        if cues[i]["end"] > cues[i + 1]["start"]:
            cues[i]["end"] = max(cues[i]["start"] + 0.2, cues[i + 1]["start"] - 0.05)
    return cues


def _split_even(text, n):
    if has_cjk(text) and " " not in text:
        k = max(1, len(text) // n)
        return [text[i:i + k] for i in range(0, len(text), k)]
    words = text.split()
    k = max(1, len(words) // n)
    return [" ".join(words[i:i + k]) for i in range(0, len(words), k)]


# ------------------------------------------------------------------ writers
def write(fmt, path, blocks, meta, settings):
    enc = settings.get("output.encoding")
    style = settings.get("output.time_format")
    times = settings.get("output.times")
    if fmt == "txt_times":
        out = header_lines(meta, settings.get("output.stats_header")) + [""]
        for b in blocks:
            out.append(f"[{fmt_time(b['start'], style)}] {b['text']}" if times != "none" else b["text"])
            out.append("")
        _write_text(path, "\n".join(out), enc)
    elif fmt == "txt_plain":
        _write_text(path, "\n\n".join(b["text"] for b in blocks) + "\n", enc)
    elif fmt in ("srt", "vtt"):
        cues = build_cues(blocks, int(settings.get("output.sub_chars")), int(settings.get("output.sub_lines")),
                          float(settings.get("output.sub_max")), float(settings.get("output.sub_min")))
        out = ["WEBVTT", ""] if fmt == "vtt" else []
        for i, c in enumerate(cues, 1):
            sep = "." if fmt == "vtt" else ","
            if fmt == "srt":
                out.append(str(i))
            out.append(f"{srt_time(c['start'], sep)} --> {srt_time(c['end'], sep)}")
            out.extend(c["lines"])
            out.append("")
        _write_text(path, "\n".join(out), "utf-8")
    elif fmt == "md":
        out = [f"# Transcript: {meta.get('file', '')}", ""]
        out += [f"- {line}" for line in header_lines(meta, settings.get("output.stats_header"))[1:]] + [""]
        for b in blocks:
            out.append(f"**[{fmt_time(b['start'], style)}]** {b['text']}" if times != "none" else b["text"])
            out.append("")
        _write_text(path, "\n".join(out), "utf-8")
    elif fmt == "json":
        data = {"app": "Secure Offline Transcriber", "version": meta.get("version"), "file": meta.get("file"),
                "media_path": meta.get("media_path"), "duration": meta.get("duration"),
                "language": meta.get("language"), "model": meta.get("model"), "statistics": meta.get("stats", {}),
                "segments": [{"start": round(b["start"], 3), "end": round(b["end"], 3), "text": b["text"],
                              "language": b.get("lang", meta.get("language")), "check": b.get("flag") == "check"}
                             for b in blocks]}
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    elif fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(["index", "start", "end", "duration", "text"])
        for i, b in enumerate(blocks, 1):
            w.writerow([i, fmt_time(b["start"], "hh:mm:ss.mmm"), fmt_time(b["end"], "hh:mm:ss.mmm"),
                        f"{b['end'] - b['start']:.2f}", b["text"]])
        with open(path, "w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel shows Chinese
            f.write(buf.getvalue())
    elif fmt == "docx":
        write_docx(path, blocks, meta, settings)
    else:
        raise ValueError(fmt)
    return path


def write_docx(path, blocks, meta, settings):
    from docx import Document
    from docx.shared import Inches, Pt
    doc = Document()
    sec = doc.sections[0]
    if settings.get("output.page_size") == "A4":
        sec.page_width, sec.page_height = Inches(8.27), Inches(11.69)
    else:
        sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    doc.add_heading(f"Transcript: {meta.get('file', '')}", level=1)
    rows = [("File", meta.get("file", "")), ("Date", meta.get("date", "")),
            ("Length", fmt_time(meta.get("duration", 0))), ("Language", meta.get("language_name", "")),
            ("Model", meta.get("model", ""))]
    if settings.get("output.stats_header") and meta.get("stats_line"):
        rows.append(("Statistics", meta["stats_line"]))
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    for k, v in rows:
        c = t.add_row().cells
        c[0].text, c[1].text = k, str(v)
    doc.add_paragraph()
    style = settings.get("output.time_format")
    for b in blocks:
        p = doc.add_paragraph()
        if settings.get("output.times") != "none":
            r = p.add_run(f"[{fmt_time(b['start'], style)}] ")
            r.bold = True
        p.add_run(b["text"])
        p.paragraph_format.space_after = Pt(6)
    doc.save(path)
