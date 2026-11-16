"""AI summary of a finished transcript (version 2.3.0).

The summary is written by a small language model on this computer (sot.llm, llama.cpp), after the transcript is
complete. A meeting that does not fit in the model's context is read in parts: notes are written for each part,
then the summary is written from the notes. Nothing leaves the computer."""
import re
import time

from . import media, spelling

# chat formats, read from each model's own chat template with "thinking" switched off (enable_thinking=false)
FAMILIES = {
    "qwen3": {"label": "Qwen3", "start": "<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
                                         "<|im_start|>assistant\n<think>\n\n</think>\n\n",
              "top_k": 20, "top_p": 0.8, "min_p": 0.0},
    "qwen3-instruct": {"label": "Qwen3 Instruct",
                       "start": "<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
                                "<|im_start|>assistant\n", "top_k": 20, "top_p": 0.8, "min_p": 0.0},
    "qwen3.5": {"label": "Qwen3.5", "start": "<|im_start|>system\n{system}<|im_end|>\n<|im_start|>user\n{user}<|im_end|>\n"
                                             "<|im_start|>assistant\n<think>\n\n</think>\n\n",
                "top_k": 20, "top_p": 0.8, "min_p": 0.0},
    "gemma4": {"label": "Gemma 4", "start": "<|turn>system\n{system}<turn|>\n<|turn>user\n{user}<turn|>\n<|turn>model\n",
               "top_k": 64, "top_p": 0.95, "min_p": 0.0},  # <bos> is added by the tokenizer (add_bos_token)
}

OUTPUT_LANGS = {"en": "English", "fr": "French", "zh-hant": "Traditional Chinese", "zh-hans": "Simplified Chinese"}

HEADINGS = {
    "en": {"overview": "Summary", "key_points": "Key points", "decisions": "Decisions", "actions": "Action items",
           "questions": "Open questions", "none": "None mentioned."},
    "fr": {"overview": "Résumé", "key_points": "Points clés", "decisions": "Décisions", "actions": "Actions à mener",
           "questions": "Questions ouvertes", "none": "Aucun(e) mentionné(e)."},
    "zh-hant": {"overview": "摘要", "key_points": "重點", "decisions": "決定", "actions": "待辦事項",
                "questions": "未解決的問題", "none": "沒有提及。"},
    "zh-hans": {"overview": "摘要", "key_points": "要点", "decisions": "决定", "actions": "待办事项",
                "questions": "未解决的问题", "none": "没有提及。"},
}

SECTIONS = ("overview", "key_points", "decisions", "actions", "questions")
SECTION_HELP = {
    "overview": "{len_overview}",
    "key_points": "bullet points with the main topics, facts and figures ({len_bullets}){each}",
    "decisions": "bullet points with what was decided{each}; if nothing was decided, write only \"{none}\"",
    "actions": "bullet points as \"Who: what to do (by when, if a date is given)\"{each}; if no task was given, "
               "write only \"{none}\"",
    "questions": "bullet points with questions or issues left open{each}; if there are none, write only \"{none}\"",
}
LENGTHS = {
    "brief": {"overview": "two or three sentences", "bullets": "at most 5 bullet points", "notes": 6, "tokens": 500},
    "standard": {"overview": "one paragraph of four to six sentences", "bullets": "at most 8 bullet points",
                 "notes": 10, "tokens": 900},
    "detailed": {"overview": "two paragraphs", "bullets": "up to 15 bullet points, with the details that matter",
                 "notes": 14, "tokens": 1600},
}

TIME_RE = re.compile(r"\[(\d{1,2}):(\d{2}):(\d{2})\]")
NOTES_TOKENS = 700      # most a part's notes may take
TEMPLATE_RESERVE = 600  # instructions and chat format, in tokens (measured ~250-450 with custom instructions)


def output_language(choice, transcript_lang, script_yue="hk", script_zh="cn"):
    """Code of the summary's language: 'same' follows the transcript (Cantonese becomes written Chinese)."""
    if choice in OUTPUT_LANGS:
        return choice
    if transcript_lang == "fr":
        return "fr"
    if transcript_lang == "yue":
        return "zh-hans" if script_yue == "cn" else "zh-hant"
    if transcript_lang == "zh":
        return "zh-hant" if script_zh in ("hk", "tw") else "zh-hans"
    return "en"


