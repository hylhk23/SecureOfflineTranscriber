"""Volume levelling and voice detection (spec section 4, steps 4-5; section 10)."""
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


def speech_pieces(audio, settings, max_piece_s):
    """Silero VAD (the model inside faster-whisper) -> [(start_sample, end_sample)], joined and capped."""
    from .avstub import install as _avstub
    _avstub()
    from faster_whisper.vad import get_speech_timestamps
    ts = get_speech_timestamps(audio, vad_options(settings, max_piece_s))
    segs = [(t["start"], t["end"]) for t in ts]
    return merge_and_split(segs, max_gap=int(0.5 * SR), max_len=int(max_piece_s * SR))


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
