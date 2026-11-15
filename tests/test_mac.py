"""macOS edition: the AI summary engine (llama.cpp libraries) in the package, how the program loads them, and the
graphics chip (Metal). These run on any system; the real libraries and Metal can only be used on a Mac ({GAP}:
smoke-test.command and a summary on a Mac check that)."""
import ctypes
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, os.path.join(ROOT, "build"))

from sot import llm  # noqa: E402


class Stop(Exception):
    pass


def test_mac_loads_llama_libraries_dependencies_first(tmp_path, monkeypatch):
    """The macOS libraries refer to each other as @rpath/... without a search path: each must already be loaded,
    by full path, when a library that needs it is loaded."""
    for n in llm.MAC_ORDER:
        (tmp_path / f"lib{n}.dylib").write_bytes(b"\0")
    monkeypatch.setenv("SOT_LLAMA_DIR", str(tmp_path))
    monkeypatch.setattr(llm.sys, "platform", "darwin")
    monkeypatch.setattr(llm.os, "name", "posix")
    loads = []

    def fake_cdll(path, mode=ctypes.DEFAULT_MODE):
        loads.append((os.path.basename(path), mode))
        if len(loads) > len(llm.MAC_ORDER):  # the preload is done; stop before the C functions are set up
            raise Stop()
        return object()
    monkeypatch.setattr(llm.ctypes, "CDLL", fake_cdll)
    with pytest.raises(Stop):
        llm.Runtime()
    first = loads[:len(llm.MAC_ORDER)]
    assert [n for n, _ in first] == [f"lib{n}.dylib" for n in
                                     ("ggml-base", "ggml-cpu", "ggml-blas", "ggml-metal", "ggml-rpc", "ggml", "llama")]
    assert all(m == ctypes.RTLD_GLOBAL for _, m in first)


def test_mac_package_llama_folder(tmp_path):
    """build_mac.add_llama: the official archive's seven libraries and their links, nothing else (no programs);
    every @rpath library they need is in the folder; all are arm64 and signed."""
    import build_mac
    import macho_check
    if not os.path.isfile(os.path.join(ROOT, "build", "cache-llama", build_mac.LLAMA_MAC_FILE)):
        pytest.skip("the llama.cpp macOS archive is not in build/cache-llama (downloaded by build_mac.py)")
    build_mac.add_llama(str(tmp_path))
    lc = tmp_path / "llamacpp"
    files = sorted(os.listdir(lc))
    real = [f for f in files if not os.path.islink(lc / f) and f.endswith(".dylib")]
    assert len(real) == 7 and len([f for f in files if os.path.islink(lc / f)]) == 14
    assert {"LICENSE.txt", "SOURCE.txt"} <= set(files)
    for n in llm.MAC_ORDER:
        assert os.path.isfile(lc / f"lib{n}.dylib")   # the names llm.Runtime opens on a Mac
    n, problems, exes, _ = macho_check.check_tree(str(tmp_path))
    assert n == 7 and not problems and not exes
    src = (lc / "SOURCE.txt").read_text()
    assert build_mac.LLAMA_MAC_SHA256 in src and "opens no connection" in src


# ---------------------------------------------------------------- graphics chip (Metal) for the AI summary
def _gguf(path, kv):
    """A small GGUF file with the given metadata (format: github.com/ggml-org/ggml docs/gguf.md)."""
    import struct

    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b
    out = b"GGUF" + struct.pack("<IQQ", 3, 0, len(kv))
    for k, v in kv:
        out += s(k)
        if isinstance(v, str):
            out += struct.pack("<I", 8) + s(v)
        elif isinstance(v, list) and v and isinstance(v[0], str):
            out += struct.pack("<IIQ", 9, 8, len(v)) + b"".join(s(x) for x in v)
        elif isinstance(v, list):
            out += struct.pack("<IIQ", 9, 5, len(v)) + b"".join(struct.pack("<i", x) for x in v)
        else:
            out += struct.pack("<I", 4) + struct.pack("<I", v)
    path.write_bytes(out + b"\0" * 1000)
    return path


QWEN3_1_7B = [("general.architecture", "qwen3"), ("qwen3.block_count", 28), ("qwen3.context_length", 40960),
              ("qwen3.embedding_length", 2048), ("qwen3.attention.head_count", 16),
              ("qwen3.attention.head_count_kv", 8), ("qwen3.attention.key_length", 128),
              ("qwen3.attention.value_length", 128), ("tokenizer.ggml.tokens", ["a", "b", "c"]),
              ("qwen3.after_tokenizer", 1)]