def script_for_output(code, script_yue="hk", script_zh="cn"):
    """OpenCC target for a Chinese summary (keeps the summary in the same script as the transcript)."""
    if code == "zh-hans":
        return "cn"
    if code == "zh-hant":
        return script_yue if script_yue in ("hk", "tw") else "hk"
    return None


def transcript_lines(blocks):
    """'[hh:mm:ss] text', or (2.4.0) '[hh:mm:ss] Name: text' when the block has a speaker's name."""
    out = []
    for b in blocks:
        t = (b.get("text") or "").strip()
        if t:
            who = f"{b['speaker']}: " if b.get("speaker") else ""
            out.append(f"[{media.fmt_clock(float(b.get('start') or 0))}] {who}{t}")
    return out


def system_prompt(lang_code, spelling_variant=None):
    name = OUTPUT_LANGS[lang_code]
    extra = " Use written Chinese, not colloquial Cantonese." if lang_code.startswith("zh") else ""
    if lang_code == "en" and spelling_variant in spelling.NAMES:
        extra = (f" Use {spelling.NAMES[spelling_variant]} English spelling (for example: "
                 f"{spelling.EXAMPLES[spelling_variant]}).")
    return ("You write meeting summaries from transcripts made by speech recognition on the user's computer. "
            "Use only what the transcript says. Never invent names, numbers, dates, decisions or tasks. "
            "The transcript can contain recognition errors: when a word is clearly misheard, use the most likely "
            "meaning; when you are unsure, keep the wording of the transcript. "
            f"Write in {name}.{extra}")


def section_instructions(opts):
    h = HEADINGS[opts["lang"]]
    ln = LENGTHS.get(opts.get("length"), LENGTHS["standard"])
    lines = []
    for sec in [s for s in SECTIONS if s in (opts.get("sections") or SECTIONS)]:
        each = ", each ending with its time [hh:mm:ss]" if opts.get("times") else ""
        what = SECTION_HELP[sec].format(len_overview=ln["overview"], len_bullets=ln["bullets"], none=h["none"],
                                        each=each)
        lines.append(f"## {h[sec]}\n({what})")
    text = ("Write the summary in Markdown with exactly these headings, in this order:\n\n" + "\n\n".join(lines))
    if opts.get("times"):
        text += ("\n\nEnd every bullet point with the time where it is discussed, copied from the transcript, "
                 "in the form [hh:mm:ss].")
    else:
        text += "\n\nDo not add times."
    if opts.get("speakers"):
        text += ("\n\nTranscript lines start with the speaker's name. Use these names to say who said, decided or "
                 "will do something. A name such as \"Speaker 2\" means a person whose name is not known: keep it as "
                 "it is and do not guess a real name.")
    if (opts.get("instructions") or "").strip():
        text += "\n\nFurther instructions from the user:\n" + opts["instructions"].strip()
    text += "\n\nWrite only the summary, with no introduction."
    return text


def build_prompt(family, system, user):
    f = FAMILIES[family]
    return f["start"].format(system=system, user=user)


def single_prompt(opts, lines):
    head = f"Transcript of {opts.get('file') or 'a meeting'} ({opts.get('duration_text', '')})"
    if opts.get("transcript_lang_name"):
        head += f", spoken in {opts['transcript_lang_name']}"
    user = head + ":\n\n" + "\n".join(lines) + "\n\n---\n" + section_instructions(opts)
    return build_prompt(opts["family"], system_prompt(opts["lang"], opts.get("spelling")), user)


def part_prompt(opts, lines, i, n):
    ln = LENGTHS.get(opts.get("length"), LENGTHS["standard"])
    first = TIME_RE.search(lines[0]).group(0) if lines and TIME_RE.search(lines[0]) else ""
    last = TIME_RE.search(lines[-1]).group(0) if lines and TIME_RE.search(lines[-1]) else ""
    user = (f"This is part {i} of {n} of the transcript of {opts.get('file') or 'a meeting'} ({first} to {last}):\n\n"
            + "\n".join(lines) + "\n\n---\n"
            f"Write notes on this part in {OUTPUT_LANGS[opts['lang']]}: the topics discussed, facts and figures, "
            f"decisions, tasks (who, what, by when) and open questions. Use at most {ln['notes']} short bullet points, "
            "each ending with its time [hh:mm:ss] from the transcript. Write only the notes. "
            "Do not add anything that is not in this part.")
    return build_prompt(opts["family"], system_prompt(opts["lang"], opts.get("spelling")), user)


