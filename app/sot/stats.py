"""Statistics bar text, shared by the window, the Raw output header and the TXT/DOCX header."""
from .media import fmt_clock
from .modelmgr import LANG_NAMES


def line(st):
    """'Time 00:41:24 · Audio 00:34:14 · Tokens in 22,551 (speech 13,551, text 9,000) / out 6,728 ·
    Speed 0.8× real time, 4.4 tok/s · SenseVoiceSmall on CPU'"""
    wall = st.get("wall_s", 0.0)
    audio = st.get("audio_s", 0.0)
    sp, tx, out = st.get("speech_tokens", 0), st.get("text_tokens", 0), st.get("out_tokens", 0)
    parts = [f"Time {fmt_clock(wall)}", f"Audio {fmt_clock(audio)}",
             f"Tokens in {sp + tx:,} (speech {sp:,}, text {tx:,}) / out {out:,}"]
    speed = audio / wall if wall > 0 else 0.0
    recog = st.get("recog_s", 0.0)
    tps = out / recog if recog > 0 else 0.0
    parts.append(f"Speed {speed:.1f}× real time, {tps:.1f} tok/s")
    if st.get("model_name"):
        parts.append(f"{st['model_name']} on CPU")
    return " · ".join(parts)


def time_tooltip(st):
    rows = [("Reading audio", st.get("read_s", 0)), ("Language check", st.get("lid_s", 0)),
            ("Model loading", st.get("load_s", 0)), ("Finding speech and recognition", st.get("recog_s", 0)),
            ("Clean-up", st.get("clean_s", 0)), ("Saving", st.get("save_s", 0))]
    return "\n".join(f"{k}: {v:.1f} s" for k, v in rows)


def audio_tooltip(st):
    out = []
    if st.get("speech_s") is not None:
        out.append(f"Speech found {fmt_clock(st.get('speech_s', 0))} of {fmt_clock(st.get('duration', 0))}")
    lang = st.get("language")
    if lang:
        p = st.get("language_prob")
        name = LANG_NAMES.get(lang, lang)
        out.append(f"Language {name}" + (f" ({p:.0%} sure)" if p else "") +
                   (" - detected" if st.get("language_detected") else " - chosen"))
    if st.get("gain_db"):
        out.append(f"Volume raised by {st['gain_db']:.1f} dB")
    if st.get("threads"):
        out.append(f"{st['threads']} CPU threads")
    if st.get("stopped_at") is not None and st.get("cancelled"):
        out.append(f"Stopped at {fmt_clock(st['stopped_at'])}")
    return "\n".join(out)


def full_text(st):
    return line(st) + "\n" + time_tooltip(st) + "\n" + audio_tooltip(st)


def queue_line(q):
    speed = q["audio_s"] / q["wall_s"] if q["wall_s"] else 0
    return (f"Queue: {q['done']} of {q['files']} files done · Audio {fmt_clock(q['audio_s'])} · "
            f"Time {fmt_clock(q['wall_s'])} · Average speed {speed:.1f}× real time"
            + (f" · {q['failed']} failed" if q.get("failed") else ""))
