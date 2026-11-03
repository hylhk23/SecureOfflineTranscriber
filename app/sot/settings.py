"""Settings: defaults, atomic save, export/import (spec sections 5-10, 14)."""
import copy
import json
import os
import tempfile

from . import paths

DEFAULT_FILTER_PHRASES = [
    "Thank you for watching",
    "Thanks for watching",
    "Takk for ating med",
    "Subtitles by",
    "Please subscribe",
    "請不吝點贊",  # 請不吝點贊
]

DEFAULT_FILLERS = ["um", "uh", "erm", "嗯", "呃"]

OLD_PER_LANGUAGE = {  # the order version 2.1.0 kept in settings.json
    "en": ["whisper-large-v3-turbo", "parakeet-tdt-0.6b-v3-int8", "whisper-small"],
    "fr": ["whisper-large-v3-turbo", "parakeet-tdt-0.6b-v3-int8", "whisper-small"],
    "yue": ["sensevoice-small-int8", "whisper-large-v3", "whisper-large-v3-turbo"],
    "zh": ["sensevoice-small-int8", "whisper-large-v3-turbo", "whisper-small"],
}

DEFAULTS = {
    # General (section 5)
    "general.data_folder": "data",
    "general.output_folder": "",          # "" = Documents\SOT Transcripts
    "general.save_next_to_media": False,
    "general.theme": "system",            # system / light / dark
    "general.font_size": 11,
    "general.always_on_top": False,
    "general.remember_recent": True,
    "general.open_last": False,
    "general.keep_awake": True,
    "general.background_priority": False,
    "general.notify": True,
    "general.ask_unsaved": True,
    "general.recent": [],
    "general.geometry": "",
    "general.last_options_tab": 0,
    "general.first_run_done": False,
    # Proofreading (player)
    "general.follow_playback": True,      # highlight the line being played and scroll with it
    "general.playback_speed": 1.0,        # 0.5 / 0.75 / 1.0 / 1.25 / 1.5
    # Transcription (section 6)
    "transcription.language": "auto",     # auto / en / fr / yue / zh
    "transcription.model": "auto",        # auto or a model key
    "transcription.per_language": {},    # your own Automatic order per language; empty = the model list's
    "transcription.speed": "fast",        # fast (beam 1) / accurate (beam 5)
    "transcription.threads": 0,           # 0 = all cores
    "transcription.script_yue": "hk",     # hk / tw / cn
    "transcription.script_zh": "cn",
    "transcription.download_folder": "models",
    "transcription.locations": [],
    "transcription.location_cache": {},
    # Vocabulary (section 8)
    "vocabulary.active": "Default",
    "vocabulary.sets": {"Default": {"scope": "all", "hotwords": [], "replacements": []}},
    # Output (section 9)
    "output.autosave": "session+exports",  # off / session / session+exports
    "output.formats": ["txt_times", "srt"],
    "output.save_to": "output",            # output / media / ask
    "output.pattern": "{name}_stt_{model}",
    "output.if_exists": "number",          # ask / number / overwrite
    "output.times": "paragraph",           # none / paragraph / segment
    "output.time_format": "hh:mm:ss",      # hh:mm:ss / mm:ss / hh:mm:ss.mmm
    "output.para_pause": 2.0,
    "output.para_chars": 600,
    "output.sub_chars": 42,
    "output.sub_lines": 2,
    "output.sub_max": 7.0,
    "output.sub_min": 1.0,
    "output.encoding": "utf-8",            # utf-8 / utf-8-sig
    "output.stats_header": False,
    "output.page_size": "Letter",
    "output.remove_fillers": False,
    "output.filler_list": DEFAULT_FILLERS,
    # Advanced (section 10)
    "advanced.beam": 0,                    # 0 = follow Speed
    "advanced.temperature_fallback": True,
    "advanced.compression_ratio": 2.4,
    "advanced.log_prob": -1.0,
    "advanced.no_speech": 0.6,
    "advanced.carry_previous": False,
    "advanced.repetition_penalty": 1.0,
    "advanced.word_timestamps": False,
    "advanced.skip_silence": 2.0,
    "advanced.vad_threshold": 0.5,
    "advanced.vad_min_silence_ms": 500,
    "advanced.vad_pad_ms": 300,
    "advanced.max_piece_s": 15,
    "advanced.normalise": True,
    "advanced.target_dbfs": -23.0,
    "advanced.max_gain_db": 35.0,
    "advanced.stereo": "average",          # average / left / right
    "advanced.noise_reduction": "off",     # off / light / strong (FFmpeg highpass + afftdn)
    "advanced.noise_in_player": False,     # hear the noise reduction in the player
    "advanced.filter_known": True,
    "advanced.filter_list": DEFAULT_FILTER_PHRASES,
    "advanced.repeat_limit": 3,
    "advanced.compute_type": "int8",       # int8 / int8_float32 / float32
    "advanced.keep_loaded": True,
    "advanced.priority": "normal",
    "advanced.offline": False,             # turned on automatically once a model is installed
    "advanced.offline_auto_done": False,
    "advanced.proxy": "system",
}

