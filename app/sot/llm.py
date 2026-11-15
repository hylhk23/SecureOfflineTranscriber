"""Local language model for the AI summary: llama.cpp, called through its C API with ctypes.

The Windows package carries the official llama.cpp CPU build (release LLAMA_BUILD from github.com/ggml-org/llama.cpp)
as libraries in the llamacpp folder: llama.dll, ggml.dll, ggml-base.dll, one ggml-cpu-*.dll per CPU generation
(llama.cpp picks the best one for this PC) and libomp.dll. No llama.cpp program (.exe) is shipped, and the network
(RPC) backend is left out, so nothing here can open a network connection.

The macOS package carries the libraries of the official macOS arm64 build of the same release, unmodified (they keep
their own code signatures): libllama, libggml, libggml-base, libggml-cpu, libggml-blas and libggml-metal, and
libggml-rpc because libggml links to it. The RPC backend has no server until ggml_backend_rpc_add_server() is given
an address, which this program never does, so it opens no connection either.

Windows runs the model on the CPU. On a Mac with the 'Use the graphics chip' setting on (default), the model and its
context go to the Metal GPU when they fit in the memory Metal recommends using (estimated from the GGUF file size and
the context's key/value cache); otherwise, or if loading on the GPU fails, the model runs on the CPU.

The structures below match include/llama.h of that release exactly (checked by tests against a C probe); a newer
llama.cpp needs these checked again."""
import ctypes
import logging
import os
import sys
import threading

from . import paths

LLAMA_BUILD = "b12000"
MAC_ORDER = ("ggml-base", "ggml-cpu", "ggml-blas", "ggml-metal", "ggml-rpc", "ggml", "llama")  # dependencies first
GPU_MARGIN = 768 * 2 ** 20   # compute buffers and headroom on the GPU, besides the weights and the key/value cache
DEV_GPU, DEV_IGPU = 1, 2     # enum ggml_backend_dev_type
log = logging.getLogger("llm")

c_bool, c_int32, c_uint32, c_float, c_void_p, c_char_p, c_size_t = (
    ctypes.c_bool, ctypes.c_int32, ctypes.c_uint32, ctypes.c_float, ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t)
llama_token = ctypes.c_int32

PROGRESS_CB = ctypes.CFUNCTYPE(c_bool, c_float, c_void_p)
ABORT_CB = ctypes.CFUNCTYPE(c_bool, c_void_p)
LOG_CB = ctypes.CFUNCTYPE(None, ctypes.c_int, c_char_p, c_void_p)


class ModelParams(ctypes.Structure):  # struct llama_model_params (80 bytes on 64-bit)
    _fields_ = [("devices", c_void_p), ("tensor_buft_overrides", c_void_p), ("n_gpu_layers", c_int32),
                ("split_mode", ctypes.c_int), ("load_mode", ctypes.c_int), ("lazy_mode", ctypes.c_int),
                ("main_gpu", c_int32), ("tensor_split", c_void_p), ("progress_callback", c_void_p),
                ("progress_callback_user_data", c_void_p), ("kv_overrides", c_void_p), ("vocab_only", c_bool),
                ("check_tensors", c_bool), ("use_extra_bufts", c_bool), ("no_host", c_bool), ("no_alloc", c_bool),
                ("load_mtp", c_bool)]