def test_gguf_header_is_read_field_by_field(tmp_path):
    h = llm.gguf_header(_gguf(tmp_path / "m.gguf", QWEN3_1_7B))
    assert h["general.architecture"] == "qwen3" and h["qwen3.block_count"] == 28
    assert "tokenizer.ggml.tokens" not in h and "qwen3.after_tokenizer" not in h   # stops at the tokenizer
    (tmp_path / "x.gguf").write_bytes(b"not a model")
    assert llm.gguf_header(str(tmp_path / "x.gguf")) == {}
    assert llm.gguf_header(str(tmp_path / "missing.gguf")) == {}
    cut = (tmp_path / "m.gguf").read_bytes()[:60]
    (tmp_path / "cut.gguf").write_bytes(cut)
    assert "qwen3.block_count" not in llm.gguf_header(str(tmp_path / "cut.gguf"))       # truncated: partial, no error
    real = os.environ.get("SOT_TEST_GGUF", "")
    if os.path.isfile(real):  # the published Qwen3-1.7B Q8_0 file, when SOT_TEST_GGUF names it
        r = llm.gguf_header(real)
        assert (r["qwen3.block_count"], r["qwen3.attention.head_count_kv"], r["qwen3.attention.key_length"]) == \
            (28, 8, 128)


def test_memory_estimate(tmp_path):
    p = _gguf(tmp_path / "m.gguf", QWEN3_1_7B)
    size = os.path.getsize(p)
    kv = 28 * 8192 * 8 * (128 + 128) * 2                       # layers x context x kv heads x (k + v) x f16
    assert llm.Model.memory_needed(str(p), 8192) == size + kv + llm.GPU_MARGIN
    assert llm.Model.memory_needed(str(p), 100000) == size + 28 * 40960 * 8 * 256 * 2 + llm.GPU_MARGIN  # trained max
    gem = [("general.architecture", "gemma4"), ("gemma4.block_count", 4), ("gemma4.embedding_length", 1536),
           ("gemma4.attention.head_count", 8), ("gemma4.attention.head_count_kv", [1, 2, 1, 1])]
    g = _gguf(tmp_path / "g.gguf", gem)
    assert llm.Model.memory_needed(str(g), 1000) == os.path.getsize(g) + 4 * 1000 * 2 * (192 + 192) * 2 + \
        llm.GPU_MARGIN                                            # per-layer heads: the largest; key length embd/heads
    assert llm.Model.memory_needed(str(_gguf(tmp_path / "e.gguf", [("general.architecture", "x")])), 8192) is None


class FakeRuntime:
    def __init__(self, gpu):
        self._gpu = gpu
        self.dir = "fake"

    def devices(self):
        return 2 if self._gpu else 1

    def gpu(self):
        return self._gpu


def _model(monkeypatch, platform, gpu_dev, gpu, need=3 * 10 ** 9, fail_gpu=False):
    monkeypatch.setattr(llm.sys, "platform", platform)
    monkeypatch.setattr(llm.Runtime, "get", classmethod(lambda cls: FakeRuntime(gpu_dev)))
    monkeypatch.setattr(llm.Model, "memory_needed", staticmethod(lambda path, n_ctx: need))
    calls = []

    def load(self, path, n_ctx, threads, use_gpu, on_load):
        calls.append(use_gpu)
        if use_gpu and fail_gpu:
            raise llm.LlmError("no room")
    monkeypatch.setattr(llm.Model, "_load", load)
    m = llm.Model("model.gguf", 8192, gpu=gpu)
    return m, calls


def test_mac_uses_the_graphics_chip_when_the_model_fits(monkeypatch):
    big = ("Apple M2", 10 * 2 ** 30, 10 * 2 ** 30)
    m, calls = _model(monkeypatch, "darwin", big, True)
    assert calls == [True] and m.device == "Apple M2 (Metal)" and m.device_note == ""
    m, calls = _model(monkeypatch, "darwin", ("Apple M1", 2 * 10 ** 9, 5 * 10 ** 9), True)
    assert calls == [False] and m.device == "CPU" and "need about 3.0 GB" in m.device_note and "2.0 GB" in m.device_note
    m, calls = _model(monkeypatch, "darwin", None, True)
    assert calls == [False] and "no graphics chip" in m.device_note
    m, calls = _model(monkeypatch, "darwin", big, True, need=None)
    assert calls == [False] and "could not be read" in m.device_note
    m, calls = _model(monkeypatch, "darwin", big, True, fail_gpu=True)  # loading on Metal fails: the processor
    assert calls == [True, False] and m.device == "CPU" and "failed" in m.device_note
    m, calls = _model(monkeypatch, "darwin", big, False)                # setting off
    assert calls == [False] and m.device_note == ""
    m, calls = _model(monkeypatch, "win32", big, True)                  # Windows: always the processor
    assert calls == [False] and m.device == "CPU" and m.device_note == ""


