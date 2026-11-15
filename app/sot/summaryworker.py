"""The AI summary runs in its own process, separate from the recognition worker, so the window (Transcript and
Raw output tabs, the player) stays usable while it works, and a failure in the language model cannot take the
transcript with it.

Main -> worker: ("job", dict) / ("unload",) / ("quit",).   Cancel: a shared Event.
Worker -> main: ("stage", text), ("progress", fraction), ("text", new_text), ("model", info),
                ("done", text, stats), ("cancelled",), ("error", message)."""
import logging
import multiprocessing as mp
import os
import queue
import time
import traceback


def summary_main(cmd_q, evt_q, cancel, data_folder):
    from . import paths
    paths.setup_logging(data_folder)
    paths.offline_env()
    log = logging.getLogger("summary")
    loaded = {"key": None, "model": None}

    def emit(*msg):
        evt_q.put(msg)

    def unload():
        if loaded["model"] is not None:
            loaded["model"].close()
        loaded.update(key=None, model=None)

    while True:
        msg = cmd_q.get()
        if msg[0] == "quit":
            break
        if msg[0] == "unload":
            unload()
            continue
        if msg[0] != "job":
            continue
        job = msg[1]
        cancel.clear()
        try:
            if job.get("background"):
                _lower_priority()
            from . import llm, summary
            key = (job["model_path"], int(job["context"]), int(job["threads"]), bool(job.get("gpu")))
            if loaded["key"] != key:
                unload()
                emit("stage", f"Loading {job['model_name']}")
                t0 = time.perf_counter()
                m = llm.Model(job["model_path"], n_ctx=int(job["context"]), threads=int(job["threads"]), cancel=cancel,
                              on_load=lambda f: emit("progress", 0.05 * f), gpu=bool(job.get("gpu")))
                loaded.update(key=key, model=m)
                log.info("summary model %s loaded in %.1f s (context %d, %d threads, %s)", job["model_name"],
                         time.perf_counter() - t0, m.n_ctx, m.threads, m.device)
            model = loaded["model"]
            model.cancel = cancel
            emit("model", {"name": job["model_name"], "context": model.n_ctx, "threads": model.threads,
                           "device": model.device, "device_note": model.device_note})
            text, stats = summary.run(model, job["blocks"], job["options"], emit, cancel)
            stats["context"] = model.n_ctx
            stats["device"] = "GPU" if model.device != "CPU" else "CPU"
            emit("done", text, stats)
        except Exception as ex:
            from . import llm
            if isinstance(ex, llm.Cancelled) or cancel.is_set():
                emit("cancelled")
            else:
                log.error("summary failed: %s", traceback.format_exc())
                emit("error", f"{ex}")
        if not job.get("keep_loaded"):
            unload()


def _lower_priority():
    if os.name == "nt":
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    else:
        try:
            os.nice(10)
        except OSError:
            pass


class SummaryWorker:
    """Main-process handle on the summary process (started on first use)."""

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
        self.proc = self.ctx.Process(target=summary_main, name="sot-summary",
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

    def poll(self, limit=400):
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
                self.cancel()
                self.cmd_q.put(("quit",))
                self.proc.join(3)
            except Exception:
                pass
            if self.proc.is_alive():
                self.proc.terminate()