class ContextParams(ctypes.Structure):  # struct llama_context_params (168 bytes on 64-bit)
    _fields_ = [("n_ctx", c_uint32), ("n_batch", c_uint32), ("n_ubatch", c_uint32), ("n_seq_max", c_uint32),
                ("n_rs_seq", c_uint32), ("n_outputs_max", c_uint32), ("n_outputs_max_per_seq", c_uint32),
                ("n_threads", c_int32), ("n_threads_batch", c_int32), ("ctx_type", ctypes.c_int),
                ("rope_scaling_type", ctypes.c_int), ("pooling_type", ctypes.c_int), ("attention_type", ctypes.c_int),
                ("flash_attn_type", ctypes.c_int), ("rope_freq_base", c_float), ("rope_freq_scale", c_float),
                ("yarn_ext_factor", c_float), ("yarn_attn_factor", c_float), ("yarn_beta_fast", c_float),
                ("yarn_beta_slow", c_float), ("yarn_orig_ctx", c_uint32), ("defrag_thold", c_float),
                ("cb_eval", c_void_p), ("cb_eval_user_data", c_void_p), ("type_k", ctypes.c_int),
                ("type_v", ctypes.c_int), ("moe_cache_size", c_size_t), ("abort_callback", c_void_p),
                ("abort_callback_data", c_void_p), ("embeddings", c_bool), ("offload_kqv", c_bool),
                ("no_perf", c_bool), ("op_offload", c_bool), ("swa_full", c_bool), ("kv_unified", c_bool),
                ("samplers", c_void_p), ("n_samplers", c_size_t), ("ctx_other", c_void_p)]


class Batch(ctypes.Structure):  # struct llama_batch (56 bytes on 64-bit)
    _fields_ = [("n_tokens", c_int32), ("token", ctypes.POINTER(llama_token)), ("embd", c_void_p),
                ("pos", c_void_p), ("n_seq_id", c_void_p), ("seq_id", c_void_p), ("logits", c_void_p)]


class ChainParams(ctypes.Structure):  # struct llama_sampler_chain_params
    _fields_ = [("no_perf", c_bool)]


SIZES = {"ModelParams": 80, "ContextParams": 168, "Batch": 56, "ChainParams": 1}


class LlmError(RuntimeError):
    pass


class Cancelled(Exception):
    pass


def runtime_dir():
    """Folder with the llama.cpp libraries: ROOT\\llamacpp in the package (SOT_LLAMA_DIR overrides, for tests)."""
    return os.environ.get("SOT_LLAMA_DIR") or os.path.join(paths.ROOT, "llamacpp")


def _libname(base):
    if os.name == "nt":
        return base + ".dll"
    if sys.platform == "darwin":
        return "lib" + base + ".dylib"
    return "lib" + base + ".so"


def available():
    """(True, folder) when the llama.cpp libraries are present, else (False, reason)."""
    d = runtime_dir()
    missing = [n for n in ("llama", "ggml", "ggml-base") if not os.path.isfile(os.path.join(d, _libname(n)))]
    if missing:
        return False, f"The AI summary libraries are missing from {d} ({', '.join(_libname(m) for m in missing)})."
    return True, d


def _short_path(p):
    """Windows: an 8.3 path for a folder or file name the C library may not open (non-ASCII letters)."""
    if os.name != "nt" or p.isascii():
        return p
    buf = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(p, buf, 1024)
    return buf.value if 0 < n < 1024 and buf.value.isascii() else p


GGUF_SCALARS = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i", 6: "<f", 7: "<?", 10: "<Q", 11: "<q", 12: "<d"}


