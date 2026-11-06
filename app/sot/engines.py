"""Recognition engines behind one interface.

Each engine: load(model) -> transcribe(audio, offset, ...) yielding segment dicts,
and adds to self.stats: speech_tokens, text_tokens, out_tokens."""
import glob
import math
import re
import os
import time
import zlib

import numpy as np

from . import audioproc

SR = 16000


def first(folder, *patterns):
    for p in patterns:
        hits = sorted(glob.glob(os.path.join(folder, p)))
        if hits:
            return hits[0]
    return None


def detect_type(folder):
    """Model type from the files in a folder. Returns (kind, files) or (None, missing)."""
    if not os.path.isdir(folder):
        return None, ["(folder not found)"]
    has = lambda n: os.path.isfile(os.path.join(folder, n))
    if has("model.bin") and has("config.json"):
        miss = [n for n in ("tokenizer.json",) if not has(n)]
        if not (has("vocabulary.txt") or has("vocabulary.json")):
            miss.append("vocabulary.txt or vocabulary.json")
        return ("whisper", {}) if not miss else (None, miss)
    tokens = has("tokens.txt")
    enc = first(folder, "encoder.int8.onnx", "encoder*.onnx")
    dec = first(folder, "decoder.int8.onnx", "decoder*.onnx")
    join = first(folder, "joiner.int8.onnx", "joiner*.onnx")
    if enc and dec and join:
        return ("parakeet", {"encoder": enc, "decoder": dec, "joiner": join,
                             "tokens": os.path.join(folder, "tokens.txt")}) if tokens else (None, ["tokens.txt"])
    pre = first(folder, "preprocess.onnx")
    if pre:
        files = {"preprocessor": pre, "encoder": first(folder, "encode.int8.onnx", "encode*.onnx"),
                 "uncached_decoder": first(folder, "uncached_decode.int8.onnx", "uncached_decode*.onnx"),
                 "cached_decoder": first(folder, "cached_decode.int8.onnx", "cached_decode*.onnx"),
                 "tokens": os.path.join(folder, "tokens.txt")}
        miss = [k for k, v in files.items() if not v or not os.path.isfile(v)]
        return ("moonshine", files) if not miss else (None, miss)
    model = first(folder, "model.int8.onnx", "model.onnx")
    if model:
        return ("sensevoice", {"model": model, "tokens": os.path.join(folder, "tokens.txt")}) if tokens \
            else (None, ["tokens.txt"])
    return None, ["Whisper: model.bin, config.json, tokenizer.json, vocabulary.txt/json",
                  "SenseVoiceSmall: model.int8.onnx, tokens.txt",
                  "Parakeet: encoder, decoder and joiner .onnx files, tokens.txt",
                  "Moonshine: preprocess.onnx, encode, uncached_decode, cached_decode .onnx, tokens.txt"]


def kind_languages(kind, folder=""):
    if kind == "whisper":
        name = os.path.basename(folder.rstrip("\\/")).lower()
        return ["en"] if (name.endswith(".en") or "distil" in name) else ["en", "fr", "yue", "zh"]
    return {"sensevoice": ["yue", "zh", "en"], "parakeet": ["en", "fr"], "moonshine": ["en"]}.get(kind, [])


class Engine:
    kind = ""
    max_piece_s = 30

    def __init__(self):
        self.folder = None
        self.stats = {}
        self.reset_stats()

    def reset_stats(self):
        self.stats = {"speech_tokens": 0, "text_tokens": 0, "out_tokens": 0, "recog_s": 0.0}

    def detect_language(self, audio, settings):
        return None, 0.0

    # "Check stretches without speech" bookkeeping, shown in Raw output
    def _gap_info(self, info):
        for k in ("stretches", "checked_s", "silent_s"):
            self.stats["gap_" + k] = self.stats.get("gap_" + k, 0) + info[k]

    def _gap_note(self, start, text, why):
        notes = self.stats.setdefault("gap_notes", [])
        if len(notes) < 50:
            notes.append({"start": start, "text": text[:120], "why": why})

    def _gap_done(self, b, e, kept):
        if kept:
            self.stats["gap_kept"] = self.stats.get("gap_kept", 0) + 1
            self.stats["gap_found_s"] = self.stats.get("gap_found_s", 0.0) + (e - b) / SR
            self.stats["speech_s"] = self.stats.get("speech_s", 0.0) + (e - b) / SR
        else:
            self.stats["gap_dropped"] = self.stats.get("gap_dropped", 0) + 1


