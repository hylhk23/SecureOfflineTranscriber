"""Recognition runs in a separate worker process so the window never freezes.

The worker stays alive between files so the loaded model is kept.
Main -> worker: ("job", dict) / ("quit",).   Cancel: a shared Event.
Worker -> main: ("stage", text), ("progress", done_s, total_s), ("model", info), ("segment", seg),
                ("raw", line), ("block_gain", db), ("done", stats), ("error", message)."""
import logging
import multiprocessing as mp
import os
import queue
import time
import traceback

BLOCK_S = 1800.0  # long files are decoded and transcribed in 30-minute blocks
LANGS = ("en", "fr", "yue", "zh")


def worker_main(cmd_q, evt_q, cancel, data_folder):
    from . import paths
    paths.setup_logging(data_folder)
    paths.offline_env()
    log = logging.getLogger("worker")
    engines = {}

    def emit(*msg):
        evt_q.put(msg)

    while True:
        msg = cmd_q.get()
        if msg[0] == "quit":
            break
        if msg[0] == "unload":
            engines.clear()
            continue
        if msg[0] != "job":
            continue
        job = msg[1]
        cancel.clear()
        try:
            stats = run_job(job, engines, emit, cancel)
            emit("done", stats)
        except Exception as ex:
            log.error("job failed: %s", traceback.format_exc())
            emit("error", f"{type(ex).__name__}: {ex}")
        if not job.get("keep_loaded", True):
            engines.clear()


def _engine(engines, model, threads, compute, language="auto"):
    from .engines import make_engine
    key = (model["kind"], model["folder"], threads, compute)
    if key in engines:
        return engines[key], 0.0
    if len(engines) >= 2:  # keep at most the detector and one recogniser
        engines.clear()
    t0 = time.perf_counter()
    eng = make_engine(model["kind"])
    if model["kind"] == "whisper":
        eng.load(model["folder"], threads, compute)
    else:
        eng.load(model["folder"], threads, language=language)
    engines[key] = eng
    return eng, time.perf_counter() - t0