def gguf_header(path, max_keys=4096):
    """Metadata of a GGUF file as {key: value}, read field by field and checked (format: github.com/ggml-org/ggml,
    docs/gguf.md). Arrays of numbers are kept; long arrays and arrays of strings (the tokenizer's vocabulary) are
    skipped. Reading stops at the tokenizer keys, which come after the model's own. {} if the file is not GGUF."""
    import struct
    out = {}
    try:
        with open(path, "rb") as f:
            def read(fmt):
                size = struct.calcsize(fmt)
                data = f.read(size)
                if len(data) != size:
                    raise ValueError("truncated")
                return struct.unpack(fmt, data)[0]

            def string():
                n = read("<Q")
                if n > 1 << 20:
                    raise ValueError("string too long")
                return f.read(n).decode("utf-8", "replace")

            def value(t, depth=0):
                if t in GGUF_SCALARS:
                    return read(GGUF_SCALARS[t])
                if t == 8:
                    return string()
                if t == 9 and depth == 0:
                    it, n = read("<I"), read("<Q")
                    if it == 8 or n > 4096:   # skip strings and long arrays without keeping them
                        for _ in range(n):
                            if it == 8:
                                f.seek(read("<Q"), 1)
                            elif it in GGUF_SCALARS:
                                f.seek(struct.calcsize(GGUF_SCALARS[it]) * n, 1)
                                break
                            else:
                                raise ValueError("bad array")
                        return None
                    return [value(it, 1) for _ in range(n)]
                raise ValueError(f"bad type {t}")

            if f.read(4) != b"GGUF" or read("<I") not in (2, 3):
                return {}
            read("<Q")                 # tensor count
            n_kv = read("<Q")
            for _ in range(min(n_kv, max_keys)):
                key = string()
                if key.startswith("tokenizer."):
                    break
                out[key] = value(read("<I"))
    except (OSError, ValueError, struct.error):
        return out
    return out


def _symbol(name, *libs):
    """The function from the first library that exports it."""
    for lib in libs:
        try:
            return getattr(lib, name)
        except AttributeError:
            continue
    raise LlmError(f"llama.cpp {LLAMA_BUILD}: {name} not found")