def test_load_parameters_follow_the_device():
    """Every layer, the key/value cache and the operations on the GPU, or none of them."""
    import inspect
    code = inspect.getsource(llm.Model._load)
    assert "mp.n_gpu_layers = -1 if gpu else 0" in code
    assert "cp.offload_kqv = gpu" in code and "cp.op_offload = gpu" in code


def test_summary_statistics_say_where_it_ran():
    from sot import summary
    st = {"seconds": 61, "prompt_tokens": 871, "output_tokens": 463, "gen_s": 55.0, "parts": 1}
    assert summary.stats_line(dict(st, device="GPU"), "Qwen3-1.7B").endswith("8.4 tokens/s · on the graphics chip")
    assert summary.stats_line(dict(st, device="CPU"), "Qwen3-1.7B").endswith("on the processor")
    assert "on the" not in summary.stats_line(st, "Qwen3-1.7B")


def test_graphics_chip_option(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    from sot import system
    from sot.modelmgr import ModelManager
    from sot.settings import Settings
    from sot.ui import options as O
    app = QApplication.instance() or QApplication([])  # noqa: F841
    s = Settings(path=str(tmp_path / "settings.json"))
    s.set("summary.enabled", True)
    s.set("transcription.download_folder", str(tmp_path / "dl"))
    assert s.get("summary.gpu") is True
    for mac in (False, True):
        monkeypatch.setattr(system, "IS_MAC", mac)
        dlg = O.OptionsDialog(None, s, ModelManager(s), 5)
        tab = dlg.summary
        assert tab.gpu.text() == "Use the graphics chip (Mac)" and tab.gpu.isChecked()
        assert tab.gpu.isEnabled() is mac
        assert ("Mac only" in tab.gpu.toolTip()) is (not mac)
        tab.gpu.setChecked(False)
        tab.collect(dlg.work)
        assert dlg.work.get("summary.gpu") is False
        dlg.close()


def test_every_engine_function_is_exported_where_it_is_looked_up():
    """Regression (2.3.1): the device functions used to choose the graphics chip were looked up in ggml.dll, but
    Windows exports ggml_backend_dev_type, _description and _memory from ggml-base.dll, so the summary engine did
    not start on Windows. Linux and macOS also search a library's dependencies, so the tests on Linux passed; a run
    of the Windows package under Wine found it. This test reads the export tables of the real Windows DLLs."""
    import inspect
    import re
    import zipfile
    pefile = pytest.importorskip("pefile")
    from build import LLAMA_URL
    z = os.path.join(ROOT, "build", "cache-llama", os.path.basename(LLAMA_URL))
    if not os.path.isfile(z):
        pytest.skip("the llama.cpp Windows zip is not in build/cache-llama (downloaded by build.py)")
    exports = {}
    with zipfile.ZipFile(z) as zf:
        for n in zf.namelist():
            base = os.path.basename(n).lower()
            if base in ("llama.dll", "ggml.dll", "ggml-base.dll"):
                pe = pefile.PE(data=zf.read(n), fast_load=True)
                pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
                exports[base] = {s.name.decode() for s in pe.DIRECTORY_ENTRY_EXPORT.symbols if s.name}
    code = inspect.getsource(llm.Runtime.__init__)
    names = set(re.findall(r'"((?:llama|ggml)_[a-z0-9_]+)"', code)) | \
        set(re.findall(r"\.((?:llama|ggml)_[a-z0-9_]+)[.(]", code))
    assert {"ggml_backend_dev_count", "ggml_backend_load_all_from_path", "llama_backend_init",
            "ggml_backend_dev_memory"} <= names and len(names) >= 40
    for name in sorted(names):
        if name.startswith("llama_"):
            assert name in exports["llama.dll"], name
        elif name in ("ggml_backend_load_all_from_path", "ggml_backend_dev_count"):
            assert name in exports["ggml.dll"], name             # called on the ggml library directly
        else:
            assert name in exports["ggml.dll"] | exports["ggml-base.dll"], name   # looked up in both (_symbol)
    assert "ggml_backend_dev_type" in exports["ggml-base.dll"] and "ggml_backend_dev_type" not in exports["ggml.dll"]
    assert "_symbol(name, self.ggml, self.ggml_base)" in code            # the device functions are looked up in both
    assert "getattr(self.ggml, name)" not in code