def run_job(job, engines, emit, cancel):
    from . import audioproc, media
    from .settings import Settings
    s = Settings(values=job["settings"])
    if job.get("background") and os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    elif job.get("background"):
        try:
            os.nice(10)  # macOS / Linux: below normal priority
        except OSError:
            pass
    threads = int(s.get("transcription.threads")) or (os.cpu_count() or 4)
    compute = s.get("advanced.compute_type")
    total = float(job["duration"]) or 0.0
    start_at = max(0.0, float(job.get("start_at") or 0.0))  # resume: carry on from here
    stats = {"read_s": 0.0, "lid_s": 0.0, "load_s": 0.0, "recog_s": 0.0, "speech_s": 0.0,
             "speech_tokens": 0, "text_tokens": 0, "out_tokens": 0, "gain_db": 0.0, "threads": threads}

    def read_block(start):
        emit("stage", "Reading audio")
        t0 = time.perf_counter()
        a = media.decode(job["path"], job["track"], start, BLOCK_S if total - start > BLOCK_S else None,
                         s.get("advanced.stereo"), s.get("advanced.noise_reduction"))
        stats["read_s"] += time.perf_counter() - t0
        if s.get("advanced.normalise"):
            a, g = audioproc.boost_quiet_audio(a, float(s.get("advanced.target_dbfs")),
                                               float(s.get("advanced.max_gain_db")))
            stats["gain_db"] = max(stats["gain_db"], g)
        return a

    if start_at:
        stats["resumed_from"] = start_at
        emit("raw", f"--- Resumed at {media.fmt_clock(start_at)} ---")
    if s.get("advanced.noise_reduction") not in (None, "off"):
        emit("raw", f"Noise reduction: {s.get('advanced.noise_reduction')} "
                    f"({', '.join(media.noise_filters(s.get('advanced.noise_reduction')))})")
    audio = read_block(start_at)
    if cancel.is_set():
        stats.update({"cancelled": True, "stopped_at": start_at, "model": job.get("model")})
        return stats
    language = job["language"]
    model = job.get("model")
    if language == "auto":
        lid = job.get("lid")
        if lid:
            emit("stage", "Detecting language")
            t0 = time.perf_counter()
            eng, load_s = _engine(engines, lid, threads, compute)
            lang, prob = eng.detect_language(audio[: 900 * 16000], s)
            stats["lid_s"] = time.perf_counter() - t0
            stats["load_s"] += load_s
            language = lang or "en"
            stats["language_prob"] = prob
            emit("raw", f"Language check with {lid['name']}: {language} ({prob:.0%})")
        else:
            language = "en"
    stats["language"] = language
    if not model:
        cands = job["candidates"].get(language if language in LANGS else "en") or []
        if not cands:
            cands = [m for ms in job["candidates"].values() for m in ms]
        if not cands:
            raise RuntimeError("No installed model can transcribe this language. Open Options > Transcription.")
        model = cands[0]
    lang_for_model = language if language in LANGS else None
    if lang_for_model and model.get("languages") and lang_for_model not in model["languages"]:
        lang_for_model = None  # the chosen model cannot take this language: let it detect on its own
    emit("model", {"model": model, "language": language})
    emit("stage", "Loading model")
    eng, load_s = _engine(engines, model, threads, compute, lang_for_model or "auto")
    stats["load_s"] += load_s
    eng.reset_stats()
    scope = job.get("hotwords_scope", "all")
    hotwords = job.get("hotwords", []) if model["kind"] == "whisper" and scope in ("all", language) else []
    last = [0.0]
    latest = [start_at]

    def progress(t):
        latest[0] = max(latest[0], t)
        now = time.perf_counter()
        if now - last[0] > 0.4:
            last[0] = now
            emit("progress", t, total, {k: eng.stats.get(k, 0) for k in ("speech_tokens", "text_tokens", "out_tokens")})

    start = start_at
    while True:
        emit("stage", "Transcribing")
        for seg in eng.transcribe(audio, start, lang_for_model, s, hotwords, cancel, progress):
            emit("segment", seg)
        if cancel.is_set():
            stats["stopped_at"] = latest[0]
            break
        start += BLOCK_S
        if start >= total:
            break
        del audio
        audio = read_block(start)
    for k in ("speech_tokens", "text_tokens", "out_tokens", "recog_s", "speech_s"):
        stats[k] += eng.stats.get(k, 0)
    for k in ("gap_stretches", "gap_checked_s", "gap_silent_s", "gap_kept", "gap_dropped", "gap_found_s"):
        if eng.stats.get(k):
            stats[k] = stats.get(k, 0) + eng.stats[k]
    if eng.stats.get("gap_notes"):
        stats["gap_notes"] = eng.stats["gap_notes"]
    if eng.stats.get("vad_off"):
        stats["vad_off"] = True
    if "language_prob" not in stats and eng.stats.get("language_prob"):
        stats["language_prob"] = eng.stats["language_prob"]
    emit("progress", total if not cancel.is_set() else stats.get("stopped_at", 0), total,
         {k: stats[k] for k in ("speech_tokens", "text_tokens", "out_tokens")})
    stats["model"] = model
    stats["cancelled"] = cancel.is_set()
    return stats


class Worker:
    """Main-process handle on the worker process."""

    def __init__(self, data_folder):
        self.data_folder = data_folder
        self.ctx = mp.get_context("spawn")
        self.proc = None
        self.cmd_q = self.evt_q = self.cancel_ev = None

    def ensure(self):
        if self.proc and self.proc.is_alive():
            return
        self.cmd_q = self.ctx.Queue()
        self.evt_q = self.ctx.Queue()
        self.cancel_ev = self.ctx.Event()
        self.proc = self.ctx.Process(target=worker_main, name="sot-worker",
                                     args=(self.cmd_q, self.evt_q, self.cancel_ev, self.data_folder), daemon=True)
        self.proc.start()

    def submit(self, job):
        self.ensure()
        self.cancel_ev.clear()
        self.cmd_q.put(("job", job))

    def cancel(self):
        if self.cancel_ev:
            self.cancel_ev.set()

    def unload(self):
        if self.proc and self.proc.is_alive():
            self.cmd_q.put(("unload",))

    def poll(self, limit=200):
        out = []
        if not self.evt_q:
            return out
        for _ in range(limit):
            try:
                out.append(self.evt_q.get_nowait())
            except queue.Empty:
                break
        return out

    def alive(self):
        return bool(self.proc and self.proc.is_alive())

    def stop(self):
        if self.proc and self.proc.is_alive():
            try:
                self.cmd_q.put(("quit",))
                self.proc.join(2)
            except Exception:
                pass
            if self.proc.is_alive():
                self.proc.terminate()