# ======================================================================== Whisper
class WhisperEngine(Engine):
    kind = "whisper"

    def __init__(self):
        super().__init__()
        self.model = None

    def load(self, folder, threads, compute_type):
        from .avstub import install as _avstub
        _avstub()
        from faster_whisper import WhisperModel
        self.model = WhisperModel(folder, device="cpu", compute_type=compute_type, cpu_threads=threads)
        self.folder = folder

    def has_language(self, lang):
        return self.model.hf_tokenizer.token_to_id(f"<|{lang}|>") is not None

    def resolve_language(self, lang):
        if not lang:
            return None
        if not self.model.model.is_multilingual:
            return "en"
        if not self.has_language(lang):
            return "zh" if lang == "yue" else None
        return lang

    def detect_language(self, audio, settings):
        audioproc.install_windowed_vad()  # issue #1: voice detection in 30-s windows
        lang, prob, _ = self.model.detect_language(
            audio=audio, vad_filter=True, vad_parameters=audioproc.vad_options(settings))
        return lang, float(prob)

    def transcribe(self, audio, offset, language, settings, hotwords, cancel, progress):
        s = settings
        beam = int(s.get("advanced.beam")) or (1 if s.get("transcription.speed") == "fast" else 5)
        temps = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0] if s.get("advanced.temperature_fallback") else [0.0]
        word_ts = bool(s.get("advanced.word_timestamps"))
        hw = " ".join(hotwords).strip() or None
        vad = audioproc.vad_options(s)
        vad_off = bool(s.get("advanced.vad_off"))  # Whisper reads everything, silence included
        kw = dict(language=self.resolve_language(language), task="transcribe", beam_size=beam,
                  best_of=max(beam, 1), temperature=temps,
                  compression_ratio_threshold=float(s.get("advanced.compression_ratio")),
                  log_prob_threshold=float(s.get("advanced.log_prob")),
                  no_speech_threshold=float(s.get("advanced.no_speech")),
                  condition_on_previous_text=bool(s.get("advanced.carry_previous")),
                  repetition_penalty=float(s.get("advanced.repetition_penalty")),
                  word_timestamps=word_ts,
                  hallucination_silence_threshold=float(s.get("advanced.skip_silence")) if word_ts else None,
                  vad_filter=not vad_off,  # normally silence never reaches Whisper
                  vad_parameters={"threshold": vad.threshold,
                                  "min_silence_duration_ms": vad.min_silence_duration_ms,
                                  "speech_pad_ms": vad.speech_pad_ms},
                  hotwords=hw)
        t0 = time.perf_counter()
        audioproc.install_windowed_vad()  # issue #1: voice detection in 30-s windows, not over the whole block
        gaps, holes = [], []
        if vad_off:
            self.stats["vad_off"] = True
        else:
            from faster_whisper.vad import VadOptions
            ts = audioproc.windowed_speech_timestamps(audio, VadOptions(**kw["vad_parameters"]))  # reused by Whisper
            holes = [a["end"] / SR for a, b in zip(ts, ts[1:]) if b["start"] - a["end"] >= 5 * SR]
            if s.get("advanced.gap_check"):
                gaps, gi = audioproc.gap_pieces(audio, [(t["start"], t["end"]) for t in ts],
                                                float(s.get("advanced.gap_min_s")), 30.0)
                self._gap_info(gi)
        segments, info = self.model.transcribe(audio, **kw)
        seeks = set()
        prompt = 3 + (len(self.model.hf_tokenizer.encode(" " + hw).ids) + 1 if hw else 0)
        gap_kw = dict(kw, vad_filter=False, condition_on_previous_text=False,
                      language=kw["language"] or info.language)
        try:
            for seg in segments:
                while gaps and gaps[0][0] / SR <= seg.start and not cancel.is_set():
                    yield from self._gap(audio, offset, gaps.pop(0), gap_kw, info.language, progress)
                seeks.add(seg.seek)
                seg_end = seg.end
                for h in holes:  # faster-whisper can stretch a line's end across a stretch without speech
                    if seg.start < h < seg_end - 1.0:
                        seg_end = h
                        break
                text = seg.text.strip()
                self.stats["out_tokens"] += len(seg.tokens)
                progress(offset + seg.end)
                if not text:
                    continue
                echoed = False
                if hw:  # faster-whisper can echo the hotwords prompt at the end of a window
                    orig_text = text
                    text, echoed = strip_hotword_echo(text, hotwords)
                    if echoed:
                        self.stats["echo_removed"] = self.stats.get("echo_removed", 0) + 1
                        if not text:
                            continue
                flag = "check" if seg.avg_logprob < -1.0 else ""
                raw = (f"[{fmt(offset + seg.start)} - {fmt(offset + seg_end)}] logprob={seg.avg_logprob:.2f} "
                       f"no_speech={seg.no_speech_prob:.2f} compression={seg.compression_ratio:.2f} "
                       f"temperature={seg.temperature} | {text}" + (f"   (hotword echo removed from: {orig_text})" if hw and echoed else ""))
                yield {"start": offset + seg.start, "end": offset + seg_end, "text": text,
                       "lang": info.language, "flag": flag, "raw": raw}
                if cancel.is_set():
                    break
            while gaps and not cancel.is_set():
                yield from self._gap(audio, offset, gaps.pop(0), gap_kw, info.language, progress)
        finally:
            windows = max(len(seeks), math.ceil(info.duration_after_vad / 30.0)) if info.duration_after_vad else len(seeks)
            self.stats["speech_tokens"] += 1500 * windows
            self.stats["text_tokens"] += prompt * windows
            self.stats["recog_s"] += time.perf_counter() - t0
            self.stats["speech_s"] = self.stats.get("speech_s", 0.0) + float(info.duration_after_vad or 0)
            self.stats["language"] = info.language
            self.stats["language_prob"] = float(info.language_probability or 0)

    def _gap(self, audio, offset, piece, kw, lang, progress):
        """Transcribe one piece of a stretch where voice detection found no speech; keep only confident text."""
        b, e = piece
        segments, _ = self.model.transcribe(audio[b:e], **kw)
        self.stats["speech_tokens"] += 1500
        kept = False
        for seg in segments:
            self.stats["out_tokens"] += len(seg.tokens)
            text = seg.text.strip()
            start = offset + b / SR + min(seg.start, (e - b) / SR)
            end = offset + b / SR + min(seg.end, (e - b) / SR)  # Whisper pads a short piece to 30 s
            progress(end)
            if not text:
                continue
            why = ""
            if seg.no_speech_prob > 0.5:
                why = f"probably not speech (no_speech {seg.no_speech_prob:.2f})"
            elif seg.avg_logprob < -0.9:
                why = f"model unsure (logprob {seg.avg_logprob:.2f})"
            elif seg.compression_ratio > 2.4:
                why = "repeats itself"
            else:
                ok, why = keep_gap_text(text, max(seg.end - seg.start, 0.5))
            if why:
                self._gap_note(start, text, why)
                continue
            kept = True
            raw = (f"[{fmt(start)} - {fmt(end)}] (stretch without detected speech) logprob={seg.avg_logprob:.2f} "
                   f"no_speech={seg.no_speech_prob:.2f} compression={seg.compression_ratio:.2f} | {text}")
            yield {"start": start, "end": end, "text": text, "lang": lang, "flag": "", "raw": raw}
        self._gap_done(b, e, kept)

