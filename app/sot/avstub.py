"""faster-whisper imports PyAV ("av") at start-up only for its own file decoder, which this program never uses:
ffmpeg.exe decodes every file. PyAV is left out of the portable package (one of its files was blocked by
antivirus during unzipping), so an empty stand-in module satisfies the import."""
import sys
import types


def install():
    if "av" in sys.modules:
        return
    try:
        import av  # noqa: F401  (source installs may have it)
        return
    except Exception:
        pass
    stub = types.ModuleType("av")
    stub.__doc__ = "Stand-in: PyAV is not included; audio is decoded by ffmpeg.exe."

    def _unavailable(*_a, **_k):
        raise RuntimeError("PyAV is not included in this package; audio is decoded by ffmpeg.exe.")
    stub.open = _unavailable
    for sub in ("audio", "error", "audio.resampler", "audio.fifo"):
        m = types.ModuleType("av." + sub)
        sys.modules["av." + sub] = m
    stub.audio = sys.modules["av.audio"]
    stub.error = sys.modules["av.error"]
    stub.error.InvalidDataError = type("InvalidDataError", (Exception,), {})
    sys.modules["av"] = stub
