"""Main window (sections 2-4): menus, Media input with queue and player, Transcript / Raw output tabs,
status line and statistics bar. Recognition runs in the worker process (sot.worker)."""
import datetime
import html
import logging
import os
import time

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (QAction, QActionGroup, QDesktopServices, QFont, QFontDatabase, QIcon, QKeySequence, QShortcut,
                           QTextCursor)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QFileDialog, QFrame, QGroupBox,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMainWindow, QMenu, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QSlider, QSystemTrayIcon, QTableWidget,
                               QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout, QWidget, QStyle,
                               QSizePolicy)

from .. import APP_NAME, APP_TITLE, SESSION_EXT, VERSION, audioproc, exporters, media, paths, postproc, session, stats, system
from ..modelmgr import LANG_NAMES, human_size
from ..settings import DEFAULTS
from .dialogs import (AboutDialog, ExportDialog, FindReplaceDialog, FirstRunDialog, ReportDialog,
                      ShortcutsDialog, file_properties_text, parse_time)
from .options import HOTWORD_LIMIT, LANGS, OptionsDialog, run_with_progress
from .player import Player
from .transcript import TranscriptEdit

log = logging.getLogger("window")
MEDIA_FILTER = "Audio and video (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma *.mp4 *.mkv *.mov *.avi *.webm);;All files (*)"
SPEEDS = [(0.5, "0.5\u00d7"), (0.75, "0.75\u00d7"), (1.0, "1\u00d7"), (1.25, "1.25\u00d7"), (1.5, "1.5\u00d7")]
NOISE = [("off", "Off"), ("light", "Light"), ("strong", "Strong")]
MEDIA_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".mp4", ".mkv", ".mov", ".avi", ".webm"}