def reduce_prompt(opts, notes):
    user = (f"Below are notes on consecutive parts of one meeting ({opts.get('file') or 'a meeting'}, "
            f"{opts.get('duration_text', '')}). Each note ends with the time it refers to.\n\n" + "\n\n".join(notes)
            + "\n\n---\nWrite the summary of the whole meeting from these notes.\n" + section_instructions(opts))
    return build_prompt(opts["family"], system_prompt(opts["lang"], opts.get("spelling")), user)


def split_parts(lines, counts, budget):
    """Greedy split of transcript lines into parts that fit the token budget (a very long line is cut)."""
    parts, cur, size = [], [], 0
    for line, n in zip(lines, counts):
        if n > budget:  # one paragraph larger than a whole part: cut it by characters
            step = max(200, int(len(line) * budget / n * 0.9))
            pieces = [line[k:k + step] for k in range(0, len(line), step)]
            for p in pieces:
                if cur:
                    parts.append(cur)
                parts.append([p])
                cur, size = [], 0
            continue
        if size + n > budget and cur:
            parts.append(cur)
            cur, size = [], 0
        cur.append(line)
        size += n
    if cur:
        parts.append(cur)
    return parts


ANY_TIME_RE = re.compile(r"[\[(（【]\s*(\d{1,2}:\d{2}:\d{2})\s*[\])）】]")
NONE_LINES = {v["none"].strip(" .。") for v in HEADINGS.values()} | {"None", "None mentioned", "Aucun", "無", "无"}


def clean_output(text):
    """Model markers out; times in any bracket style become [hh:mm:ss]; repeated bullets and a stray
    'None mentioned' under a heading that has points are dropped."""
    t = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    t = re.sub(r"<\|channel>.*?<channel\|>", "", t, flags=re.S)
    for tag in ("<|im_end|>", "<turn|>", "<|turn>", "<end_of_turn>", "<think>", "</think>"):
        t = t.replace(tag, "")
    t = t.strip()
    if t.startswith("```") and t.endswith("```"):
        t = re.sub(r"^```[a-z]*\s*", "", t).rstrip("`").strip()
    t = ANY_TIME_RE.sub(lambda m: "[" + _pad_time(m.group(1)) + "]", t)
    out, seen, section = [], set(), []
    for line in t.splitlines() + ["## "]:
        if line.startswith("#"):
            has_points = any(x.lstrip().startswith(("-", "*", "•")) for x in section)
            for x in section:
                if has_points and x.strip(" -*•.。").strip() in NONE_LINES:
                    continue
                out.append(x)
            out.append(line)
            section, seen = [], set()
            continue
        key = re.sub(r"\s+", " ", line.strip(" -*•")).strip()
        if line.lstrip().startswith(("-", "*", "•")) and key:
            if key in seen:
                continue
            seen.add(key)
        section.append(line.rstrip())
    return "\n".join(out[:-1]).strip()


def _pad_time(t):
    h, m, s = t.split(":")
    return f"{int(h):02d}:{m}:{s}"