# ======================================================================== sherpa-onnx
class SherpaEngine(Engine):
    FRAME = {"sensevoice": 0.06, "parakeet": 0.08, "moonshine": 384 / 16000}
    TEXT_TOKENS = {"sensevoice": 4, "parakeet": 0, "moonshine": 0}

    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.rec = None
        self.key = None
        self.files = None
        self.threads = 1

    def load(self, folder, threads, compute_type=None, language="auto"):
        kind, files = detect_type(folder)
        if kind != self.kind:
            raise RuntimeError(f"{folder} is not a {self.kind} model folder.")
        self.folder, self.files, self.threads = folder, files, threads
        self._build(language)

    def _build(self, language):
        import sherpa_onnx
        key = (self.folder, self.threads, language if self.kind == "sensevoice" else "")
        if key == self.key:
            return
        f = self.files
        if self.kind == "sensevoice":
            lang = language if language in ("zh", "en", "yue", "ja", "ko") else ""
            self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=f["model"], tokens=f["tokens"], num_threads=self.threads, use_itn=True, language=lang)
        elif self.kind == "parakeet":
            self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
                encoder=f["encoder"], decoder=f["decoder"], joiner=f["joiner"], tokens=f["tokens"],
                num_threads=self.threads, model_type="nemo_transducer")
        else:
            self.rec = sherpa_onnx.OfflineRecognizer.from_moonshine(
                preprocessor=f["preprocessor"], encoder=f["encoder"], uncached_decoder=f["uncached_decoder"],
                cached_decoder=f["cached_decoder"], tokens=f["tokens"], num_threads=self.threads)
        self.key = key

    def _decode(self, chunk):
        st = self.rec.create_stream()
        st.accept_waveform(SR, chunk)
        self.rec.decode_stream(st)
        return st.result

    def detect_language(self, audio, settings):
        if self.kind != "sensevoice":
            return ("en", 0.5)
        self._build("auto")
        pieces = audioproc.speech_pieces(audio, settings, 15)
        pieces = [p for p in pieces if p[1] - p[0] >= 2 * SR] or pieces
        votes = {}
        used = 0
        for b, e in pieces:
            if used >= 45 * SR:
                break
            used += e - b
            r = self._decode(audio[b:e])
            lang = getattr(r, "lang", "").strip("<|>")
            if lang:
                votes[lang] = votes.get(lang, 0) + (e - b)
        if not votes:
            return None, 0.0
        best = max(votes, key=votes.get)
        return best, votes[best] / sum(votes.values())

    def transcribe(self, audio, offset, language, settings, hotwords, cancel, progress):
        self._build(language or "auto")
        max_piece = min(float(settings.get("advanced.max_piece_s")), 25.0)
        pieces = audioproc.speech_pieces(audio, settings, max_piece)
        self.stats["speech_s"] = self.stats.get("speech_s", 0.0) + sum(e - b for b, e in pieces) / SR
        gaps = []
        if settings.get("advanced.gap_check"):
            gaps, gi = audioproc.gap_pieces(audio, pieces, float(settings.get("advanced.gap_min_s")), max_piece)
            self._gap_info(gi)
        work = sorted([(b, e, False) for b, e in pieces] + [(b, e, True) for b, e in gaps])
        t0 = time.perf_counter()
        try:
            for b, e, is_gap in work:
                if cancel.is_set():
                    break
                r = self._decode(audio[b:e])
                dur = (e - b) / SR
                self.stats["speech_tokens"] += int(round(dur / self.FRAME[self.kind]))
                self.stats["text_tokens"] += self.TEXT_TOKENS[self.kind]
                toks = list(getattr(r, "tokens", []) or [])
                self.stats["out_tokens"] += len(toks)
                text = (r.text or "").strip()
                start, end = offset + b / SR, offset + e / SR
                progress(end)
                tags = ""
                lang = language or ""
                if self.kind == "sensevoice":
                    lang = getattr(r, "lang", "").strip("<|>") or lang
                    tags = f"{getattr(r, 'lang', '')}{getattr(r, 'emotion', '')}{getattr(r, 'event', '')} "
                raw = f"[{fmt(start)} - {fmt(end)}] {tags}{text}"
                if is_gap:
                    why = ""
                    if text:
                        ok, why = keep_gap_text(text, e / SR - b / SR, getattr(r, "event", ""))
                        if why:
                            self._gap_note(start, text, why)
                    self._gap_done(b, e, bool(text) and not why)
                    if why:
                        continue
                    raw = f"[{fmt(start)} - {fmt(end)}] (stretch without detected speech) {tags}{text}"
                if not text:
                    continue
                yield {"start": start, "end": end, "text": text, "lang": lang, "flag": "", "raw": raw}
        finally:
            self.stats["recog_s"] += time.perf_counter() - t0


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
NON_SPEECH_EVENTS = ("BGM", "Applause", "Laughter", "Cry", "Sneeze", "Breath", "Cough")


