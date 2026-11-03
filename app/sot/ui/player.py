"""Player (FR-UI-04): ffmpeg.exe streams PCM from a position; sounddevice (PortAudio) plays it."""
import threading

from PySide6.QtCore import QObject, Signal

from .. import media


class Player(QObject):
    state_changed = Signal(bool)
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self.path = None
        self.track = 0
        self.duration = 0.0
        self.pos = 0.0
        self.playing = False
        self._stream = None
        self._src = None
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._eof = False
        self._frames = 0
        self._start = 0.0
        self._reader = None
        self._stop = threading.Event()
        self._sd = None
        self.unavailable = None
        self.speed = 1.0          # 0.5-1.5x, pitch kept
        self.denoise = "off"      # play with the noise reduction used for recognition
        try:
            import sounddevice as sd
            self._sd = sd
        except Exception as ex:  # PortAudio missing
            self.unavailable = f"Audio playback is not available: {ex}"

    def load(self, path, track=0, duration=0.0):
        self.stop()
        self.path, self.track, self.duration, self.pos = path, track, duration, 0.0

    def position(self):
        if self.playing:
            return min(self.duration or 1e12, self._start + self._frames / media.PcmStream.RATE * self.speed)
        return self.pos

    def toggle(self):
        self.pause() if self.playing else self.play()

    def play(self, at=None):
        if self.unavailable:
            self.error.emit(self.unavailable)
            return
        if not self.path:
            return
        if at is not None:
            self.pos = max(0.0, min(at, self.duration or at))
        self._close()
        if self.duration and self.pos >= self.duration - 0.2:
            self.pos = 0.0
        try:
            self._src = media.PcmStream(self.path, self.track, self.pos, self.speed, self.denoise)
        except Exception as ex:
            self.error.emit(str(ex))
            return
        self._buf = bytearray()
        self._eof = False
        self._frames = 0
        self._start = self.pos
        self._stop.clear()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        try:
            self._stream = self._sd.RawOutputStream(samplerate=media.PcmStream.RATE, channels=2, dtype="int16",
                                                    callback=self._callback, finished_callback=self._finished)
            self._stream.start()
        except Exception as ex:
            self._close()
            self.error.emit(f"Cannot open the audio output: {ex}")
            return
        self.playing = True
        self.state_changed.emit(True)

    def _read(self):
        limit = media.PcmStream.RATE * 4 * 3  # keep about 3 s buffered
        while not self._stop.is_set():
            with self._lock:
                full = len(self._buf) >= limit
            if full:
                self._stop.wait(0.05)
                continue
            data = self._src.read(65536) if self._src else b""
            if not data:
                self._eof = True
                return
            with self._lock:
                self._buf.extend(data)

    def _callback(self, outdata, frames, time_info, status):
        need = frames * 4
        with self._lock:
            chunk = bytes(self._buf[:need])
            del self._buf[:need]
        if len(chunk) < need:
            if self._eof and not chunk:
                outdata[:] = b"\x00" * need
                raise self._sd.CallbackStop
            chunk += b"\x00" * (need - len(chunk))
        outdata[:] = chunk
        self._frames += frames

    def _finished(self):
        if self.playing and self._eof:
            self.pos = self.position()
            self.playing = False
            self.state_changed.emit(False)

    def pause(self):
        if self.playing:
            self.pos = self.position()
        self._close()
        if self.playing:
            self.playing = False
            self.state_changed.emit(False)

    def stop(self):
        self.pause()
        self.pos = 0.0

    def seek(self, t):
        was = self.playing
        self.pause()
        self.pos = max(0.0, min(t, self.duration or t))
        if was:
            self.play()

    def set_options(self, speed=None, denoise=None):
        """Change speed or noise preview; playback carries on from the same place."""
        changed = (speed is not None and abs(speed - self.speed) > 1e-3) or (denoise is not None and denoise != self.denoise)
        if not changed:
            return
        was = self.playing
        if was:
            self.pause()
        if speed is not None:
            self.speed = max(0.5, min(2.0, float(speed)))
        if denoise is not None:
            self.denoise = denoise
        if was:
            self.play()

    def skip(self, delta):
        self.seek(self.position() + delta)

    def _close(self):
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.abort()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._src is not None:
            self._src.close()
            self._src = None
