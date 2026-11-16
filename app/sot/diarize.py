"""Speaker identification in the worker process (version 2.4.0).

sherpa-onnx (already used for SenseVoiceSmall, Parakeet and Moonshine) does the work on the CPU:
  OfflineSpeakerDiarization  pyannote segmentation 3.0 finds where voices speak, the voice model (3D-Speaker
                             CAM++) turns each stretch into a voiceprint, and the stretches are grouped into speakers
  SpeakerEmbeddingExtractor  one averaged voiceprint per speaker, from its turns of 1.5 s or more
Recordings up to an hour are processed in one pass; longer ones in 30-minute blocks whose speakers are then linked
by voiceprint (sot.speakers.link_blocks). A two-channel recording (left = microphone, right = computer audio) has
only its right channel grouped into speakers; the left channel is "me".
Note: sherpa-onnx 2.0.1 reports progress through its callback but does not stop when the callback asks it to, so
Stop ends the worker process."""
import os
import time

import numpy as np

from . import media, speakers

RATE = media.SAMPLE_RATE
ONE_PASS_S = 3600.0
BLOCK_S = 1800.0
MAX_TURNS_PER_SPEAKER = 40   # voiceprint from up to 40 of a speaker's longest turns
MAX_TURN_S = 10.0            # each cut to 10 s


def model_path(path):
    """sherpa-onnx opens model files by a narrow path, which on Windows cannot hold letters outside the system's
    code page (for example Chinese folder names on an English Windows). Such a path is given in its short 8.3 form;
    if the drive keeps no short names, the error says what to do."""
    if os.name != "nt" or path.isascii():
        return path
    import ctypes
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(path, buf, 1024)
    short = buf.value if 0 < n < 1024 else ""
    if short and short.isascii():
        return short
    raise RuntimeError("The speaker models are in a folder whose name has letters this library cannot open "
                       f"({path}). Move the models folder to a path with English letters only (Options > "
                       "Transcription > Download folder > Change).")


def _config(job, num_clusters):
    import sherpa_onnx as so
    t = int(job.get("threads") or 0) or 4
    cfg = so.OfflineSpeakerDiarizationConfig(
        segmentation=so.OfflineSpeakerSegmentationModelConfig(
            pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=model_path(job["seg_model"])),
            num_threads=t),
        embedding=so.SpeakerEmbeddingExtractorConfig(model=model_path(job["emb_model"]), num_threads=t),
        clustering=so.FastClusteringConfig(num_clusters=num_clusters, threshold=float(job.get("threshold") or 0.5)),
        min_duration_on=0.3, min_duration_off=0.5)
    if not cfg.validate():
        raise RuntimeError("The speaker models could not be loaded. Download them again in Options > Speakers.")
    return cfg


def _extractor(job):
    import sherpa_onnx as so
    t = int(job.get("threads") or 0) or 4
    return so.SpeakerEmbeddingExtractor(so.SpeakerEmbeddingExtractorConfig(model=model_path(job["emb_model"]),
                                                                           num_threads=t))


def voiceprint(ex, audio, spans):
    """Averaged voiceprint of the stretches (start, end in samples of `audio`) that are long enough."""
    spans = sorted(spans, key=lambda s: -(s[1] - s[0]))[:MAX_TURNS_PER_SPEAKER]
    vecs, weights = [], []
    for a, b in spans:
        if (b - a) / RATE < speakers.MIN_EMBED_S:
            continue
        b = min(b, a + int(MAX_TURN_S * RATE))
        st = ex.create_stream()
        st.accept_waveform(RATE, audio[a:b])
        st.input_finished()
        if not ex.is_ready(st):
            continue
        vecs.append(list(np.asarray(ex.compute(st), dtype=float)))
        weights.append((b - a) / RATE)
    return speakers.centroid(vecs, weights), sum(weights)


def _level_db(x):
    if x.size == 0:
        return None
    r = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return float(20 * np.log10(r)) if r > 1e-6 else -120.0