def run(model, blocks, opts, emit, cancel):
    """Write the summary. emit(kind, *args): ("stage", text), ("progress", fraction), ("text", new_text),
    ("reset_text",). Returns (summary text, statistics)."""
    t0 = time.perf_counter()
    lines = transcript_lines(blocks)
    if not lines:
        raise ValueError("The transcript is empty.")
    fam = FAMILIES[opts["family"]]
    ln = LENGTHS.get(opts.get("length"), LENGTHS["standard"])
    max_out = int(opts.get("max_tokens") or ln["tokens"])
    gen = {"temperature": float(opts.get("temperature", 0.3)), "top_k": fam["top_k"], "top_p": fam["top_p"],
           "min_p": fam["min_p"], "repeat_penalty": 1.05}
    stats = {"prompt_tokens": 0, "output_tokens": 0, "prompt_s": 0.0, "gen_s": 0.0, "parts": 1}
    reserve = TEMPLATE_RESERVE + model.count(opts.get("instructions") or "")
    full = single_prompt(opts, lines)
    n_full = model.count(full)
    budget_single = model.n_ctx - max_out - 64
    if n_full <= budget_single:
        emit("stage", "Reading the transcript")
        text, st = model.generate(full, max_tokens=max_out, on_prompt=lambda d, t: emit("progress", 0.85 * d / t),
                                  on_text=lambda s: emit("text", s), **gen)
        _add(stats, st)
    else:
        part_budget = max(512, model.n_ctx - NOTES_TOKENS - reserve - 64)
        emit("stage", "Measuring the transcript")
        counts = [model.count(x) + 1 for x in lines]
        parts = split_parts(lines, counts, part_budget)
        n = len(parts)
        stats["parts"] = n
        notes = []
        for i, part in enumerate(parts, 1):
            emit("stage", f"Reading part {i} of {n}")
            base = (i - 1) / (n + 1)
            note, st = model.generate(part_prompt(opts, part, i, n), max_tokens=NOTES_TOKENS,
                                      on_prompt=lambda d, t, b=base: emit("progress", b + 0.8 / (n + 1) * d / t), **gen)
            _add(stats, st)
            notes.append(f"Part {i} of {n}:\n" + clean_output(note))
        # notes too long for one pass: shorten them in groups first
        while model.count(reduce_prompt(opts, notes)) > budget_single and len(notes) > 1:
            emit("stage", "Combining the notes")
            groups, cur, size = [], [], 0
            for nt in notes:
                c = model.count(nt)
                if size + c > part_budget and cur:
                    groups.append(cur)
                    cur, size = [], 0
                cur.append(nt)
                size += c
            if cur:
                groups.append(cur)
            if len(groups) == len(notes):  # each note alone is too long: keep the first lines of each
                notes = ["\n".join(nt.splitlines()[:max(3, len(nt.splitlines()) // 2)]) for nt in notes]
                continue
            new = []
            for g in groups:
                t, st = model.generate(reduce_prompt(dict(opts, sections=["key_points"], length="standard"), g),
                                       max_tokens=NOTES_TOKENS, **gen)
                _add(stats, st)
                new.append(clean_output(t))
            notes = new
        emit("stage", "Writing the summary")
        text, st = model.generate(reduce_prompt(opts, notes), max_tokens=max_out,
                                  on_prompt=lambda d, t: emit("progress", n / (n + 1) + 0.6 / (n + 1) * d / t),
                                  on_text=lambda s: emit("text", s), **gen)
        _add(stats, st)
    text = clean_output(text)
    stats["seconds"] = time.perf_counter() - t0
    stats["truncated"] = bool(st.get("stopped"))
    emit("progress", 1.0)
    return text, stats


def _add(stats, st):
    for k in ("prompt_tokens", "output_tokens", "prompt_s", "gen_s"):
        stats[k] += st.get(k, 0)


def times_in(text):
    """Seconds of every [hh:mm:ss] in the summary (to make them clickable)."""
    return [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in TIME_RE.findall(text)]


def stats_line(st, model_name=""):
    if not st:
        return ""
    sec = st.get("seconds", 0)
    gen = st.get("gen_s") or 0
    rate = f", {st['output_tokens'] / gen:.1f} tokens/s" if gen > 0 and st.get("output_tokens") else ""
    parts = f" · read in {st['parts']} parts" if st.get("parts", 1) > 1 else ""
    took = f"{int(sec // 60)} min {int(sec % 60)} s" if sec >= 60 else f"{sec:.0f} s"
    where = {"GPU": " · on the graphics chip", "CPU": " · on the processor"}.get(st.get("device"), "")
    return (f"{model_name} · {took}{parts} · {st.get('prompt_tokens', 0):,} tokens read, "
            f"{st.get('output_tokens', 0):,} written{rate}{where}").strip(" ·")


def to_plain(md_text):
    """Markdown to plain text (headings without #, bullets as '- ', no ** markers)."""
    lines = []
    for line in (md_text or "").splitlines():
        s = re.sub(r"^#{1,6}\s*", "", line.rstrip())
        s = re.sub(r"^\s*[*•]\s+", "- ", s)
        lines.append(s.replace("**", ""))
    return "\n".join(lines).strip()