class MainWindow(QMainWindow):
    def __init__(self, settings, manager, worker, icon_path):
        super().__init__()
        self.settings = settings
        self.manager = manager
        self.worker = worker
        self.icon_path = icon_path
        self.setWindowIcon(QIcon(icon_path))
        self.setAcceptDrops(True)
        # state
        self.info = None          # probe() of the open file
        self.track = 0
        self.segments = []        # cleaned segments of the last job (with "orig")
        self.raw_lines = []
        self.cleanup_log = []
        self.stats = {}
        self.job = None           # running job description
        self.job_t0 = 0.0
        self.job_segments = []
        self.queue = []           # [{"path", "info", "status", "error"}]
        self.queue_totals = None
        self.session_path = None
        self.media_sha = None
        self.stop_all = False
        self.find_dlg = None
        self.last_find = None
        self.tray = None
        self.job_meta = {}
        self.shown_blocks = []
        self.resume = None        # while a resumed job runs: the text and statistics from before
        self.autosave_paths = {}  # files the last auto-save wrote, overwritten when a stopped job is resumed
        self._last_hl = None
        self._journal_t = 0.0
        self._build_ui()
        self._build_menus()
        self.manager.before_update = self.worker.unload  # Windows cannot replace a loaded model's files
        self.player = Player()
        self.player.speed = float(settings.get("general.playback_speed"))
        self.player.denoise = settings.get("advanced.noise_reduction") if settings.get("advanced.noise_in_player") else "off"
        self.player.state_changed.connect(self._player_state)
        self.player.error.connect(lambda m: self.status_msg(m))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(100)
        self.apply_settings()
        self.refresh_models()
        self.update_title()
        self.update_actions()
        geo = settings.get("general.geometry")
        if geo:
            from PySide6.QtCore import QByteArray
            self.restoreGeometry(QByteArray.fromHex(geo.encode()))
        else:
            self.resize(1100, 760)

    # ================================================================== UI
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(10, 8, 10, 6)
        # ---- Media input
        box = QGroupBox("Media input")
        bv = QVBoxLayout(box)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open…")
        self.open_btn.setToolTip("Open a recording (Ctrl+O). You can also drop files on the window.")
        self.open_btn.clicked.connect(self.open_media)
        row.addWidget(self.open_btn)
        self.info_label = QLabel("No file open. Drop a recording here.")
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # the button keeps its full text
        self.info_label.setMinimumWidth(120)
        row.addWidget(self.info_label, 1)
        self.track_combo = QComboBox()
        self.track_combo.setVisible(False)
        self.track_combo.currentIndexChanged.connect(self._track_changed)
        row.addWidget(self.track_combo)
        self.go_btn = QPushButton("Transcribe")
        self.go_btn.setMinimumHeight(34)
        f = self.go_btn.font()
        f.setBold(True)
        self.go_btn.setFont(f)
        self.go_btn.clicked.connect(self.transcribe_or_stop)
        row.addWidget(self.go_btn)
        bv.addLayout(row)
        # queue (FR-Q-02)
        self.queue_box = QWidget()
        qh = QHBoxLayout(self.queue_box)
        qh.setContentsMargins(0, 0, 0, 0)
        self.queue_table = QTableWidget(0, 4)
        self.queue_table.setHorizontalHeaderLabels(["#", "Name", "Length", "Status"])
        self.queue_table.verticalHeader().hide()
        self.queue_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.queue_table.setMaximumHeight(130)
        self.queue_table.cellDoubleClicked.connect(self._queue_open)
        qh.addWidget(self.queue_table, 1)
        qb = QVBoxLayout()
        for text, slot in (("Remove", self.queue_remove), ("Move up", lambda: self.queue_move(-1)),
                           ("Move down", lambda: self.queue_move(1))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            qb.addWidget(b)
        qb.addStretch(1)
        qh.addLayout(qb)
        self.queue_box.setVisible(False)
        bv.addWidget(self.queue_box)
        # player (FR-UI-04)
        prow = QHBoxLayout()
        st = self.style()
        self.play_btn = QToolButton()
        self.play_btn.setIcon(st.standardIcon(QStyle.SP_MediaPlay))
        self.play_btn.setToolTip("Play / Pause (F9, or Space outside the text)")
        self.play_btn.clicked.connect(self.play_pause)
        self.back_btn = QToolButton()
        self.back_btn.setIcon(st.standardIcon(QStyle.SP_MediaSeekBackward))
        self.back_btn.setToolTip("Skip back 5 s (Ctrl+Left)")
        self.back_btn.clicked.connect(lambda: self.skip(-5))
        self.fwd_btn = QToolButton()
        self.fwd_btn.setIcon(st.standardIcon(QStyle.SP_MediaSeekForward))
        self.fwd_btn.setToolTip("Skip forward 5 s (Ctrl+Right)")
        self.fwd_btn.clicked.connect(lambda: self.skip(5))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderReleased.connect(self._slider_seek)
        self.slider.sliderPressed.connect(lambda: setattr(self, "_dragging", True))
        self._dragging = False
        self.pos_label = QLabel("00:00:00 / 00:00:00")
        self.speed_combo = QComboBox()
        for sp, label in SPEEDS:
            self.speed_combo.addItem(label, sp)
        self.speed_combo.setToolTip("Playback speed (the pitch stays natural). 0.75\u00d7 helps when checking fast speech.")
        self.speed_combo.currentIndexChanged.connect(lambda _: self.set_playback_speed(self.speed_combo.currentData()))
        self.follow_btn = QToolButton()
        self.follow_btn.setText("Follow")
        self.follow_btn.setCheckable(True)
        self.follow_btn.setToolTip("Follow playback: the transcript scrolls with the audio and the sentence being "
                                   "played is highlighted. F6 replays the sentence, F7/F8 go to unsure (amber) lines.")
        self.follow_btn.toggled.connect(lambda on: self._set_quick("general.follow_playback", on))
        for w in (self.back_btn, self.play_btn, self.fwd_btn):
            prow.addWidget(w)
        prow.addWidget(self.slider, 1)
        prow.addWidget(self.pos_label)
        prow.addWidget(self.speed_combo)
        prow.addWidget(self.follow_btn)
        bv.addLayout(prow)
        v.addWidget(box)
        # ---- tabs
        self.tabs = QTabWidget()
        self.editor = TranscriptEdit()
        self.editor.time_clicked.connect(self.play_from)
        self.editor.textChanged.connect(self._edited)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Consolas") if os.name == "nt" else QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setStyleHint(QFont.Monospace)
        self.raw.setFont(mono)
        self.tabs.addTab(self.editor, "Transcript")
        self.tabs.addTab(self.raw, "Raw output")
        self.tabs.currentChanged.connect(lambda _: self.update_actions())
        v.addWidget(self.tabs, 1)
        # ---- status line (FR-STAT-02)
        srow = QHBoxLayout()
        self.stage_label = QLabel("Ready")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(12)
        self.progress.setMaximumWidth(260)
        self.progress.setVisible(False)
        srow.addWidget(self.stage_label, 1)
        srow.addWidget(self.progress)
        v.addLayout(srow)
        # ---- statistics bar (section 11)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        v.addWidget(line)
        self.stats_label = QLabel(" ")
        self.stats_label.setContextMenuPolicy(Qt.CustomContextMenu)
        self.stats_label.customContextMenuRequested.connect(self._stats_menu)
        self.stats_label.setStyleSheet("color: palette(placeholder-text);")
        v.addWidget(self.stats_label)

    def _act(self, menu, text, slot=None, shortcut=None, checkable=False, tip=None):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if checkable:
            a.setCheckable(True)
            if slot:
                a.toggled.connect(slot)
        elif slot:
            a.triggered.connect(slot)
        if tip:
            a.setStatusTip(tip)
        menu.addAction(a)
        return a

    def _build_menus(self):
        mb = self.menuBar()
        # File
        m = mb.addMenu("&File")
        self.a_open = self._act(m, "&Open Media…", self.open_media, "Ctrl+O")
        self.recent_menu = m.addMenu("Open &Recent")
        self.recent_menu.aboutToShow.connect(self._fill_recent)
        self._act(m, "Open &Transcript…", self.open_session)
        self._act(m, "Open Output &Folder", self.open_output_folder, "Ctrl+Shift+O")
        m.addSeparator()
        self.a_save = self._act(m, "&Save Transcript", self.save_session, "Ctrl+S")
        self.a_save_as = self._act(m, "Save Transcript &As…", self.save_session_as, "Ctrl+Shift+S")
        self.a_export = self._act(m, "&Export…", self.export_dialog, "Ctrl+E")
        self.a_copy_tr = self._act(m, "&Copy Transcript", self.copy_transcript, "Ctrl+Shift+C")
        m.addSeparator()
        self.a_props = self._act(m, "File &Properties…", self.file_properties, "Alt+Return")
        self.a_close = self._act(m, "C&lose", self.close_file, "Ctrl+W")
        self._act(m, "E&xit", self.close, "Ctrl+Q")
        # Edit
        m = mb.addMenu("&Edit")
        self.a_undo = self._act(m, "&Undo", lambda: self.editor.undo(), "Ctrl+Z")
        self.a_redo = self._act(m, "&Redo", lambda: self.editor.redo(), "Ctrl+Y")
        m.addSeparator()
        self.a_cut = self._act(m, "Cu&t", lambda: self._active().cut(), "Ctrl+X")
        self.a_copy = self._act(m, "&Copy", lambda: self._active().copy(), "Ctrl+C")
        self.a_paste = self._act(m, "&Paste", lambda: self._active().paste(), "Ctrl+V")
        self.a_selall = self._act(m, "Select &All", lambda: self._active().selectAll(), "Ctrl+A")
        m.addSeparator()
        self._act(m, "&Find…", lambda: self.find_dialog(False), "Ctrl+F")
        self._act(m, "Find &Next", self.find_next, "F3")
        self._act(m, "R&eplace…", lambda: self.find_dialog(True), "Ctrl+H")
        m.addSeparator()
        self.a_add_vocab = self._act(m, "Add Selection to &Vocabulary…", self.add_selection_to_vocab)
        self._act(m, "Add &Names from Documents…", self.names_from_documents)
        self.a_apply_vocab = self._act(m, "Apply Vocabulary &Replacements", self.apply_vocab)
        self._act(m, "&Go to Time…", self.go_to_time, "Ctrl+G")
        for a in (self.a_cut, self.a_copy, self.a_paste, self.a_selall, self.a_undo, self.a_redo):
            a.setShortcutContext(Qt.WidgetWithChildrenShortcut)  # let the text widgets handle their own keys
        # Transcribe
        m = mb.addMenu("&Transcribe")
        self.a_go = self._act(m, "&Transcribe / Resume", self.transcribe, "F5")
        self.a_restart = self._act(m, "Start &Again from the Beginning", self.start_again)
        self.a_stop = self._act(m, "&Stop", self.stop, "Esc")
        m.addSeparator()
        self.a_play = self._act(m, "&Play / Pause", self.play_pause, "F9")
        self.a_replay = self._act(m, "&Replay Sentence", self.replay_sentence, "F6")
        self.a_prev_unsure = self._act(m, "Previous &Unsure Line", lambda: self.unsure_line(-1), "F7")
        self.a_next_unsure = self._act(m, "&Next Unsure Line", lambda: self.unsure_line(1), "F8")
        self._act(m, "Skip &Back 5 s\tCtrl+Left", lambda: self.skip(-5))
        self._act(m, "Skip &Forward 5 s\tCtrl+Right", lambda: self.skip(5))
        self.pspeed_menu = m.addMenu("Playback Spee&d")
        self.pspeed_group = QActionGroup(self)
        for v, label in SPEEDS:
            a = QAction(label, self, checkable=True)
            a.setData(v)
            a.triggered.connect(lambda _=False, x=v: self.set_playback_speed(x))
            self.pspeed_group.addAction(a)
            self.pspeed_menu.addAction(a)
        self.a_follow = self._act(m, "Follow Pla&yback", lambda on: self._set_quick("general.follow_playback", on),
                                  checkable=True)
        for key, slot in (("Space", self.play_pause), ("Ctrl+Left", lambda: self.skip(-5)),
                          ("Ctrl+Right", lambda: self.skip(5))):
            QShortcut(QKeySequence(key), self, slot)  # editable text claims these keys first (ShortcutOverride)
        m.addSeparator()
        self.lang_menu = m.addMenu("&Language")
        self.lang_group = QActionGroup(self)
        for key, name in LANGS:
            a = QAction(name, self, checkable=True)
            a.setData(key)
            a.triggered.connect(lambda _=False, k=key: self._set_quick("transcription.language", k))
            self.lang_group.addAction(a)
            self.lang_menu.addAction(a)
        self.model_menu = m.addMenu("&Model")
        self.model_menu.aboutToShow.connect(self._fill_model_menu)
        self.speed_menu = m.addMenu("&Speed")
        self.speed_group = QActionGroup(self)
        for key, name in (("fast", "Fast (beam 1)"), ("accurate", "Accurate (beam 5)")):
            a = QAction(name, self, checkable=True)
            a.setData(key)
            a.triggered.connect(lambda _=False, k=key: self._set_quick("transcription.speed", k))
            self.speed_group.addAction(a)
            self.speed_menu.addAction(a)
        self.noise_menu = m.addMenu("&Noise Reduction")
        self.noise_group = QActionGroup(self)
        for key, label in NOISE:
            a = QAction(label, self, checkable=True)
            a.setData(key)
            a.triggered.connect(lambda _=False, k=key: self._set_quick("advanced.noise_reduction", k))
            self.noise_group.addAction(a)
            self.noise_menu.addAction(a)
        self.noise_menu.addSeparator()
        self.a_noise_play = QAction("Hear It in the Player", self, checkable=True)
        self.a_noise_play.toggled.connect(lambda on: self._set_quick("advanced.noise_in_player", on))
        self.noise_menu.addAction(self.a_noise_play)
        self.vocab_menu = m.addMenu("&Vocabulary Set")
        self.vocab_menu.aboutToShow.connect(self._fill_vocab_menu)
        m.addSeparator()
        self._act(m, "&Add Files to Queue…", self.add_to_queue)
        # Options
        m = mb.addMenu("&Options")
        for i, name in enumerate(["General", "Transcription", "Vocabulary", "Output", "Advanced"]):
            self._act(m, f"&{name}…", lambda _=False, t=i: self.options(t))
        m.addSeparator()
        self.a_offline = self._act(m, "Offline &Mode", self._toggle_offline, checkable=True)
        self.a_autosave = self._act(m, "Auto-save &Transcript", self._toggle_autosave, checkable=True)
        self.a_awake = self._act(m, "&Keep PC Awake During Jobs",
                                 lambda on: self._set_quick("general.keep_awake", on), checkable=True)
        self.a_top = self._act(m, "Always on &Top", self._toggle_top, checkable=True)
        m.addSeparator()
        self._act(m, "All &Settings…", lambda: self.options(self.settings.get("general.last_options_tab")), "Ctrl+,")
        # Help
        m = mb.addMenu("&Help")
        self._act(m, "&Help Contents", self.help_contents, "F1")
        self._act(m, "&Keyboard Shortcuts", lambda: ShortcutsDialog(self).exec(), "Ctrl+/")
        self._act(m, "Open &Log Folder", lambda: system.open_folder(os.path.join(self.settings.data_folder(), "logs")))
        self._act(m, "Check for &Updates", self.check_updates)
        m.addSeparator()
        self._act(m, "&About", lambda: AboutDialog(self, self.icon_path).exec())

    def _active(self):
        return self.editor if self.tabs.currentIndex() == 0 else self.raw

    # ================================================================== settings in the window
    def apply_settings(self):
        s = self.settings
        f = self.editor.font()
        f.setPointSize(int(s.get("general.font_size")))
        self.editor.setFont(f)
        for a in self.lang_group.actions():
            a.setChecked(a.data() == s.get("transcription.language"))
        for a in self.speed_group.actions():
            a.setChecked(a.data() == s.get("transcription.speed"))
        for a in self.noise_group.actions():
            a.setChecked(a.data() == s.get("advanced.noise_reduction"))
        sp = float(s.get("general.playback_speed"))
        for a in self.pspeed_group.actions():
            a.setChecked(abs(a.data() - sp) < 1e-3)
        self.speed_combo.blockSignals(True)
        self.speed_combo.setCurrentIndex(max(0, self.speed_combo.findData(sp)))
        self.speed_combo.blockSignals(False)
        for w, on in ((self.a_follow, s.get("general.follow_playback")), (self.follow_btn, s.get("general.follow_playback")),
                      (self.a_noise_play, s.get("advanced.noise_in_player"))):
            w.blockSignals(True)
            w.setChecked(bool(on))
            w.blockSignals(False)
        if hasattr(self, "player"):
            self.player.set_options(speed=sp, denoise=s.get("advanced.noise_reduction")
                                    if s.get("advanced.noise_in_player") else "off")
        for a, on in ((self.a_offline, s.get("advanced.offline")), (self.a_autosave, s.get("output.autosave") != "off"),
                      (self.a_awake, s.get("general.keep_awake")), (self.a_top, s.get("general.always_on_top"))):
            a.blockSignals(True)
            a.setChecked(bool(on))
            a.blockSignals(False)
        top = bool(s.get("general.always_on_top"))
        if bool(self.windowFlags() & Qt.WindowStaysOnTopHint) != top:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, top)
            self.show()
        apply_theme(s.get("general.theme"))
        self.update_go_label()

    def _set_quick(self, key, value):
        self.settings.set(key, value)
        self._save_settings()
        self.apply_settings()
        self.update_actions()

    def _save_settings(self):
        try:
            self.settings.save()
        except OSError as ex:
            self.status_msg(f"Settings could not be saved: {ex}")

    def _toggle_offline(self, on):
        self._set_quick("advanced.offline", on)
        paths_note = "Offline Mode is on: no network call is possible." if on else \
            "Offline Mode is off: the network is used only when you start a model download."
        self.status_msg(paths_note)

    def _toggle_autosave(self, on):
        self._set_quick("output.autosave", "session+exports" if on else "off")

    def _toggle_top(self, on):
        self._set_quick("general.always_on_top", on)

    def options(self, tab=0, focus_model=None, vocab_heard=None):
        dlg = OptionsDialog(self, self.settings, self.manager, tab, focus_model, vocab_heard)
        dlg.applied.connect(self._options_applied)
        dlg.show_first_run.connect(lambda: QTimer.singleShot(0, self.first_run))
        dlg.exec()
        self.refresh_models()
        self.apply_settings()
        self.update_actions()

    def _options_applied(self):
        new_data = self.settings.data_folder()
        if new_data != getattr(self.worker, "data_folder", new_data):
            paths.setup_logging(new_data)
        self.apply_settings()
        self.refresh_models()

    def refresh_models(self):
        self.manager.scan()
        if self.manager.installed() and not self.settings.get("advanced.offline_auto_done"):
            # Network: "On once a model is installed" (section 10)
            self.settings.set("advanced.offline", True)
            self.settings.set("advanced.offline_auto_done", True)
            self._save_settings()
            self.apply_settings()
            self.status_msg("A model is installed, so Offline Mode is now on. Turn it off in the Options menu to "
                            "download more models.")
        self.update_go_label()

    def _fill_model_menu(self):
        self.model_menu.clear()
        g = QActionGroup(self.model_menu)
        cur = self.settings.get("transcription.model")
        items = [("auto", "Automatic")] + [(m.key, m.name) for m in sorted(self.manager.installed(), key=lambda m: m.name)]
        if cur != "auto" and not any(k == cur for k, _ in items):
            cur = "auto"
        for key, name in items:
            a = QAction(name, self.model_menu, checkable=True)
            a.setChecked(key == cur)
            a.triggered.connect(lambda _=False, k=key: self._set_quick("transcription.model", k))
            g.addAction(a)
            self.model_menu.addAction(a)
        if len(items) == 1:
            self.model_menu.addSeparator()
            self.model_menu.addAction("Install models…", lambda: self.options(1))

    def _fill_vocab_menu(self):
        self.vocab_menu.clear()
        g = QActionGroup(self.vocab_menu)
        for name in self.settings.get("vocabulary.sets"):
            a = QAction(name, self.vocab_menu, checkable=True)
            a.setChecked(name == self.settings.get("vocabulary.active"))
            a.triggered.connect(lambda _=False, n=name: self._set_quick("vocabulary.active", n))
            g.addAction(a)
            self.vocab_menu.addAction(a)
        self.vocab_menu.addSeparator()
        self.vocab_menu.addAction("Edit Vocabulary…", lambda: self.options(2))

    def _fill_recent(self):
        self.recent_menu.clear()
        rec = [p for p in self.settings.get("general.recent")]
        for p in rec:
            a = self.recent_menu.addAction(os.path.basename(p), lambda _=False, x=p: self.open_path(x))
            a.setStatusTip(p)
            a.setToolTip(p)
            a.setEnabled(os.path.exists(p))
        if not rec:
            self.recent_menu.addAction("(empty)").setEnabled(False)
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Clear List", lambda: self._set_quick("general.recent", []))

    # ================================================================== model choice (section 6)
    def model_dict(self, m):
        return {"key": m.key, "kind": m.kind, "folder": m.folder, "name": m.name, "short": m.short,
                "languages": list(m.languages)}

    def plan(self):
        """Returns (model ModelInfo or None, language, lid ModelInfo or None, error text)."""
        s = self.settings
        lang = s.get("transcription.language")
        key = s.get("transcription.model")
        if key != "auto":
            m = self.manager.find(key)
            if not m or not m.installed:
                return None, lang, None, ("not_installed", key)
            if lang != "auto" and lang not in m.languages:
                return None, lang, None, ("language", f"{m.name} cannot transcribe {LANG_NAMES[lang]}.")
            lid = self.manager.lid_model() if lang == "auto" else None
            return m, lang, lid, None
        if not self.manager.installed():
            return None, lang, None, ("none", None)
        if lang != "auto":
            c = self.manager.candidates(lang)
            if not c:
                return None, lang, None, ("language", f"No installed model can transcribe {LANG_NAMES[lang]}. "
                                          "Open Options > Transcription to download one.")
            return c[0], lang, None, None
        lid = self.manager.lid_model()
        inst = self.manager.installed()
        return (inst[0] if len(inst) == 1 else None), lang, lid, None

    def update_go_label(self):
        if self.job:
            self.go_btn.setText("Stop")
            return
        rp = self.resume_point()
        if rp is not None:
            jm = self.job_meta or {}
            mname = (jm.get("model") or {}).get("name") or "the same model"
            lname = LANG_NAMES.get(jm.get("language"), jm.get("language") or "")
            self.go_btn.setText(f"Resume from {media.fmt_clock(rp)} \u2013 {mname}" + (f" \u00b7 {lname}" if lname else ""))
            self.go_btn.setToolTip("Carry on from where the job stopped and keep the text done so far (F5). "
                                   "Transcribe > Start Again from the Beginning starts over.")
            return
        m, lang, lid, err = self.plan()
        lname = LANG_NAMES.get(lang, lang)
        if self.settings.get("transcription.model") == "auto":
            pick = m.name if m else ("by language" if self.manager.installed() else "no model installed")
            label = f"Automatic ({pick})"
        else:
            mm = self.manager.find(self.settings.get("transcription.model"))
            label = mm.name if mm else self.settings.get("transcription.model")
        self.go_btn.setText(f"Transcribe – {label} · {lname}")
        self.go_btn.setToolTip("Start (F5). The model and language that will be used are shown on the button.")

    # ================================================================== files
    def open_media(self):
        start = os.path.dirname(self.info["path"]) if self.info else ""
        files, _ = QFileDialog.getOpenFileNames(self, "Open Media", start, MEDIA_FILTER)
        if files:
            self.open_paths(files)

    def open_paths(self, files):
        files = [f for f in files if os.path.isfile(f)]
        if not files:
            return
        from .. import modellist
        lists = [f for f in files if modellist.is_list_file(f)]
        if lists:  # a model list file someone sent: offer to use it
            if QMessageBox.question(self, "Model list", f"{os.path.basename(lists[0])} is a model list. Use it for "
                                    "Options > Transcription?") == QMessageBox.Yes:
                from .options import import_model_list
                if import_model_list(self, self.manager, lists[0]):
                    self.refresh_models()
            files = [f for f in files if f not in lists]
            if not files:
                return
        sessions = [f for f in files if f.lower().endswith(SESSION_EXT)]
        if sessions:
            self.open_session(sessions[0])
            return
        if len(files) == 1 and not self.queue and not self.job:
            self.open_path(files[0])
        else:
            self.enqueue(files)

    def open_path(self, path, keep_text=False):
        if self.job:
            self.status_msg("Wait for the job to finish, or add the file to the queue.")
            self.enqueue([path])
            return False
        if not keep_text and not self.confirm_unsaved():
            return False
        try:
            info = media.probe(path)
        except Exception as ex:
            QMessageBox.warning(self, "Open Media", f"{os.path.basename(path)} cannot be opened:\n{ex}")
            return False
        if not info["audio"]:
            QMessageBox.warning(self, "Open Media", f"{os.path.basename(path)} has no audio track.")
            return False
        self.info = info
        self.media_sha = None
        self.track_combo.blockSignals(True)
        self.track_combo.clear()
        for i, a in enumerate(info["audio"]):
            self.track_combo.addItem(f"Track {i + 1}: {a['codec']} {a['channels']}" + (f" ({a['lang']})" if a["lang"] else ""))
        self.track_combo.setVisible(len(info["audio"]) > 1)
        self.track_combo.blockSignals(False)
        self.track = 0
        self.info_label.setText(f"<b>{html.escape(os.path.basename(path))}</b> · {html.escape(media.info_line(info))}")
        self.info_label.setToolTip(path)
        self.player.load(path, 0, info["duration"])
        if not keep_text:
            self.clear_results()
            self.session_path = None
        self.settings.add_recent(path)
        self._save_settings()
        self.update_title()
        self.update_actions()
        self._update_pos()
        return True

    def _track_changed(self, i):
        self.track = max(0, i)
        if self.info:
            self.player.load(self.info["path"], self.track, self.info["duration"])

    def clear_results(self):
        self.editor.set_blocks([])
        self.shown_blocks = []
        self.resume = None
        self.autosave_paths = {}
        self.job_meta = {}
        self.raw.clear()
        self.segments = []
        self.raw_lines = []
        self.cleanup_log = []
        self.stats = {}
        self.stats_label.setText(" ")
        self.stats_label.setToolTip("")
        self.editor.document().setModified(False)

    def close_file(self):
        if self.job:
            self.status_msg("Stop the job first.")
            return
        if not self.confirm_unsaved():
            return
        self.player.stop()
        self.info = None
        self.player.load(None)
        self.clear_results()
        self.session_path = None
        self.info_label.setText("No file open. Drop a recording here.")
        self.track_combo.setVisible(False)
        self.update_title()
        self.update_actions()
        self._update_pos()

    def confirm_unsaved(self):
        if not self.editor.document().isModified() or not self.settings.get("general.ask_unsaved"):
            return True
        r = QMessageBox.question(self, APP_NAME, "The transcript has unsaved edits. Save them?",
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.save_session()
        return True

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        self.open_paths(files)

    def open_output_folder(self):
        system.open_folder(self.output_dir() or self.settings.output_folder())

    def file_properties(self):
        if self.info:
            ReportDialog(self, "File Properties", file_properties_text(self.info)).exec()

    # ================================================================== queue (FR-Q)
    def add_to_queue(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add Files to Queue", "", MEDIA_FILTER)
        if files:
            self.enqueue(files)

    def enqueue(self, files):
        if not self.queue and self.info and not self.job and not self.segments:
            self.queue.append({"path": self.info["path"], "info": self.info, "status": "Waiting", "error": ""})
        for f in files:
            if any(os.path.normcase(q["path"]) == os.path.normcase(f) and q["status"] == "Waiting" for q in self.queue):
                continue
            try:
                info = media.probe(f)
            except Exception as ex:
                self.queue.append({"path": f, "info": None, "status": "Failed", "error": str(ex)})
                continue
            self.queue.append({"path": f, "info": info, "status": "Waiting", "error": ""})
        if not self.info and self.queue and self.queue[0]["info"]:
            self.open_path(self.queue[0]["path"], keep_text=True)
        self.refresh_queue()

    def refresh_queue(self):
        self.queue_box.setVisible(len(self.queue) >= 2)
        self.queue_table.setRowCount(len(self.queue))
        for r, q in enumerate(self.queue):
            dur = media.fmt_clock(q["info"]["duration"]) if q["info"] else "-"
            vals = [str(r + 1), os.path.basename(q["path"]), dur, q["status"]]
            for c, t in enumerate(vals):
                it = QTableWidgetItem(t)
                if c == 3 and q["error"]:
                    it.setToolTip(q["error"])
                if c == 1:
                    it.setToolTip(q["path"])
                self.queue_table.setItem(r, c, it)
        self.queue_table.resizeColumnToContents(0)

    def queue_remove(self):
        rows = sorted({i.row() for i in self.queue_table.selectedIndexes()}, reverse=True)
        for r in rows:
            if self.queue[r]["status"].startswith("Transcribing"):
                continue
            del self.queue[r]
        self.refresh_queue()

    def queue_move(self, d):
        r = self.queue_table.currentRow()
        t = r + d
        if r < 0 or t < 0 or t >= len(self.queue):
            return
        self.queue[r], self.queue[t] = self.queue[t], self.queue[r]
        self.refresh_queue()
        self.queue_table.selectRow(t)

    def _queue_open(self, row, _col):
        q = self.queue[row]
        if self.job or not q["info"]:
            return
        self.open_path(q["path"])

    # ================================================================== transcription
    def transcribe_or_stop(self):
        self.stop() if self.job else self.transcribe()

    def transcribe(self):
        if self.job:
            return
        waiting = [q for q in self.queue if q["status"] == "Waiting"]
        if waiting:
            self.stop_all = False
            self.queue_totals = {"files": len(waiting), "done": 0, "failed": 0, "audio_s": 0.0, "wall_s": 0.0}
            self._next_in_queue()
            return
        if not self.info:
            self.open_media()
            return
        rp = self.resume_point()
        if rp is not None:
            self._start(self.info, resume_at=rp)
            return
        self._start(self.info)

    def resume_point(self):
        """Where a stopped, failed or crashed job can carry on: the end of its last piece of text."""
        if self.job or not self.info or not self.segments:
            return None
        jm = self.job_meta or {}
        if not (self.stats.get("cancelled") or jm.get("partial")):
            return None
        t = max(float(sg.get("end", 0)) for sg in self.segments)
        dur = self.info.get("duration") or 0
        if t < 1 or (dur and t >= dur - 1):
            return None
        return t

    def start_again(self):
        if self.job or not self.info:
            return
        if self.editor.toPlainText().strip() and QMessageBox.question(
                self, "Start again", "Transcribe the whole file again from the beginning?\n\nThe text on screen is "
                "replaced; files already saved are kept.") != QMessageBox.Yes:
            return
        self.stats = {}
        self.job_meta = {}
        self._start(self.info)

    def _next_in_queue(self):
        for q in self.queue:
            if q["status"] == "Waiting":
                if not self.open_path(q["path"], keep_text=True):
                    q["status"], q["error"] = "Failed", "cannot be opened"
                    continue
                self.clear_results()
                self.session_path = None
                q["status"] = "Transcribing"
                self.refresh_queue()
                if not self._start(self.info, q):
                    q["status"] = "Waiting"
                    self.refresh_queue()
                return
        if self.queue_totals:  # FR-Q-05
            self.stats_label.setText(stats.queue_line(self.queue_totals))
            self.stats_label.setToolTip("")
            self.queue_totals = None

    def _start(self, info, qitem=None, resume_at=None):
        if resume_at is not None:
            return self._start_resume(info, resume_at)
        m, lang, lid, err = self.plan()
        if err:
            kind, detail = err
            if kind == "not_installed":
                if QMessageBox.question(self, "Transcribe", "The chosen model is not installed. Open Options > "
                                        "Transcription on that model's row?") == QMessageBox.Yes:
                    self.options(1, focus_model=detail)
            elif kind == "none":
                self.first_run()
            else:
                QMessageBox.warning(self, "Transcribe", detail)
            return False
        cands = {}
        for code, _ in LANGS[1:]:
            cands[code] = [self.model_dict(x) for x in self.manager.candidates(code)]
        self.raw_lines, self.cleanup_log, self.segments, self.shown_blocks = [], [], [], []
        self.session_path, self.autosave_paths, self.resume = None, {}, None
        hot, words, scope = self._hotwords()
        job = {"path": info["path"], "track": self.track, "duration": info["duration"], "language": lang,
               "model": self.model_dict(m) if (m and self.settings.get("transcription.model") != "auto") or
               (m and lang != "auto") else None,
               "lid": self.model_dict(lid) if lid and lang == "auto" else None,
               "candidates": cands, "settings": dict(self.settings.values), "hotwords": hot,
               "hotwords_scope": scope,
               "background": bool(self.settings.get("general.background_priority") or
                                  self.settings.get("advanced.priority") == "below"),
               "keep_loaded": bool(self.settings.get("advanced.keep_loaded"))}
        if len(words) > HOTWORD_LIMIT:
            self.raw_lines.append(f"Hotwords cut to about {HOTWORD_LIMIT} words; not used: "
                                  + " ".join(words[HOTWORD_LIMIT:]))
        self.editor.set_blocks([])
        return self._launch(info, job, qitem, lang, lang)

    def _hotwords(self):
        sets = self.settings.get("vocabulary.sets") or {}
        vs = sets.get(self.settings.get("vocabulary.active")) or {}
        words = " ".join(vs.get("hotwords", [])).split()
        hot = []
        n = 0
        for h in vs.get("hotwords", []):  # FR-VOC-02: cut to the prompt limit, most important first
            n += len(h.split())
            if n > HOTWORD_LIMIT:
                break
            hot.append(h)
        return hot, words, vs.get("scope", "all")

    def _start_resume(self, info, resume_at):
        """Carry on a stopped, failed or crashed job: same model and language, from the end of the text so far."""
        jm = self.job_meta or {}
        model = dict(jm.get("model") or {})
        lang = jm.get("language") or self.settings.get("transcription.language")
        usable = model.get("kind") and model.get("folder") and os.path.isdir(model["folder"])
        if not usable:
            m, plang, lid, err = self.plan()
            code = lang if lang in dict(LANGS) and lang != "auto" else (plang if plang != "auto" else "en")
            cands = self.manager.candidates(code)
            alt = m or (cands[0] if cands else None)
            if err or alt is None:
                QMessageBox.warning(self, "Resume", "The model used so far is not available and no other installed "
                                    "model fits. Install one in Options > Transcription, or start again.")
                return False
            if QMessageBox.question(self, "Resume", f"The model used so far ({model.get('name', 'unknown')}) is not "
                                    f"available. Carry on with {alt.name}?") != QMessageBox.Yes:
                return False
            model = self.model_dict(alt)
            lang = code if lang == "auto" else lang
        if lang == "auto":
            lang = "en"
        hot, _, scope = self._hotwords()
        job = {"path": info["path"], "track": self.track, "duration": info["duration"], "language": lang,
               "model": model, "lid": None, "candidates": {}, "settings": dict(self.settings.values),
               "hotwords": hot, "hotwords_scope": scope, "start_at": resume_at,
               "background": bool(self.settings.get("general.background_priority") or
                                  self.settings.get("advanced.priority") == "below"),
               "keep_loaded": bool(self.settings.get("advanced.keep_loaded"))}
        modified = self.editor.document().isModified()
        self.resume = {"segments": [dict(x) for x in self.segments], "shown": [dict(b) for b in self.shown_blocks],
                       "edited": self.editor.blocks() if modified else None, "stats": dict(self.stats),
                       "cleanup": list(self.cleanup_log), "at": resume_at}
        chosen = "auto" if self.stats.get("language_detected") else lang
        log.info("resuming %s at %.1f s with %s", os.path.basename(info["path"]), resume_at, model.get("name"))
        return self._launch(info, job, None, lang, chosen)

    def _launch(self, info, job, qitem, lang, chosen_lang):
        self.job = {"info": info, "qitem": qitem, "language": lang, "model": job.get("model"), "done_s":
                    float(job.get("start_at") or 0), "chosen_lang": chosen_lang, "stage": "Starting", "live": {}}
        self.job_segments = []
        self.job_t0 = time.perf_counter()
        self.editor.setReadOnly(True)
        self.raw.setPlainText("\n".join(self.raw_lines))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        if self.settings.get("general.keep_awake"):
            system.keep_awake(True)
        self.worker.submit(job)
        log.info("job started: %s (%.0f s), language %s", os.path.basename(info["path"]), info["duration"], lang)
        self._journal()
        self.update_go_label()
        self.update_actions()
        self.stage("Starting")
        return True

    def stop(self):
        if not self.job:
            return
        self.stop_all = True
        self.worker.cancel()
        self.stage("Stopping…")

    # ---------------------------------------------------------------- worker events
    def _tick(self):
        if self.job:
            for ev in self.worker.poll():
                self._event(ev)
            if self.job and not self.worker.alive():  # FR-ASR-05
                self._failed("The recognition process ended unexpectedly. The text done so far is kept; see the log.")
            if self.job:
                self._live_stats()
        self._update_pos()

    def _event(self, ev):
        kind = ev[0]
        j = self.job
        if kind == "stage":
            j["stage"] = ev[1]
            self.stage(ev[1])
        elif kind == "progress":
            j["done_s"], total = ev[1], ev[2] or 1
            if len(ev) > 3:
                j["live"] = ev[3]
            frac = min(1.0, j["done_s"] / total)
            self.progress.setValue(int(frac * 1000))
            el = time.perf_counter() - self.job_t0
            left = ""
            if frac > 0.03 and j["stage"] == "Transcribing":
                rest = el / frac * (1 - frac)
                left = f" · about {max(1, round(rest / 60))} min left" if rest >= 60 else f" · about {int(rest)} s left"
            self.stage_label.setText(f"Transcribing {media.fmt_clock(j['done_s'])} / {media.fmt_clock(total)} "
                                     f"({frac:.0%}){left}")
            if j["qitem"]:
                j["qitem"]["status"] = f"Transcribing {frac:.0%}"
                self._queue_status(j["qitem"])
            self.update_title()
        elif kind == "model":
            j["model"] = ev[1]["model"]
            j["language"] = ev[1]["language"]
            self._journal()
        elif kind == "segment":
            seg = ev[1]
            self.job_segments.append(seg)
            self.raw_lines.append(seg.get("raw", ""))
            self.raw.appendPlainText(seg.get("raw", ""))
            live = dict(seg)
            script = postproc.script_for(seg.get("lang") or j["language"], self.settings)
            if script:
                live["text"] = postproc.convert_script(seg["text"], script)
            self.editor.append_block(live)
            self.editor.document().setModified(False)  # live text is not an edit
            if len(self.job_segments) % 10 == 0 or time.perf_counter() - self._journal_t > 3:
                self._journal()
        elif kind == "raw":
            self.raw_lines.append(ev[1])
            self.raw.appendPlainText(ev[1])
        elif kind == "done":
            self._finish(ev[1])
        elif kind == "error":
            self._failed(ev[1])

    def _journal(self):
        """FR-ASR-08: the text so far, saved while the job runs, so a crash can be resumed."""
        j = self.job
        self._journal_t = time.perf_counter()
        prev = self.resume["segments"] if self.resume else []
        system.journal_write(self.settings, {
            "path": j["info"]["path"], "track": self.track, "duration": j["info"]["duration"],
            "language": j["language"], "model": j["model"], "segments": prev + self.job_segments,
            "language_detected": j["chosen_lang"] == "auto",
            "started": datetime.datetime.now().isoformat(timespec="seconds")})

    def _live_stats(self):
        j = self.job
        st = {"wall_s": time.perf_counter() - self.job_t0, "audio_s": j["done_s"],
              "recog_s": time.perf_counter() - self.job_t0, "model_name": (j["model"] or {}).get("name", "")}
        st.update(j.get("live") or {})
        self.stats_label.setText(stats.line(st))

    def stage(self, text):
        self.stage_label.setText(text)

    def _queue_status(self, q):
        try:
            r = self.queue.index(q)
        except ValueError:
            return
        it = self.queue_table.item(r, 3)
        if it:
            it.setText(q["status"])
            it.setToolTip(q.get("error", ""))

    def _end_job_ui(self):
        self.editor.setReadOnly(False)
        self.progress.setVisible(False)
        system.keep_awake(False)
        self.job = None
        self.update_title()
        self.update_go_label()
        self.update_actions()

    def _finish(self, wst):
        j = self.job
        t_clean = time.perf_counter()
        info = j["info"]
        model = wst.get("model") or j["model"] or {}
        lang = wst.get("language") or j["language"]
        segs, dropped = postproc.filter_segments(self.job_segments, self.settings)
        segs, reps = postproc.clean_segments(segs, self.settings, lang)
        rs = self.resume
        if rs and rs["segments"] and segs:  # no repeated word where the resumed text joins the earlier text
            t = postproc.trim_overlap(rs["segments"][-1]["text"], segs[0]["text"])
            if t != segs[0]["text"]:
                cut = segs[0]["text"][:len(segs[0]["text"]) - len(t)].strip()
                self._overlap_note = f"Resume: '{cut}' was heard twice where the text joins; removed once"
                segs[0] = dict(segs[0], text=t)
            segs = [x for x in segs if x["text"].strip()]
        self.segments = (rs["segments"] if rs else []) + segs
        self.cleanup_log = (rs["cleanup"] + [f"--- Resumed at {media.fmt_clock(rs['at'])} ---"]) if rs else []
        if getattr(self, "_overlap_note", None):
            self.cleanup_log.append(self._overlap_note)
            self._overlap_note = None
        self.cleanup_log += [f"Filtered [{media.fmt_clock(s['start'])}] \"{s['text']}\" - {why}" for s, why in dropped]
        self.cleanup_log += [f"Replaced '{h}' → '{c}' ×{n}" for h, c, n in reps]
        script = postproc.script_for(lang, self.settings)
        if script or any(postproc.has_cjk(s["text"]) for s in segs[:50]):
            names = {"hk": "Traditional (Hong Kong)", "tw": "Traditional (Taiwan)", "cn": "Simplified"}
            sc = script or (self.settings.get("transcription.script_yue") if lang == "yue" else
                            self.settings.get("transcription.script_zh"))
            self.cleanup_log.append(f"Chinese script: {names.get(sc, sc)}")
        if self.settings.get("output.remove_fillers"):
            self.cleanup_log.append("Filler words removed: " + ", ".join(self.settings.get("output.filler_list")))
        cancelled = wst.get("cancelled")
        audio_s = wst.get("stopped_at", info["duration"]) if cancelled else info["duration"]
        prev = rs["stats"] if rs else {}
        base_wall = float(prev.get("wall_s") or 0)
        self.stats = dict(wst)
        self.stats.update({"wall_s": base_wall + time.perf_counter() - self.job_t0, "audio_s": audio_s,
                           "duration": info["duration"], "model_name": model.get("name", ""),
                           "language_detected": j["chosen_lang"] == "auto", "clean_s": time.perf_counter() - t_clean})
        if rs:  # totals over every run of this file
            for k in ("read_s", "lid_s", "load_s", "recog_s", "speech_s", "speech_tokens", "text_tokens",
                      "out_tokens", "clean_s"):
                self.stats[k] = (prev.get(k) or 0) + (self.stats.get(k) or 0)
            for k in ("language_prob", "gain_db"):
                if not self.stats.get(k) and prev.get(k):
                    self.stats[k] = prev[k]
            self.stats["resumed"] = int(prev.get("resumed") or 0) + 1
        low_note = self._speech_check(audio_s, cancelled)
        self._show_merged(segs)
        if cancelled:
            self.status_msg(f"Stopped at {media.fmt_clock(audio_s)}. The text done so far is kept.")
        self.job_meta = {"model": model, "language": lang, "partial": bool(cancelled)}
        # save (step 10)
        t_save = time.perf_counter()
        saved = self.autosave()
        self.stats["save_s"] = time.perf_counter() - t_save + float(prev.get("save_s") or 0)
        self.stats["wall_s"] = base_wall + time.perf_counter() - self.job_t0
        self.resume = None
        self.update_stats_bar()
        self.update_raw()
        if saved and self.session_path:  # session again with final statistics
            self._write_session(self.session_path)
        system.journal_clear(self.settings)
        q = j["qitem"]
        log.info("job done: %s, %d segments, %s", os.path.basename(info["path"]), len(segs), stats.line(self.stats))
        self._end_job_ui()
        if q:
            q["status"] = f"Stopped at {media.fmt_clock(audio_s)}" if cancelled else \
                "Done \u00b7 little speech found" if low_note else "Done"
            q["error"] = low_note or ""
            self._queue_status(q)
            if self.queue_totals:
                self.queue_totals["done"] += 1
                self.queue_totals["audio_s"] += audio_s
                self.queue_totals["wall_s"] += self.stats["wall_s"]
        self.notify(f"{os.path.basename(info['path'])}: " + ("stopped" if cancelled else "transcript ready"))
        self.stage(("Stopped at " + media.fmt_clock(audio_s)) if cancelled else
                   ("Done" + (f" · saved to {self.output_dir()}" if saved else "")))
        if low_note:
            self.status_msg("Little of the recording was recognised as speech; Raw output lists the stretches "
                            "without text.")
            if not q:
                QTimer.singleShot(0, lambda: QMessageBox.warning(self, "Transcribe", low_note))
        if q and not self.stop_all:
            QTimer.singleShot(200, self._next_in_queue)
        elif q and self.queue_totals:
            self.stats_label.setText(stats.queue_line(self.queue_totals))

    def _speech_check(self, audio_s, cancelled):
        """Issue #1: list long stretches without text in Raw output, and warn when little of the recording was
        recognised as speech (speech may have been missed). Returns the warning text, or None."""
        for a, b in audioproc.silent_stretches(self.segments, audio_s)[:10]:
            self.cleanup_log.append(f"No text from {media.fmt_clock(a)} to {media.fmt_clock(b)} "
                                    f"({(b - a) / 60:.0f} min)")
        frac = audioproc.low_speech(self.stats.get("speech_s"), audio_s)
        if frac is None or cancelled:
            return None
        sp = float(self.stats.get("speech_s") or 0)
        note = (f"Only {frac:.0%} of this recording ({media.fmt_clock(sp)} of {media.fmt_clock(audio_s)}) was "
                "recognised as speech, so parts where people talk may be missing from the transcript.\n\n"
                "Raw output lists the stretches without text. If people talk there, lower Speech threshold in "
                "Options > Advanced > Voice detection (for example to 0.35), turn Noise reduction off, and use "
                "Transcribe > Start Again from the Beginning.")
        self.cleanup_log.append(f"Warning: only {frac:.0%} of the recording was recognised as speech")
        log.warning("little speech found: %.0f%% of %.0f s", frac * 100, audio_s)
        return note

    def _failed(self, message):
        j = self.job
        log.error("job failed: %s", message)
        rs = self.resume
        new, _ = postproc.clean_segments(self.job_segments, self.settings, j.get("language"))
        if self.job_segments or rs:
            self.segments = (rs["segments"] if rs else []) + new
            self._show_merged(new)
        if self.segments:  # Resume can carry on from the end of this text
            self.job_meta = {"model": j.get("model") or {}, "language": j.get("language"), "partial": True}
            self.stats = dict(rs["stats"]) if rs else {}
            self.stats.update({"cancelled": True, "language_detected": j["chosen_lang"] == "auto"})
        self.resume = None
        self.raw.appendPlainText(f"\nERROR: {message}")
        q = j["qitem"]
        self._end_job_ui()
        self.stage("Failed: " + message)
        if q:
            q["status"], q["error"] = "Failed", message
            self._queue_status(q)
            if self.queue_totals:
                self.queue_totals["failed"] += 1
            if not self.stop_all:  # FR-Q-04: the queue goes on
                QTimer.singleShot(200, self._next_in_queue)
                return
        QMessageBox.warning(self, "Transcribe", f"The transcription stopped:\n\n{message}\n\nThe text done so far is kept.")

    # ---------------------------------------------------------------- results
    def blocks_from_segments(self, segs=None, offset=0):
        segs = self.segments if segs is None else segs
        if self.settings.get("output.times") == "segment":
            blocks = postproc.segment_blocks(segs)
            for i, b in enumerate(blocks):
                b["segs"] = [offset + i]
            return blocks
        paras = postproc.paragraphs(segs, float(self.settings.get("output.para_pause")),
                                    int(self.settings.get("output.para_chars")))
        for p in paras:
            p["segs"] = [offset + i for i in p["segs"]]
        return paras

    def attach_subs(self, blocks):
        """Give edited or reloaded blocks the sentence times of the paragraphs they came from (proofreading)."""
        by_start = {round(p["start"], 3): p for p in self.shown_blocks or []}
        out = []
        for b in blocks:
            b = dict(b)
            p = by_start.get(round(b["start"], 3))
            if p and p.get("subs"):
                b["subs"] = p["subs"]
                b["orig_text"] = p["text"]  # sentence positions only hold while the text is unchanged
            out.append(b)
        return out

    def show_blocks(self, blocks=None):
        if blocks is None:
            blocks = self.blocks_from_segments()
            self.shown_blocks = [dict(b) for b in blocks]
        else:
            blocks = self.attach_subs(blocks)
        self.editor.set_blocks(blocks)

    def _show_merged(self, new_segs):
        """After a resumed job: keep the edits made to the earlier text, add the new paragraphs after it."""
        rs = self.resume
        if rs and rs.get("edited") is not None:
            n0 = len(rs["segments"])
            new_blocks = self.blocks_from_segments(new_segs, offset=n0)
            self.shown_blocks = rs["shown"] + [dict(b) for b in new_blocks]
            self.editor.set_blocks(self.attach_subs(rs["edited"]) + new_blocks)
            self.editor.document().setModified(True)
        else:
            self.show_blocks()
            self.editor.document().setModified(False)

    SEGMENT_FORMATS = ("srt", "vtt", "json", "csv")

    def segment_blocks(self, edited):
        """Subtitles, JSON and CSV are per segment (section 9): an unedited paragraph is expanded back into its
        segments; an edited one is kept whole so the edit is never lost (FR-OUT-05)."""
        by_start = {round(p["start"], 3): p for p in getattr(self, "shown_blocks", []) or []}
        out = []
        for b in edited:
            p = by_start.get(round(b["start"], 3))
            segs = p.get("segs") if p else None
            if p and segs and len(segs) > 1 and b["text"] == p["text"] and max(segs) < len(self.segments):
                for i in segs:
                    sg = self.segments[i]
                    out.append({"start": sg["start"], "end": sg["end"], "text": sg["text"],
                                "lang": sg.get("lang", ""), "flag": sg.get("flag", "")})
            else:
                out.append(b)
        return out

    def update_stats_bar(self):
        if not self.stats:
            return
        self.stats_label.setText(stats.line(self.stats))
        self.stats_label.setToolTip(stats.time_tooltip(self.stats) + "\n\n" + stats.audio_tooltip(self.stats))

    def update_raw(self):
        st = self.stats
        s = self.settings
        head = [f"{APP_NAME} {VERSION} · {datetime.datetime.now():%Y-%m-%d %H:%M}",
                f"File: {self.info['path'] if self.info else ''}",
                f"Statistics: {stats.line(st)}", stats.time_tooltip(st), stats.audio_tooltip(st),
                f"Settings: language {s.get('transcription.language')}, model {s.get('transcription.model')}, "
                f"speed {s.get('transcription.speed')}, beam {s.get('advanced.beam') or 'follow speed'}, "
                f"VAD threshold {s.get('advanced.vad_threshold')}, min silence {s.get('advanced.vad_min_silence_ms')} ms, "
                f"padding {s.get('advanced.vad_pad_ms')} ms, normalise {s.get('advanced.normalise')} "
                f"({s.get('advanced.target_dbfs')} dBFS, max +{s.get('advanced.max_gain_db')} dB), "
                f"noise reduction {s.get('advanced.noise_reduction')}, "
                f"compute {s.get('advanced.compute_type')}, vocabulary set '{s.get('vocabulary.active')}'",
                "=" * 78]
        tail = ["", "=" * 78, "Clean-up:"] + (self.cleanup_log or ["(nothing changed)"])
        self.raw.setPlainText("\n".join(head + self.raw_lines + tail))

    def _stats_menu(self, pos):
        m = QMenu(self)
        a = m.addAction("Copy statistics")
        a.setEnabled(bool(self.stats))
        if m.exec(self.stats_label.mapToGlobal(pos)) == a:
            QApplication.clipboard().setText(stats.full_text(self.stats))

    def _edited(self):
        self.update_title()

    # ================================================================== saving and export (section 9)
    def meta(self):
        info = self.info or {}
        jm = getattr(self, "job_meta", {}) or {}
        model = jm.get("model") or {}
        lang = jm.get("language", "")
        path = info.get("path", "")
        return {"stem": os.path.splitext(os.path.basename(path))[0] or "transcript", "file": os.path.basename(path),
                "media_path": path, "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "duration": info.get("duration", 0), "language": lang, "language_name": LANG_NAMES.get(lang, lang),
                "model": model.get("name", ""), "model_short": model.get("short", "model"),
                "stats_line": stats.line(self.stats) if self.stats else "", "stats": self.stats_public(),
                "version": VERSION}

    def stats_public(self):
        return {k: (v if not isinstance(v, dict) else v.get("name")) for k, v in (self.stats or {}).items()}

    def output_dir(self, ask=False):
        s = self.settings
        if s.get("general.save_next_to_media") or s.get("output.save_to") == "media":
            if self.info:
                return os.path.dirname(self.info["path"])
        if s.get("output.save_to") == "ask" and ask:
            d = QFileDialog.getExistingDirectory(self, "Save transcripts to", s.output_folder())
            return d or None
        return s.output_folder()

    def autosave(self):
        mode = self.settings.get("output.autosave")
        if mode == "off" or not self.segments:
            return False
        same = bool(self.resume and self.session_path and os.path.isdir(os.path.dirname(self.session_path)))
        folder = os.path.dirname(self.session_path) if same else self.output_dir(ask=True)
        if not folder:
            return False
        try:
            os.makedirs(folder, exist_ok=True)
            base = exporters.base_name(self.meta(), self.settings.get("output.pattern"))
            sp = self.session_path if same else os.path.join(folder, base + SESSION_EXT)
            if not same and os.path.exists(sp) and self.settings.get("output.if_exists") != "overwrite":
                sp = exporters.unique(sp, "number")
            self.session_path = sp
            if mode == "session+exports":
                written = self.export(self.settings.get("output.formats"), folder, self.settings.get("output.pattern"),
                                      replace=self.autosave_paths if same else None)
                self.autosave_paths = {f: p for f, p in written}
            self._write_session(sp)
            self.editor.document().setModified(False)
            self.update_title()
            return True
        except Exception as ex:
            log.exception("auto-save failed")
            self.status_msg(f"Auto-save failed: {ex}")
            QMessageBox.warning(self, "Auto-save", f"The transcript could not be saved to {folder}:\n{ex}")
            return False

    def export(self, formats, folder, pattern, replace=None):
        """Writes the formats; returns [(format, path)]. replace: {format: path} to overwrite without asking."""
        blocks = self.editor.blocks()  # FR-OUT-05: the edited transcript
        if not blocks:
            return []
        meta = self.meta()
        base = exporters.base_name(meta, pattern)
        seg_blocks = None
        written = []
        policy = self.settings.get("output.if_exists")
        os.makedirs(folder, exist_ok=True)
        for fmt in formats:
            p = exporters.target_path(folder, base, fmt)
            if replace and replace.get(fmt):
                p = replace[fmt]
            elif os.path.exists(p):
                if policy == "ask":
                    r = QMessageBox.question(self, "Export", f"{os.path.basename(p)} exists. Overwrite it?\n\n"
                                             "No = save with a number added.",
                                             QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
                    if r == QMessageBox.Cancel:
                        continue
                    if r == QMessageBox.No:
                        p = exporters.unique(p, "number")
                elif policy == "number":
                    p = exporters.unique(p, "number")
            if fmt in self.SEGMENT_FORMATS:
                if seg_blocks is None:
                    seg_blocks = self.segment_blocks(blocks)
                exporters.write(fmt, p, seg_blocks, meta, self.settings)
            else:
                exporters.write(fmt, p, blocks, meta, self.settings)
            written.append((fmt, p))
        log.info("exported %d file(s) to %s", len(written), folder)
        return written

    def export_dialog(self):
        if not self.editor.blocks():
            return
        dlg = ExportDialog(self, self.settings, self.output_dir() or self.settings.output_folder())
        if dlg.exec():
            fmts, folder, pattern = dlg.result_values()
            try:
                written = self.export(fmts, folder, pattern or self.settings.get("output.pattern"))
            except Exception as ex:
                QMessageBox.warning(self, "Export", str(ex))
                return
            self.status_msg(f"Exported {len(written)} file(s) to {folder}")

    def _session_data(self):
        info = self.info or {}
        if self.media_sha is None and info.get("path") and os.path.isfile(info["path"]):
            self.media_sha = media.sha256_file(info["path"])
        jm = getattr(self, "job_meta", {}) or {}
        return {"media_path": info.get("path"), "media_sha256": self.media_sha, "track": self.track,
                "duration": info.get("duration"), "language": jm.get("language"), "model": jm.get("model"),
                "segments": self.segments, "blocks": self.editor.blocks(), "raw": self.raw_lines,
                "paragraphs": getattr(self, "shown_blocks", []),
                "cleanup": self.cleanup_log, "stats": self.stats_public(), "exports": self.autosave_paths,
                "partial": bool((self.job_meta or {}).get("partial")),
                "settings": {k: v for k, v in self.settings.values.items()
                             if k.startswith(("transcription.", "advanced.", "output.", "vocabulary.active"))
                             and k != "transcription.location_cache"},
                "edited": self.editor.document().isModified()}

    def _write_session(self, path):
        session.save(path, self._session_data())

    def save_session(self):
        if not self.editor.blocks():
            return True
        if not self.session_path:
            return self.save_session_as()
        try:
            self._write_session(self.session_path)
        except OSError as ex:
            QMessageBox.warning(self, "Save Transcript", str(ex))
            return False
        self.editor.document().setModified(False)
        self.update_title()
        self.status_msg(f"Saved {self.session_path}")
        return True

    def save_session_as(self):
        if not self.editor.blocks():
            return True
        base = exporters.base_name(self.meta(), self.settings.get("output.pattern"))
        start = os.path.join(self.output_dir() or self.settings.output_folder(), base + SESSION_EXT)
        p, _ = QFileDialog.getSaveFileName(self, "Save Transcript As", start, f"Transcript session (*{SESSION_EXT})")
        if not p:
            return False
        if not p.endswith(SESSION_EXT):
            p += SESSION_EXT
        self.session_path = p
        return self.save_session()

    def open_session(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open Transcript", self.settings.output_folder(),
                                                  f"Transcript session (*{SESSION_EXT})")
            if not path:
                return
        if self.job or not self.confirm_unsaved():
            return
        try:
            data = session.load(path)
        except Exception as ex:
            QMessageBox.warning(self, "Open Transcript", str(ex))
            return
        mp = data.get("media_path") or ""
        if not os.path.isfile(mp):  # FR-OUT-06: find the media again and check it
            QMessageBox.information(self, "Open Transcript", f"The recording was not found at\n{mp}\n\nPlease locate it.")
            mp, _ = QFileDialog.getOpenFileName(self, "Locate the recording", os.path.dirname(path), MEDIA_FILTER)
            if mp and data.get("media_sha256") and media.sha256_file(mp) != data["media_sha256"]:
                if QMessageBox.question(self, "Open Transcript", "This file is not the same recording (SHA-256 "
                                        "differs). Use it anyway?") != QMessageBox.Yes:
                    mp = ""
        if mp and os.path.isfile(mp):
            self.open_path(mp, keep_text=True)
            self.track_combo.setCurrentIndex(int(data.get("track") or 0))
            self.media_sha = data.get("media_sha256")
        self.segments = data.get("segments") or []
        self.raw_lines = data.get("raw") or []
        self.cleanup_log = data.get("cleanup") or []
        self.stats = data.get("stats") or {}
        self.job_meta = {"model": data.get("model") or {"name": self.stats.get("model", ""), "short": "model"},
                         "language": data.get("language"),
                         "partial": bool(data.get("partial") or self.stats.get("cancelled"))}
        self.autosave_paths = data.get("exports") or {}
        self.resume = None
        self.shown_blocks = data.get("paragraphs") or []
        if data.get("blocks"):
            self.show_blocks(data["blocks"])
        else:
            self.show_blocks()
        self.editor.document().setModified(False)
        self.update_stats_bar()
        self.update_raw()
        self.session_path = path
        self.update_title()
        self.update_actions()
        self.update_go_label()
        if self.resume_point() is not None:
            self.status_msg(f"This transcript stopped at {media.fmt_clock(self.resume_point())}. Press Resume to "
                            "carry on from there.")

    def copy_transcript(self):
        """FR-OUT-04: plain text with times and HTML."""
        blocks = self.editor.blocks()
        if not blocks:
            return
        style = self.settings.get("output.time_format")
        text = "\r\n\r\n".join(f"[{exporters.fmt_time(b['start'], style)}] {b['text']}" for b in blocks)
        htm = "".join(f"<p><b>[{exporters.fmt_time(b['start'], style)}]</b> {html.escape(b['text'])}</p>" for b in blocks)
        md = QMimeData()
        md.setText(text)
        md.setHtml(htm)
        QApplication.clipboard().setMimeData(md)
        self.status_msg("Transcript copied to the clipboard.")

    # ================================================================== edit menu
    def find_dialog(self, replace):
        self.tabs.setCurrentIndex(0)
        if self.find_dlg:
            self.find_dlg.close()
        self.find_dlg = FindReplaceDialog(self, self.editor, replace)
        self.find_dlg.show()

    def find_next(self):
        if self.find_dlg and self.find_dlg.find_edit.text():
            self.find_dlg.find_next()
        else:
            self.find_dialog(False)

    def add_selection_to_vocab(self):
        sel = self.editor.textCursor().selectedText().strip()
        if not sel:
            QMessageBox.information(self, "Vocabulary", "Select the misheard words in the transcript first.")
            return
        self.options(2, vocab_heard=sel)

    def apply_vocab(self):
        blocks = self.editor.blocks()
        if not blocks:
            return
        _, reps = self.settings.vocabulary((getattr(self, "job_meta", {}) or {}).get("language"))
        total = {}
        for b in blocks:
            script = postproc.script_for(b.get("lang") or (self.job_meta or {}).get("language"), self.settings)
            b["text"], lg = postproc.apply_replacements(b["text"], reps, script)
            for h, c, n in lg:
                total[(h, c)] = total.get((h, c), 0) + n
        for seg in self.segments:  # keep the per-segment formats in step
            script = postproc.script_for(seg.get("lang") or (self.job_meta or {}).get("language"), self.settings)
            seg["text"], _ = postproc.apply_replacements(seg["text"], reps, script)
        for p in getattr(self, "shown_blocks", []) or []:
            script = postproc.script_for(p.get("lang") or (self.job_meta or {}).get("language"), self.settings)
            p["text"], _ = postproc.apply_replacements(p["text"], reps, script)
        cur = self.editor.verticalScrollBar().value()
        self.show_blocks(blocks)
        self.editor.document().setModified(True)
        self.editor.verticalScrollBar().setValue(cur)
        lines = [f"Replaced '{h}' → '{c}' ×{n}" for (h, c), n in total.items()]
        self.cleanup_log += lines
        self.update_title()
        self.status_msg("; ".join(lines) if lines else "No replacement matched.")

    def go_to_time(self):
        text, ok = QInputDialog.getText(self, "Go to Time", "Time (hh:mm:ss or mm:ss):")
        if not ok:
            return
        t = parse_time(text)
        if t is None:
            return
        self.editor.go_to_time(t)
        self.player.seek(t)
        self._update_pos()

    # ================================================================== player
    def play_pause(self):
        if not self.info:
            return
        self.player.toggle()

    def play_from(self, t):
        if self.info:
            self.player.play(t)

    def skip(self, d):
        if self.info:
            self.player.skip(d)
            self._update_pos()

    def set_playback_speed(self, v):
        v = float(v)
        self.settings.set("general.playback_speed", v)
        self._save_settings()
        self.player.set_options(speed=v)
        self.speed_combo.blockSignals(True)
        self.speed_combo.setCurrentIndex(max(0, self.speed_combo.findData(v)))
        self.speed_combo.blockSignals(False)
        for a in self.pspeed_group.actions():
            a.setChecked(abs(a.data() - v) < 1e-3)

    def replay_sentence(self):
        """F6: play the current sentence again (pressed early in a sentence, the one before)."""
        if not self.info:
            return
        sents = self.editor.sentences()
        if not sents:
            self.player.play(max(0.0, self.player.position() - 3))
            return
        if self.player.playing:
            t = self.player.position()
            cur = self.editor.sentence_at_time(t, sents)
            if cur is not None and t - cur["start"] < 0.8:
                i = next(k for k, x in enumerate(sents) if x is cur)
                cur = sents[i - 1] if i > 0 else cur
        else:
            cur = self.editor.sentence_at_cursor() or self.editor.sentence_at_time(self.player.position(), sents)
        if cur is not None:
            self.player.play(cur["start"])
            self._update_pos()

    def unsure_line(self, step):
        """F7 / F8: previous / next line Whisper was unsure about (amber); selects it and plays it."""
        sents = self.editor.sentences()
        flagged = [x for x in sents if x["flag"] == "check"]
        if not flagged:
            self.status_msg("No unsure lines. Only Whisper marks lines it was unsure about (amber).")
            return
        if self.player.playing:
            here = self.editor.sentence_at_time(self.player.position(), sents)
        else:
            here = self.editor.sentence_at_cursor()
        ref = here["start"] if here else -1.0
        if step > 0:
            target = next((x for x in flagged if x["start"] > ref + 0.05), None)
        else:
            target = next((x for x in reversed(flagged) if x["start"] < ref - 0.05), None)
        if target is None:
            self.status_msg("No more unsure lines " + ("after" if step > 0 else "before") + " this point.")
            return
        self.tabs.setCurrentIndex(0)
        self.editor.select_sentence(target)
        self.editor.setFocus()
        k = next(i for i, x in enumerate(flagged) if x is target) + 1
        self.status_msg(f"Unsure line {k} of {len(flagged)} at {media.fmt_clock(target['start'])} - correct it, "
                        "F6 plays it again, F8 goes to the next.")
        if self.info:
            self.player.play(target["start"])

    def names_from_documents(self):
        """Edit > Add Names from Documents: names from an agenda, attendee list or e-mail become hotwords."""
        from ..names import FILE_FILTER
        from .namesdlg import NamesDialog
        files, _ = QFileDialog.getOpenFileNames(self, "Documents with names", "", FILE_FILTER)
        if not files:
            return
        sets = self.settings.get("vocabulary.sets") or {"Default": {"scope": "all", "hotwords": [], "replacements": []}}
        dlg = NamesDialog(self, sets, self.settings.get("vocabulary.active"), files)
        if not dlg.exec():
            return
        self.settings.set("vocabulary.sets", dlg.apply_to(sets))
        if dlg.make_active.isChecked():
            self.settings.set("vocabulary.active", dlg.result_set)
        self._save_settings()
        self.status_msg(f"Added {len(dlg.result_names)} names to the vocabulary set '{dlg.result_set}'"
                        + (", which is now used for transcription." if dlg.make_active.isChecked() else "."))

    def _slider_seek(self):
        self._dragging = False
        if self.info and self.info["duration"]:
            self.player.seek(self.slider.value() / 1000 * self.info["duration"])

    def _player_state(self, playing):
        self.play_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause if playing else QStyle.SP_MediaPlay))

    def _update_pos(self):
        dur = self.info["duration"] if self.info else 0
        pos = self.player.position() if self.info else 0
        self.pos_label.setText(f"{media.fmt_clock(pos)} / {media.fmt_clock(dur)}")
        if not self._dragging and dur:
            self.slider.blockSignals(True)
            self.slider.setValue(int(pos / dur * 1000))
            self.slider.blockSignals(False)
        for w in (self.play_btn, self.back_btn, self.fwd_btn, self.slider):
            w.setEnabled(bool(self.info))
        # proofreading: highlight the sentence being played and keep it in view
        if self.settings.get("general.follow_playback") and self.info:
            if self.player.playing or (self._last_hl is not None and abs(pos - self._last_hl) > 0.05):
                self.editor.highlight_time(pos, follow=self.player.playing)
                self._last_hl = pos
        elif self._last_hl is not None:
            self.editor.clear_highlight()
            self._last_hl = None

    # ================================================================== window state
    def update_title(self):
        name = os.path.basename(self.info["path"]) if self.info else ""
        mod = "*" if self.editor.document().isModified() else ""
        pct = ""
        if self.job and self.job["info"]["duration"]:
            pct = f" [{min(100, int(self.job['done_s'] / self.job['info']['duration'] * 100))}%]"
        self.setWindowTitle((f"{name}{mod} - " if name else "") + APP_TITLE + pct)

    def update_actions(self):
        running = bool(self.job)
        has_text = bool(self.editor.toPlainText().strip())
        self.a_stop.setEnabled(running)
        self.a_go.setEnabled(not running)
        for a in (self.a_export, self.a_copy_tr, self.a_save, self.a_save_as, self.a_apply_vocab):
            a.setEnabled(has_text and not running)
        self.a_props.setEnabled(bool(self.info))
        self.a_restart.setEnabled(bool(self.info) and not running)
        for a in (self.a_replay, self.a_prev_unsure, self.a_next_unsure):
            a.setEnabled(has_text)
        self.a_close.setEnabled(bool(self.info) and not running)
        raw = self.tabs.currentIndex() == 1
        for a in (self.a_cut, self.a_paste, self.a_undo, self.a_redo):
            a.setEnabled(not raw and not running)
        mkey = self.settings.get("transcription.model")
        m = self.manager.find(mkey) if mkey != "auto" else None
        self.speed_menu.setEnabled(m is None or m.kind == "whisper")  # FR-MENU-01
        self.go_btn.setEnabled(True)

    def status_msg(self, text):
        self.stage_label.setText(text)
        log.info("status: %s", text)

    def notify(self, text):
        """FR-MENU-03: a Windows notification when the window is minimised."""
        if not self.settings.get("general.notify") or not (self.isMinimized() or not self.isActiveWindow()):
            return
        if not QSystemTrayIcon.isSystemTrayAvailable():
            QApplication.alert(self)
            return
        if self.tray is None:
            self.tray = QSystemTrayIcon(self.windowIcon(), self)
            self.tray.activated.connect(lambda *_: (self.showNormal(), self.activateWindow()))
        self.tray.show()
        self.tray.showMessage(APP_TITLE, text, self.windowIcon(), 8000)
        QApplication.alert(self)

    def first_run(self):
        rec = self.manager.recommended()
        size = sum(self.manager.by_id[c]["size"] for c in rec)
        names = " + ".join(self.manager.by_id[c]["name"] for c in rec)
        extra = [c for c in self.manager.recommended_cantonese_mandarin() if c not in rec]
        extra_text = ""
        if extra:
            extra_text = (f"I also transcribe Cantonese or Mandarin: add "
                          f"{' + '.join(self.manager.by_id[c]['name'] for c in extra)} "
                          f"({human_size(sum(self.manager.by_id[c]['size'] for c in extra))})")
        dlg = FirstRunDialog(self, human_size(size), names, extra_text)
        dlg.exec()
        self.settings.set("general.first_run_done", True)
        self._save_settings()
        if dlg.choice == "download":
            if self.settings.get("advanced.offline"):
                self._set_quick("advanced.offline", False)
            opt = OptionsDialog(self, self.settings, self.manager, 1)
            opt.applied.connect(self._options_applied)
            cm = dlg.wants_cantonese_mandarin()
            QTimer.singleShot(100, lambda: opt.trans.download_recommended(cm))
            opt.exec()
            self.refresh_models()
            self.update_actions()
        elif dlg.choice == "folder":
            d = QFileDialog.getExistingDirectory(self, "Folder where you keep models (for example the version 2.0 "
                                                       "program folder)")
            if d:
                self.add_location_with_notes(d)

    def add_location_with_notes(self, d):
        """FR-MOD-10: version 2.0 folders: local_whisper* are used; FunASR folders are skipped with a note."""
        try:
            found = self.manager.add_location(d)
        except Exception as ex:
            QMessageBox.warning(self, "Add models", str(ex))
            return
        self._save_settings()
        self.refresh_models()
        names = []
        try:
            names = [n.lower() for n in os.listdir(d)]
        except OSError:
            pass
        note = ""
        if "local_stt" in names or "local_vad" in names:
            note = ("\n\nThe FunASR folders (local_stt, local_vad) were skipped: this version runs SenseVoiceSmall "
                    "through sherpa-onnx, which needs the int8 ONNX download (240 MB) in Options > Transcription.")
        msg = (f"Found {len(found)} model folder(s) in {d}." if found else
               f"No model was found in {d} or its subfolders.") + note
        QMessageBox.information(self, "Add models", msg)
        self.update_go_label()

    def check_updates(self):
        QMessageBox.information(self, "Check for Updates",
                                f"You are running {APP_NAME} {VERSION}. This build has no update server configured; "
                                "new versions are installed by replacing the program folder.")

    def help_contents(self):
        p = os.path.join(paths.APP_DIR, "docs", "HELP.html")
        if os.path.isfile(p):
            QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        else:
            QMessageBox.information(self, "Help", "HELP.html is missing from the app\\docs folder.")

    def offer_journal(self):
        """FR-ASR-08: a job journal left behind by a crash or power cut: resume it, or show its text."""
        j = system.journal_read(self.settings)
        if not j or not j.get("segments") or not os.path.isfile(j.get("path", "")):
            system.journal_clear(self.settings)
            return
        last = max(float(sg.get("end", 0)) for sg in j["segments"])
        box = QMessageBox(self)
        box.setWindowTitle(APP_TITLE)
        box.setIcon(QMessageBox.Question)
        box.setText(f"The last job on {os.path.basename(j['path'])} did not finish. Its text up to "
                    f"{media.fmt_clock(last)} was saved.")
        b_resume = box.addButton("Resume transcribing", QMessageBox.AcceptRole)
        b_show = box.addButton("Show the text only", QMessageBox.ActionRole)
        box.addButton("Discard", QMessageBox.RejectRole)
        box.exec()
        choice = box.clickedButton()
        if choice in (b_resume, b_show) and self.open_path(j["path"]):
            if int(j.get("track") or 0) < self.track_combo.count():
                self.track_combo.setCurrentIndex(int(j.get("track") or 0))
            self.segments, _ = postproc.clean_segments(j["segments"], self.settings, j.get("language"))
            self.job_meta = {"model": j.get("model") or {}, "language": j.get("language"), "partial": True}
            self.stats = {"cancelled": True, "stopped_at": last, "language_detected": bool(j.get("language_detected")),
                          "duration": j.get("duration") or (self.info or {}).get("duration", 0)}
            self.raw_lines = [sg.get("raw", "") for sg in j["segments"]]
            self.show_blocks()
            self.editor.document().setModified(True)
            self.update_raw()
            self.update_actions()
            self.update_title()
            self.update_go_label()
            system.journal_clear(self.settings)
            if choice is b_resume and self.resume_point() is not None:
                QTimer.singleShot(0, lambda: self._start(self.info, resume_at=self.resume_point()))
            return
        system.journal_clear(self.settings)

    def closeEvent(self, e):
        if self.job:
            if QMessageBox.question(self, APP_NAME, "Stop the transcription and close?") != QMessageBox.Yes:
                e.ignore()
                return
            self.worker.cancel()
        elif not self.confirm_unsaved():
            e.ignore()
            return
        self.player.stop()
        for cid in list(self.manager.downloads):
            self.manager.pause_download(cid)
        self.settings.set("general.geometry", bytes(self.saveGeometry().toHex()).decode())
        self._save_settings()
        system.keep_awake(False)
        if self.tray:
            self.tray.hide()
        self.worker.stop()
        e.accept()


def apply_theme(theme):
    app = QApplication.instance()
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+
        hints.setColorScheme({"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(
            theme, Qt.ColorScheme.Unknown))