def run(job, emit, cancel):
    """job: path, track, duration, seg_model, emb_model, threads, num_speakers (0 = automatic), threshold,
    split (two channels), items [[start, end] of each transcript segment]. Returns the result dict."""
    import sherpa_onnx as so
    t0 = time.perf_counter()
    total = float(job["duration"]) or 0.0
    split = bool(job.get("split"))
    n_req = int(job.get("num_speakers") or 0)
    if split and n_req > 0:
        n_req = max(1, n_req - 1)  # the number given counts everyone; the right channel holds the others
    emit("stage", "Reading audio for speaker identification")
    side = "right" if split else "average"
    blocks = [(0.0, None)] if total <= ONE_PASS_S else \
        [(s, min(BLOCK_S, total - s)) for s in np.arange(0.0, total, BLOCK_S)]
    clusters, turns_all = [], []
    ex = _extractor(job)
    emit("stage", "Identifying speakers")
    for bi, (start, dur) in enumerate(blocks):
        if cancel.is_set():
            return {"cancelled": True}
        audio = media.decode(job["path"], job.get("track", 0), float(start), dur, side, "off")
        sd = so.OfflineSpeakerDiarization(_config(job, n_req if len(blocks) == 1 and n_req > 0 else -1))

        def progress(done, n, bi=bi):
            frac = (bi + done / max(n, 1)) / len(blocks)
            emit("progress", frac * total * 0.9, total, {})
            return 0
        res = sd.process(audio, callback=progress).sort_by_start_time()
        per = {}
        for r in res:
            a, b = int(r.start * RATE), int(r.end * RATE)
            per.setdefault(r.speaker, []).append((a, b))
            turns_all.append({"start": float(start) + r.start, "end": float(start) + r.end,
                              "speaker": f"b{bi}:{r.speaker}"})
        for spk, spans in per.items():
            if cancel.is_set():
                return {"cancelled": True}
            vec, secs = voiceprint(ex, audio, spans)
            clusters.append({"id": f"b{bi}:{spk}", "block": bi, "centroid": vec,
                             "speech_s": sum((b - a) for a, b in spans) / RATE, "voiced_s": secs})
        del audio
    # one name per speaker across blocks
    if len(blocks) > 1:
        groups = speakers.link_blocks(clusters, n_req)
    else:
        groups = {c["id"]: i for i, c in enumerate(sorted(clusters, key=lambda c: c["id"]))}
    turns = [dict(t, speaker=f"S{groups[t['speaker']] + 1}") for t in turns_all if t["speaker"] in groups]
    spk = {}
    for c in clusters:
        sid = f"S{groups[c['id']] + 1}"
        d = spk.setdefault(sid, {"vecs": [], "w": [], "speech_s": 0.0})
        if c["centroid"]:
            d["vecs"].append(c["centroid"])
            d["w"].append(max(c["voiced_s"], 0.1))
        d["speech_s"] += c["speech_s"]
    result = {"turns": turns, "split": split, "speakers": {}}
    for sid, d in spk.items():
        result["speakers"][sid] = {"centroid": speakers.centroid(d["vecs"], d["w"]), "speech_s": round(d["speech_s"], 1)}
    if split:  # left channel = me; its loudness against the right decides each segment
        emit("stage", "Comparing the two channels")
        diffs, me_spans = [], []
        left_all = None
        for bi, (start, dur) in enumerate(blocks):
            left = media.decode(job["path"], job.get("track", 0), float(start), dur, "left", "off")
            right = media.decode(job["path"], job.get("track", 0), float(start), dur, "right", "off")
            end = float(start) + (dur if dur else total)
            for s, e in job.get("items") or []:
                if not (start <= s < end):
                    continue
                a, b = int((s - start) * RATE), int((e - start) * RATE)
                ll, rl = _level_db(left[a:b]), _level_db(right[a:b])
                d = None if ll is None or rl is None or max(ll, rl) < -60 else ll - rl
                diffs.append((s, d))
                if d is not None and d >= speakers.CHANNEL_DB:
                    me_spans.append((a, b, bi))
            if len(blocks) == 1:
                left_all = left
            del right
        diffs.sort(key=lambda x: x[0])
        result["diffs"] = [d for _, d in diffs]
        if left_all is None:  # long file: voiceprint of "me" from the first block that has my speech
            first = next((bi for _, _, bi in me_spans), None)
            if first is not None:
                st, du = blocks[first]
                left_all = media.decode(job["path"], job.get("track", 0), float(st), du, "left", "off")
                me_spans = [(a, b, bi) for a, b, bi in me_spans if bi == first]
        if left_all is not None and me_spans:
            vec, secs = voiceprint(ex, left_all, [(a, b) for a, b, _ in me_spans])
            result["speakers"][speakers.ME] = {"centroid": vec,
                                               "speech_s": round(sum(b - a for a, b, _ in me_spans) / RATE, 1)}
    emit("progress", total, total, {})
    result["stats"] = {"speaker_s": round(time.perf_counter() - t0, 1), "blocks": len(blocks),
                       "audio_s": total}
    return result