class Runtime:
    """The loaded libraries. One per process."""
    _instance = None
    _lock = threading.Lock()

    @classmethod
    def get(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def __init__(self):
        ok, d = available()
        if not ok:
            raise LlmError(d)
        self.dir = d
        if sys.platform == "darwin":
            # the libraries name each other '@rpath/libggml-base.0.dylib' and carry no search path of their own:
            # load them from the bottom up by full path, so each one is already loaded when another one needs it
            for dep in MAC_ORDER:
                f = os.path.join(d, _libname(dep))
                if os.path.isfile(f):
                    ctypes.CDLL(f, mode=ctypes.RTLD_GLOBAL)
        if os.name == "nt":
            os.add_dll_directory(d)
            # dependencies first, by full path, so the CPU libraries loaded later by ggml find them already loaded
            for pre in ("msvcp140.dll", "libomp.dll"):
                if os.path.isfile(os.path.join(d, pre)):
                    ctypes.CDLL(os.path.join(d, pre))
        self.ggml_base = ctypes.CDLL(os.path.join(d, _libname("ggml-base")))
        self.ggml = ctypes.CDLL(os.path.join(d, _libname("ggml")))
        self.lib = L = ctypes.CDLL(os.path.join(d, _libname("llama")))
        self._log_cb = LOG_CB(self._log)
        L.llama_log_set.argtypes = [LOG_CB, c_void_p]
        L.llama_log_set.restype = None
        L.llama_log_set(self._log_cb, None)
        self.ggml.ggml_backend_load_all_from_path.argtypes = [c_char_p]
        self.ggml.ggml_backend_load_all_from_path.restype = None
        self.ggml.ggml_backend_load_all_from_path(_short_path(d).encode("utf-8"))
        self.ggml.ggml_backend_dev_count.restype = c_size_t
        # the device functions: the registry ones are exported by ggml, the others by ggml-base (on Windows a
        # function must be looked up in the DLL that exports it; Linux and macOS also search the dependencies)
        self.dev = {}
        for name, args, res in (("ggml_backend_dev_get", [c_size_t], c_void_p),
                                ("ggml_backend_dev_type", [c_void_p], ctypes.c_int),
                                ("ggml_backend_dev_description", [c_void_p], c_char_p),
                                ("ggml_backend_dev_memory", [c_void_p, ctypes.POINTER(c_size_t),
                                                             ctypes.POINTER(c_size_t)], None)):
            f = _symbol(name, self.ggml, self.ggml_base)
            f.argtypes, f.restype = args, res
            self.dev[name] = f
        L.llama_backend_init.restype = None
        L.llama_backend_init()
        sig = {
            "llama_model_default_params": ([], ModelParams),
            "llama_context_default_params": ([], ContextParams),
            "llama_sampler_chain_default_params": ([], ChainParams),
            "llama_model_load_from_file": ([c_char_p, ModelParams], c_void_p),
            "llama_model_free": ([c_void_p], None),
            "llama_init_from_model": ([c_void_p, ContextParams], c_void_p),
            "llama_free": ([c_void_p], None),
            "llama_n_ctx": ([c_void_p], c_uint32),
            "llama_n_batch": ([c_void_p], c_uint32),
            "llama_model_get_vocab": ([c_void_p], c_void_p),
            "llama_model_n_ctx_train": ([c_void_p], c_int32),
            "llama_model_desc": ([c_void_p, c_char_p, c_size_t], c_int32),
            "llama_model_meta_val_str": ([c_void_p, c_char_p, c_char_p, c_size_t], c_int32),
            "llama_vocab_is_eog": ([c_void_p, llama_token], c_bool),
            "llama_tokenize": ([c_void_p, c_char_p, c_int32, ctypes.POINTER(llama_token), c_int32, c_bool, c_bool],
                               c_int32),
            "llama_token_to_piece": ([c_void_p, llama_token, ctypes.c_char_p, c_int32, c_int32, c_bool], c_int32),
            "llama_batch_get_one": ([ctypes.POINTER(llama_token), c_int32], Batch),
            "llama_decode": ([c_void_p, Batch], c_int32),
            "llama_get_memory": ([c_void_p], c_void_p),
            "llama_memory_clear": ([c_void_p, c_bool], None),
            "llama_sampler_chain_init": ([ChainParams], c_void_p),
            "llama_sampler_chain_add": ([c_void_p, c_void_p], None),
            "llama_sampler_init_greedy": ([], c_void_p),
            "llama_sampler_init_dist": ([c_uint32], c_void_p),
            "llama_sampler_init_top_k": ([c_int32], c_void_p),
            "llama_sampler_init_top_p": ([c_float, c_size_t], c_void_p),
            "llama_sampler_init_min_p": ([c_float, c_size_t], c_void_p),
            "llama_sampler_init_temp": ([c_float], c_void_p),
            "llama_sampler_init_penalties": ([c_int32, c_int32, c_float, c_float, c_float], c_void_p),
            "llama_vocab_n_tokens": ([c_void_p], c_int32),
            "llama_sampler_sample": ([c_void_p, c_void_p, c_int32], llama_token),
            "llama_sampler_free": ([c_void_p], None),
            "llama_print_system_info": ([], c_char_p),
        }
        for name, (args, res) in sig.items():
            f = getattr(L, name)
            f.argtypes = args
            f.restype = res
        for cls, size in ((ModelParams, 80), (ContextParams, 168), (Batch, 56)):
            if ctypes.sizeof(cls) != size:
                raise LlmError(f"{cls.__name__} layout does not match llama.cpp {LLAMA_BUILD}.")

    def _log(self, level, text, _data):
        if level >= 4 and text:  # 4 = error; warnings and info are too chatty for the program log
            log.warning("llama.cpp: %s", text.decode("utf-8", "replace").rstrip())

    def system_info(self):
        return (self.lib.llama_print_system_info() or b"").decode("utf-8", "replace").strip()

    def devices(self):
        return int(self.ggml.ggml_backend_dev_count())

    def gpu(self):
        """(description, free bytes, total bytes) of the first GPU (Metal on a Mac), or None. The Windows package has
        no GPU backend, so it finds none."""
        f = self.dev
        for i in range(self.devices()):
            d = f["ggml_backend_dev_get"](i)
            if d and f["ggml_backend_dev_type"](d) in (DEV_GPU, DEV_IGPU):
                free, total = c_size_t(0), c_size_t(0)
                f["ggml_backend_dev_memory"](d, ctypes.byref(free), ctypes.byref(total))
                desc = (f["ggml_backend_dev_description"](d) or b"").decode("utf-8", "replace").strip()
                return desc or "GPU", int(free.value), int(total.value)
        return None


class Model:
    """A GGUF model and one context for it. Not thread-safe; used by one worker thread."""

    def __init__(self, path, n_ctx=8192, threads=0, cancel=None, on_load=None, gpu=False):
        """gpu: use the Mac's graphics chip (Metal) when the model and its context fit; ignored elsewhere."""
        self.rt = Runtime.get()
        self.cancel = cancel
        self.path = path
        if self.rt.devices() == 0:
            raise LlmError("llama.cpp found no CPU backend in " + self.rt.dir)
        self.device, self.device_note = "CPU", ""
        use_gpu = False
        if gpu and sys.platform == "darwin":
            g = self.rt.gpu()
            if not g:
                self.device_note = "no graphics chip that llama.cpp can use was found"
            else:
                need = self.memory_needed(path, n_ctx)
                if need and need <= g[1]:
                    use_gpu = True
                    self.device = f"{g[0]} (Metal)"
                else:
                    self.device_note = (f"the model and its context need about {need / 1e9:.1f} GB; the graphics "
                                        f"chip can use {g[1] / 1e9:.1f} GB" if need else
                                        "the model's size could not be read")
        try:
            self._load(path, n_ctx, threads, use_gpu, on_load)
        except LlmError as ex:
            if not use_gpu or (cancel is not None and cancel.is_set()):
                raise
            log.warning("loading on the graphics chip failed (%s); using the processor", ex)
            self.device, self.device_note = "CPU", "loading on the graphics chip failed"
            self._load(path, n_ctx, threads, False, on_load)
        if self.device_note:
            log.info("summary model on the processor: %s", self.device_note)

    @staticmethod
    def memory_needed(path, n_ctx):
        """Bytes the model needs on the GPU: file size + key/value cache for the context + GPU_MARGIN. The cache is
        estimated from the GGUF header (read in Python, no weights loaded): layers x context x key/value heads x
        (key + value length) x 2 bytes (f16). Models with sliding-window or linear-attention layers need less, so the
        estimate errs on the safe side. None if the header cannot be read."""
        h = gguf_header(path)
        arch = h.get("general.architecture")
        if not isinstance(arch, str):
            return None

        def num(key, default=0):
            v = h.get(f"{arch}.{key}")
            if isinstance(v, list):  # per-layer values (for example key/value heads): take the largest
                v = max([x for x in v if isinstance(x, int)] or [0])
            return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else default
        layers, heads, embd = num("block_count"), num("attention.head_count"), num("embedding_length")
        if not (layers and heads and embd):
            return None
        kv_heads = num("attention.head_count_kv", heads)
        klen = num("attention.key_length", embd // heads)
        vlen = num("attention.value_length", klen)
        train = num("context_length")
        ctx = min(int(n_ctx), train) if train else int(n_ctx)
        return os.path.getsize(path) + layers * ctx * kv_heads * (klen + vlen) * 2 + GPU_MARGIN

    def _load(self, path, n_ctx, threads, gpu, on_load):
        L = self.rt.lib
        cancel = self.cancel
        mp = L.llama_model_default_params()
        mp.n_gpu_layers = -1 if gpu else 0   # -1: every layer on the GPU

        def progress(frac, _data):
            if on_load:
                on_load(frac)
            return not (cancel is not None and cancel.is_set())  # False stops loading
        self._progress = PROGRESS_CB(progress)
        mp.progress_callback = ctypes.cast(self._progress, c_void_p)
        p = _short_path(os.path.abspath(path))
        self.model = L.llama_model_load_from_file(p.encode("utf-8"), mp)
        if not self.model:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            raise LlmError(f"The model file could not be loaded: {path}")
        self.vocab = L.llama_model_get_vocab(self.model)
        self.n_ctx_train = int(L.llama_model_n_ctx_train(self.model))
        self.threads = int(threads) or max(1, (os.cpu_count() or 4))
        cp = L.llama_context_default_params()
        cp.n_ctx = int(min(n_ctx, self.n_ctx_train)) if self.n_ctx_train > 0 else int(n_ctx)
        cp.n_batch = 512
        cp.n_ubatch = 512
        cp.n_seq_max = 1
        cp.n_threads = self.threads
        cp.n_threads_batch = self.threads
        cp.no_perf = True
        cp.offload_kqv = gpu     # key/value cache and operations on the GPU only when the model is there
        cp.op_offload = gpu
        self._abort = ABORT_CB(lambda _d: bool(self.cancel is not None and self.cancel.is_set()))
        cp.abort_callback = ctypes.cast(self._abort, c_void_p)
        self.ctx = L.llama_init_from_model(self.model, cp)
        if not self.ctx:
            L.llama_model_free(self.model)
            self.model = None
            raise LlmError("Not enough memory for the model and its context. Choose a smaller context size or model.")
        self.n_ctx = int(L.llama_n_ctx(self.ctx))
        self.n_batch = int(L.llama_n_batch(self.ctx))

    # ------------------------------------------------------------------ text <-> tokens
    def meta(self, key):
        buf = ctypes.create_string_buffer(256)
        n = self.rt.lib.llama_model_meta_val_str(self.model, key.encode(), buf, 256)
        return buf.value.decode("utf-8", "replace") if n >= 0 else ""

    def tokenize(self, text, add_special=False, parse_special=True):
        L = self.rt.lib
        data = text.encode("utf-8")
        n = len(data) + 16
        buf = (llama_token * n)()
        got = L.llama_tokenize(self.vocab, data, len(data), buf, n, add_special, parse_special)
        if got < 0:
            n = -got
            buf = (llama_token * n)()
            got = L.llama_tokenize(self.vocab, data, len(data), buf, n, add_special, parse_special)
        if got < 0:
            raise LlmError("The text could not be tokenized.")
        return list(buf[:got])

    def count(self, text):
        return len(self.tokenize(text, add_special=False, parse_special=False))

    def piece(self, tok):
        buf = ctypes.create_string_buffer(64)
        n = self.rt.lib.llama_token_to_piece(self.vocab, tok, buf, 64, 0, False)
        if n < 0:
            buf = ctypes.create_string_buffer(-n)
            n = self.rt.lib.llama_token_to_piece(self.vocab, tok, buf, -n, 0, False)
        return buf.raw[:max(0, n)]

    # ------------------------------------------------------------------ generation
    def generate(self, prompt, max_tokens=1024, temperature=0.3, top_k=40, top_p=0.9, min_p=0.05,
                 repeat_penalty=1.05, presence_penalty=0.0, seed=1234, on_prompt=None, on_text=None,
                 add_special=True):
        """Returns (text, stats). on_prompt(done, total) while the prompt is read; on_text(new_text) as it is written.
        Raises Cancelled when the cancel event is set."""
        import time
        L = self.rt.lib
        mem = L.llama_get_memory(self.ctx)
        L.llama_memory_clear(mem, True)
        toks = self.tokenize(prompt, add_special=add_special, parse_special=True)
        if len(toks) + 8 >= self.n_ctx:
            raise LlmError(f"The text is too long for the context ({len(toks)} of {self.n_ctx} tokens).")
        max_tokens = max(16, min(int(max_tokens), self.n_ctx - len(toks) - 4))
        arr = (llama_token * len(toks))(*toks)
        t0 = time.perf_counter()
        done = 0
        step = max(32, self.n_batch)
        while done < len(toks):
            if self.cancel is not None and self.cancel.is_set():
                raise Cancelled()
            n = min(step, len(toks) - done)
            ptr = ctypes.cast(ctypes.byref(arr, done * ctypes.sizeof(llama_token)), ctypes.POINTER(llama_token))
            rc = L.llama_decode(self.ctx, L.llama_batch_get_one(ptr, n))
            if rc == 2 or (self.cancel is not None and self.cancel.is_set()):
                raise Cancelled()
            if rc != 0:
                raise LlmError(f"llama.cpp could not read the text (error {rc}).")
            done += n
            if on_prompt:
                on_prompt(done, len(toks))
        t1 = time.perf_counter()
        chain = L.llama_sampler_chain_init(ChainParams(True))
        try:
            if (repeat_penalty and repeat_penalty != 1.0) or presence_penalty:
                L.llama_sampler_chain_add(chain, L.llama_sampler_init_penalties(
                    L.llama_vocab_n_tokens(self.vocab), 256, float(repeat_penalty or 1.0), 0.0,
                    float(presence_penalty or 0.0)))
            if temperature <= 0:
                L.llama_sampler_chain_add(chain, L.llama_sampler_init_greedy())
            else:
                L.llama_sampler_chain_add(chain, L.llama_sampler_init_top_k(int(top_k)))
                L.llama_sampler_chain_add(chain, L.llama_sampler_init_top_p(float(top_p), 1))
                L.llama_sampler_chain_add(chain, L.llama_sampler_init_min_p(float(min_p), 1))
                L.llama_sampler_chain_add(chain, L.llama_sampler_init_temp(float(temperature)))
                L.llama_sampler_chain_add(chain, L.llama_sampler_init_dist(int(seed)))
            out = bytearray()
            shown = 0
            tok_arr = (llama_token * 1)()
            n_out = 0
            for _ in range(max_tokens):
                if self.cancel is not None and self.cancel.is_set():
                    raise Cancelled()
                tok = L.llama_sampler_sample(chain, self.ctx, -1)
                if L.llama_vocab_is_eog(self.vocab, tok):
                    break
                out += self.piece(tok)
                n_out += 1
                text = out.decode("utf-8", "ignore") if not _incomplete_utf8(out) else out[:_utf8_cut(out)].decode("utf-8", "ignore")
                if on_text and len(text) > shown:
                    on_text(text[shown:])
                    shown = len(text)
                tok_arr[0] = tok
                rc = L.llama_decode(self.ctx, L.llama_batch_get_one(tok_arr, 1))
                if rc == 2:
                    raise Cancelled()
                if rc != 0:
                    break  # context full: keep what was written
            text = out.decode("utf-8", "replace")
            if on_text and len(text) > shown:
                on_text(text[shown:])
        finally:
            L.llama_sampler_free(chain)
        t2 = time.perf_counter()
        return text, {"prompt_tokens": len(toks), "output_tokens": n_out, "prompt_s": t1 - t0, "gen_s": t2 - t1,
                      "stopped": n_out >= max_tokens}

    def close(self):
        L = self.rt.lib
        if getattr(self, "ctx", None):
            L.llama_free(self.ctx)
            self.ctx = None
        if getattr(self, "model", None):
            L.llama_model_free(self.model)
            self.model = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _utf8_cut(b):
    """Length of the complete UTF-8 prefix of b (a Chinese character can arrive in two tokens)."""
    i = len(b)
    k = 0
    while i > 0 and k < 4:
        c = b[i - 1]
        if c & 0xC0 == 0x80:  # continuation byte
            i -= 1
            k += 1
            continue
        need = 2 if c & 0xE0 == 0xC0 else 3 if c & 0xF0 == 0xE0 else 4 if c & 0xF8 == 0xF0 else 1
        return len(b) if k + 1 >= need else i - 1
    return len(b)


def _incomplete_utf8(b):
    return _utf8_cut(b) != len(b)