def keep_gap_text(text, dur, event=""):
    """Is text from a stretch without detected speech worth keeping? -> (True, "") or (False, reason).
    Models tend to invent short phrases, fast streams or repeats in noise and music."""
    cjk = len(_CJK.findall(text))
    words = len(_WORD.findall(_CJK.sub(" ", text)))
    if cjk + words < 2:
        return False, "too short to tell from noise"
    dur = max(float(dur), 0.5)
    if cjk / dur > 9 or words / dur > 5:
        return False, "too fast for speech"
    raw = text.encode("utf-8")
    if len(raw) >= 24 and len(raw) / len(zlib.compress(raw)) > 2.4:
        return False, "repeats itself"
    ev = (event or "").strip("<|>")
    if ev in NON_SPEECH_EVENTS and cjk + words < 8:
        return False, f"sounds like {ev.lower()}, not speech"
    return True, ""


def _norm_words(t):
    return re.sub(r"[^\w\s]", " ", t.lower()).split()


def strip_hotword_echo(text, hotwords):
    """Remove the hotword list when Whisper repeats it at the end of a segment (two or more entries in order)."""
    words = _norm_words(text)
    hw = _norm_words(" ".join(hotwords))
    if len(hotwords) < 2 or not hw:
        return text, False
    for start in range(len(hw)):  # the echo may begin anywhere in the list but runs to its end
        tail = hw[start:]
        if len(tail) < 2 or len(words) < len(tail) or words[-len(tail):] != tail:
            continue
        # count entries covered: must be at least two whole hotwords
        covered, n = 0, 0
        for h in reversed(hotwords):
            n += len(_norm_words(h))
            if n <= len(tail):
                covered += 1
        if covered < 2 or n < len(tail) and covered < 2:
            continue
        cut = text
        for _ in range(len(tail)):
            cut = re.sub(r"[\W_]*\w+[\W_]*$", "", cut, flags=re.UNICODE) if re.search(r"\w", cut) else cut
        return cut.rstrip(" ,;:-"), True
    return text, False


def make_engine(kind):
    return WhisperEngine() if kind == "whisper" else SherpaEngine(kind)


def fmt(t):
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    return f"{h:02d}:{m:02d}:{t % 60:06.3f}"