GROUPS = {
    "Decoding": ["advanced.beam", "advanced.temperature_fallback", "advanced.compression_ratio", "advanced.log_prob",
                 "advanced.no_speech", "advanced.carry_previous", "advanced.repetition_penalty",
                 "advanced.word_timestamps", "advanced.skip_silence"],
    "Voice detection": ["advanced.vad_threshold", "advanced.vad_min_silence_ms", "advanced.vad_pad_ms",
                        "advanced.max_piece_s"],
    "Audio": ["advanced.normalise", "advanced.target_dbfs", "advanced.max_gain_db", "advanced.stereo",
              "advanced.noise_reduction", "advanced.noise_in_player"],
    "Invented-text filter": ["advanced.filter_known", "advanced.filter_list", "advanced.repeat_limit"],
    "Performance": ["advanced.compute_type", "advanced.keep_loaded", "advanced.priority"],
    "Network": ["advanced.offline", "advanced.proxy"],
}


class Settings:
    def __init__(self, path=None, values=None):
        self.values = copy.deepcopy(DEFAULTS)
        if values is not None:  # worker process: a snapshot, never saved
            self.path = None
            self.values.update(values)
            return
        self.path = path or os.path.join(paths.settings_dir(), "settings.json")
        self.load()

    # ---------------------------------------------------------------- basics
    def get(self, key):
        if key in self.values:
            return self.values[key]
        return copy.deepcopy(DEFAULTS.get(key))

    def set(self, key, value):
        self.values[key] = value

    def is_default(self, key):
        return self.values.get(key) == DEFAULTS.get(key)

    def restore(self, keys=None):
        for k in (keys or list(DEFAULTS)):
            if k in ("general.recent", "general.geometry", "general.first_run_done",
                     "transcription.locations", "transcription.download_folder", "general.data_folder"):
                if keys is None:
                    continue  # never lose locations or recent files on a full restore
            self.values[k] = copy.deepcopy(DEFAULTS[k])

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError):
            return
        self.apply_dict(stored.get("values", stored))

    def apply_dict(self, stored):
        if stored.get("transcription.per_language") == OLD_PER_LANGUAGE:
            stored = dict(stored)  # 2.1.0 saved its built-in order; follow the model list instead
            stored["transcription.per_language"] = {}
        for k, v in stored.items():
            if k in DEFAULTS and (DEFAULTS[k] is None or isinstance(v, type(DEFAULTS[k]))
                                  or (isinstance(DEFAULTS[k], float) and isinstance(v, int))):
                self.values[k] = v

    def save(self):
        """Atomic write: temp file then replace (FR-PKG settings)."""
        folder = os.path.dirname(self.path)
        os.makedirs(folder, exist_ok=True)
        data = {"app": "SecureOfflineTranscriber", "format": 1, "values": self.values}
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".settings", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def export_to(self, path):
        skip = ("general.recent", "general.geometry")
        data = {"app": "SecureOfflineTranscriber", "format": 1,
                "values": {k: v for k, v in self.values.items() if k not in skip}}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)

    def import_from(self, path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("app") != "SecureOfflineTranscriber":
            raise ValueError("This is not a Secure Offline Transcriber settings file.")
        self.apply_dict(data.get("values", {}))

    # ---------------------------------------------------------------- derived
    def data_folder(self):
        stored = self.get("general.data_folder")
        if stored in ("", "data"):
            return paths.settings_dir()  # data\ in the portable folder (or the fallback)
        return paths.from_stored(stored)

    def download_folder(self):
        if paths.using_fallback() and self.get("transcription.download_folder") in ("", "models"):
            return os.path.join(paths.fallback_dir(), "models")
        return paths.from_stored(self.get("transcription.download_folder"), "models")

    def output_folder(self):
        return paths.from_stored(self.get("general.output_folder")) or paths.default_output_folder()

    def locations(self):
        return [paths.from_stored(p) for p in self.get("transcription.locations")]

    def vocabulary(self, language=None):
        """Hotwords and replacements of the active set, if it applies to this language."""
        sets = self.get("vocabulary.sets") or {}
        vs = sets.get(self.get("vocabulary.active")) or {}
        scope = vs.get("scope", "all")
        if language and scope not in ("all", language):
            return [], []
        return list(vs.get("hotwords", [])), list(vs.get("replacements", []))

    def add_recent(self, path):
        if not self.get("general.remember_recent"):
            return
        rec = [p for p in self.get("general.recent") if os.path.normcase(p) != os.path.normcase(path)]
        self.values["general.recent"] = [path] + rec[:9]
