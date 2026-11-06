"""Volume levelling and voice detection."""
import numpy as np

SR = 16000


def boost_quiet_audio(audio, target_dbfs=-23.0, max_gain_db=35.0):
    """Raise quiet recordings so speech sits near target_dbfs; never turn audio down.

    Level = mean RMS of the loudest half of 100 ms frames (roughly the speech)."""
    frame = SR // 10
    n = len(audio) // frame
    if n == 0:
        return audio, 0.0
    frames = audio[: n * frame].reshape(n, frame).astype(np.float64)
    rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
    speech_rms = float(np.mean(np.sort(rms)[n // 2:]))
    current = 20 * np.log10(speech_rms + 1e-12)
    gain = float(min(max(target_dbfs - current, 0.0), max_gain_db))
    if gain < 0.5:
        return audio, 0.0
    return np.clip(audio * (10 ** (gain / 20)), -1.0, 1.0).astype(np.float32), gain


def vad_options(settings, max_speech_s=None):
    from .avstub import install as _avstub
    _avstub()
    from faster_whisper.vad import VadOptions
    kw = dict(threshold=float(settings.get("advanced.vad_threshold")),
              min_silence_duration_ms=int(settings.get("advanced.vad_min_silence_ms")),
              speech_pad_ms=int(settings.get("advanced.vad_pad_ms")))
    if max_speech_s:
        kw["max_speech_duration_s"] = float(max_speech_s)
    return VadOptions(**kw)


# Voice detection runs on 30-second windows (issue #1). faster-whisper 1.2.1 runs Silero VAD over the whole
# 30-minute block in one pass, carrying the model's state across thousands of frames; on some recordings that state
# drifts and most of the speech is reported as silence (a 30-s piece alone: 30 s of speech; the same piece inside
# 10 minutes: almost none). Fresh 30-s windows, each with 1 s of the audio before it so a word on the boundary is
# heard whole, give the model a clean start every 30 s.
VAD_WINDOW_S = 30.0
VAD_OVERLAP_S = 1.0


def _silero():
    from .avstub import install as _avstub
    _avstub()
    from faster_whisper import vad
    return vad


_last = {"key": None, "out": None}  # the last result, so Whisper's own call reuses what the stretch check computed


def windowed_speech_timestamps(audio, vad_options=None, sampling_rate=16000, **kwargs):
    """Same arguments and result as faster_whisper.vad.get_speech_timestamps, computed in 30-s windows:
    [{"start": sample, "end": sample}], sorted, overlaps joined, pieces no longer than max_speech_duration_s."""
    vad = _silero()
    if vad_options is None:
        vad_options = vad.VadOptions(**kwargs)
    key = (id(audio), len(audio), sampling_rate, repr(vad_options),
           float(np.asarray(audio[::4001], dtype=np.float64).sum()) if len(audio) else 0.0)
    if _last["key"] != key:
        _last.update(key=key, out=_windowed(audio, vad, vad_options, sampling_rate))
    return [dict(t) for t in _last["out"]]


def _windowed(audio, vad, vad_options, sampling_rate):
    base = vad.get_speech_timestamps
    n = len(audio)
    win = int(VAD_WINDOW_S * sampling_rate)
    ov = int(VAD_OVERLAP_S * sampling_rate)
    if n <= win + ov:
        return base(audio, vad_options, sampling_rate)
    segs = []
    for st in range(0, n, win):
        a0, a1 = max(0, st - ov), min(n, st + win)
        if a1 - a0 < 512:  # less than one VAD frame left at the end
            continue
        for t in base(audio[a0:a1], vad_options, sampling_rate):
            segs.append([int(t["start"]) + a0, int(t["end"]) + a0])
    segs.sort()
    merged = []
    for b, e in segs:
        if merged and b <= merged[-1][1]:  # the same speech seen by two windows, or touching at a boundary
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([b, e])
    cap = vad_options.max_speech_duration_s
    cap = int(cap * sampling_rate) if cap and cap != float("inf") else None
    out = []
    for b, e in merged:
        while cap and e - b > cap:
            out.append({"start": b, "end": b + cap})
            b += cap
        out.append({"start": b, "end": e})
    return out


def install_windowed_vad():
    """Whisper's transcribe() and detect_language() call get_speech_timestamps from faster_whisper.transcribe's own
    namespace; point that name at the windowed version (faster-whisper is pinned at 1.2.1; checked by a test)."""
    _silero()
    import faster_whisper.transcribe as fwt
    if fwt.get_speech_timestamps is not windowed_speech_timestamps:
        fwt.get_speech_timestamps = windowed_speech_timestamps


def speech_pieces(audio, settings, max_piece_s):
    """Silero VAD (the model inside faster-whisper), in 30-s windows -> [(start_sample, end_sample)], joined and
    capped (SenseVoiceSmall, Parakeet and Moonshine)."""
    ts = windowed_speech_timestamps(audio, vad_options(settings, max_piece_s))
    segs = [(t["start"], t["end"]) for t in ts]
    return merge_and_split(segs, max_gap=int(0.5 * SR), max_len=int(max_piece_s * SR))


# "Check stretches without speech": where voice detection found no speech for a while, the model still listens,
# in pieces cut at the quietest moments; near-silent pieces are skipped and only confident text is kept
# (engines.keep_gap_text). A safety net for recordings where voice detection misses speech.
SILENT_DBFS = -50.0  # a piece whose loudest moments stay below this is silence: not worth the model's time


def frame_db(audio, frame=SR // 10):
    """Level of each 100-ms frame in dBFS."""
    n = len(audio) // frame
    if n == 0:
        return np.array([-120.0])
    f = audio[: n * frame].reshape(n, frame).astype(np.float64)
    return 20 * np.log10(np.sqrt(np.mean(f ** 2, axis=1)) + 1e-9)


def gap_pieces(audio, speech, min_gap_s=20.0, max_piece_s=25.0, silent_dbfs=SILENT_DBFS):
    """Stretches of at least min_gap_s with no detected speech (speech = [(start, end)] in samples), cut into pieces
    of at most max_piece_s at the quietest 100 ms. -> (pieces [(start, end)], info)."""
    n = len(audio)
    fr = SR // 10
    gaps, t = [], 0
    for b, e in sorted(speech):
        if b - t >= min_gap_s * SR:
            gaps.append((t, b))
        t = max(t, e)
    if n - t >= min_gap_s * SR:
        gaps.append((t, n))
    maxlen = int(max_piece_s * SR)
    pieces, checked, silent = [], 0, 0
    for gb, ge in gaps:
        checked += ge - gb
        b = gb
        while b < ge:
            e = min(ge, b + maxlen)
            if e < ge:  # cut at the quietest moment in the last 40 % of the piece, not inside a word
                lo = b + int(0.6 * maxlen)
                e = lo + int(np.argmin(frame_db(audio[lo:e], fr))) * fr + fr // 2
            if e - b >= SR // 2:
                if np.percentile(frame_db(audio[b:e], fr), 95) < silent_dbfs:
                    silent += e - b
                else:
                    pieces.append((b, e))
            b = e
    return pieces, {"stretches": len(gaps), "checked_s": checked / SR, "silent_s": silent / SR}


def silent_stretches(segments, duration, min_gap_s=60.0):
    """[(start_s, end_s)] of stretches of at least min_gap_s with no text (start, between segments, end)."""
    out = []
    t = 0.0
    for sg in sorted(segments, key=lambda x: float(x.get("start", 0))):
        st, en = float(sg.get("start", 0)), float(sg.get("end", 0))
        if st - t >= min_gap_s:
            out.append((t, st))
        t = max(t, en)
    if duration and duration - t >= min_gap_s:
        out.append((t, float(duration)))
    return out


def low_speech(speech_s, audio_s, min_fraction=0.40, min_audio_s=120.0):
    """The share of the audio found to be speech, when it is so small that speech may have been missed (else None)."""
    if not audio_s or audio_s < min_audio_s or speech_s is None:
        return None
    frac = float(speech_s) / float(audio_s)
    return frac if frac < min_fraction else None


def merge_and_split(segs, max_gap, max_len):
    merged = []
    for b, e in segs:
        if merged and b - merged[-1][1] <= max_gap and e - merged[-1][0] <= max_len:
            merged[-1][1] = e
        else:
            merged.append([b, e])
    out = []
    for b, e in merged:
        while e - b > max_len:
            out.append((b, b + max_len))
            b += max_len
        if e - b >= SR // 5:  # drop slivers under 0.2 s
            out.append((b, e))
    return out
