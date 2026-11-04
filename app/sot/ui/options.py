"""Options dialog (sections 5-10): General · Transcription (with the model manager) · Vocabulary · Output · Advanced.

The dialog edits a working copy of the settings; Apply/OK validate and copy it back (FR-OPT-01).
Model-manager actions (download, delete, locations, packs) act at once, because they change files."""
import copy
import csv
import os
import shutil
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton,
                               QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import paths, system
from ..exporters import FORMATS, base_name
from ..modelmgr import KIND_LABEL, LANG_NAMES, copy_tree, free_disk, human_size
from ..postproc import apply_replacements, convert_script, script_for
from ..settings import DEFAULTS, GROUPS, Settings

LANGS = [("auto", "Auto-detect"), ("en", "English"), ("fr", "French"), ("yue", "Cantonese"), ("zh", "Mandarin")]
SCRIPTS = [("hk", "Traditional (Hong Kong)"), ("tw", "Traditional (Taiwan)"), ("cn", "Simplified")]
HOTWORD_LIMIT = 100  # about half of Whisper's 448-token prompt (FR-VOC-02)
TABS = ["General", "Transcription", "Vocabulary", "Output", "Advanced"]


def run_with_progress(parent, title, fn):
    """Run fn(progress) in a thread with a progress dialog. Returns (result, error)."""
    dlg = QProgressDialog(title, None, 0, 1000, parent)
    dlg.setWindowTitle(title)
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setCancelButton(None)
    box = {"p": 0.0, "res": None, "err": None, "done": False}

    def work():
        try:
            box["res"] = fn(lambda f: box.__setitem__("p", f))
        except Exception as ex:
            box["err"] = str(ex)
        box["done"] = True

    t = threading.Thread(target=work, daemon=True)
    t.start()
    while not box["done"]:
        dlg.setValue(int(box["p"] * 1000))
        QApplication.processEvents()
        t.join(0.05)
    dlg.setValue(1000)
    dlg.close()
    return box["res"], box["err"]


def combo(items, current=None, tip=""):
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(10)
    for key, label in items:
        c.addItem(label, key)
    if current is not None:
        i = c.findData(current)
        c.setCurrentIndex(max(0, i))
    if tip:
        c.setToolTip(tip)
    return c


def check(text, value, tip=""):
    c = QCheckBox(text)
    c.setChecked(bool(value))
    if tip:
        c.setToolTip(tip)
    return c


def path_row(edit, *buttons):
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(edit, 1)
    for b in buttons:
        h.addWidget(b)
    return w


def button(text, slot, tip=""):
    b = QPushButton(text)
    b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    return b


def import_model_list(parent, mgr, path=None):
    """Import a model list file (Options > Transcription, or a file dropped on the window). True if used."""
    from .. import modellist
    if not path:
        path, _ = QFileDialog.getOpenFileName(parent, "Import model list", "", "Model list (*.json)")
        if not path:
            return False
    try:
        data, added, removed, changed, warn = modellist.import_file(path, mgr.listdata)
    except modellist.ListError as ex:
        QMessageBox.warning(parent, "Import model list", f"This model list was not used:\n\n{ex}")
        return False
    except OSError as ex:
        QMessageBox.warning(parent, "Import model list", f"The model list could not be saved: {ex}")
        return False
    mgr.reload_list()
    mgr.scan()
    lines = [f"Model list {data['list_version']} is now used ({len(data['models'])} models)."]
    if added:
        lines.append("New: " + ", ".join(added))
    if changed:
        lines.append("Updated (shown as 'Update available' if you have them): " + ", ".join(changed))
    if removed:
        lines.append("No longer listed: " + ", ".join(removed) + ". Copies you already have stay usable.")
    if not (added or changed or removed):
        lines.append("No model was added, updated or removed.")
    if warn:
        lines.append("\nNotes:\n" + "\n".join(warn))
    QMessageBox.information(parent, "Import model list", "\n\n".join(lines))
    return True


# ====================================================================== dialog
class OptionsDialog(QDialog):
    applied = Signal()
    show_first_run = Signal()

    def __init__(self, parent, settings, manager, tab=0, focus_model=None, vocab_heard=None):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.settings = settings
        self.manager = manager
        self.work = Settings(path=settings.path, values=copy.deepcopy(settings.values))
        self.resize(980, 700)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.general = GeneralTab(self)
        self.trans = TranscriptionTab(self)
        self.vocab = VocabularyTab(self)
        self.output = OutputTab(self)
        self.advanced = AdvancedTab(self)
        for w, name in zip((self.general, self.trans, self.vocab, self.output, self.advanced), TABS):
            sc = QScrollArea()
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)  # pages fit the width; tables scroll inside
            sc.setWidget(w)
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            self.tabs.addTab(sc, name)
        self.tabs.setCurrentIndex(tab)
        row = QHBoxLayout()
        row.addWidget(button("Export…", self.export_settings, "Save all settings, including vocabulary, as one .json file."))
        row.addWidget(button("Import…", self.import_settings, "Load a settings file someone shared with you."))
        row.addWidget(button("Restore defaults", self.restore_defaults,
                             "Put every setting back to its default. Models, data and transcripts are kept."))
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Apply)
        bb.accepted.connect(self.ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        row.addWidget(bb)
        lay.addLayout(row)
        if focus_model:
            self.trans.focus_model(focus_model)
        if vocab_heard:
            self.vocab.add_heard(vocab_heard)

    # ------------------------------------------------------------ apply
    def collect(self):
        for t in (self.general, self.trans, self.vocab, self.output, self.advanced):
            t.collect(self.work)

    def validate(self):
        problems = []
        for key, label in (("general.output_folder", "Default output folder"),
                           ("general.data_folder", "Data folder")):
            stored = self.work.get(key)
            if not stored or stored == "data":
                continue
            folder = paths.from_stored(stored)
            if not paths._writable(folder):
                problems.append(f"{label}: {folder} cannot be written.")
        if not self.work.get("output.formats") and self.work.get("output.autosave") == "session+exports":
            problems.append("Output: tick at least one default export format, or set Auto-save to Session only.")
        return problems

    def apply(self):
        self.collect()
        problems = self.validate()
        if problems:
            QMessageBox.warning(self, "Options", "\n".join(problems))
            return False
        # model-manager actions changed these on the live settings while the dialog was open
        for k in ("transcription.locations", "transcription.location_cache", "transcription.download_folder"):
            self.work.values[k] = copy.deepcopy(self.settings.get(k))
        self.work.values["general.last_options_tab"] = self.tabs.currentIndex()
        self.settings.values = copy.deepcopy(self.work.values)
        try:
            self.settings.save()
        except OSError as ex:
            QMessageBox.warning(self, "Options", f"The settings could not be saved: {ex}")
        self.applied.emit()
        self.output.refresh_bold()
        self.advanced.refresh_bold()
        return True

    def ok(self):
        if self.apply():
            self.accept()

    def reject(self):
        self.settings.set("general.last_options_tab", self.tabs.currentIndex())
        super().reject()

    # ------------------------------------------------------------ settings files (FR-OPT-02/03)
    def export_settings(self):
        p, _ = QFileDialog.getSaveFileName(self, "Export settings", "sot-settings.json", "Settings (*.json)")
        if p:
            self.collect()
            self.work.export_to(p)

    def import_settings(self):
        p, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "Settings (*.json)")
        if not p:
            return
        try:
            self.work.import_from(p)
        except Exception as ex:
            QMessageBox.warning(self, "Import settings", str(ex))
            return
        self.reload_tabs()
        QMessageBox.information(self, "Import settings", "Settings loaded. Press OK or Apply to use them.")

    def restore_defaults(self):
        if QMessageBox.question(self, "Restore defaults",
                                "Put every setting back to its default?\n\nModels, data, transcripts, model "
                                "locations and vocabulary sets are kept.") != QMessageBox.Yes:
            return
        keep = ("general.data_folder", "transcription.download_folder", "transcription.locations",
                "transcription.location_cache", "vocabulary.sets", "vocabulary.active", "general.recent",
                "general.geometry", "general.first_run_done", "advanced.offline_auto_done")
        self.work.restore([k for k in DEFAULTS if k not in keep])
        self.reload_tabs()

    def reload_tabs(self):
        idx = self.tabs.currentIndex()
        for t in (self.general, self.trans, self.vocab, self.output, self.advanced):
            t.load(self.work)
        self.tabs.setCurrentIndex(idx)


# ====================================================================== General (section 5)
class GeneralTab(QWidget):
    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        f = QFormLayout(self)
        self.data = QLineEdit()
        self.data.setReadOnly(True)
        f.addRow("Data folder (logs, temp, job journal):", path_row(
            self.data, button("Change…", self.change_data), button("Open", lambda: system.open_folder(self.data.text()))))
        self.out = QLineEdit()
        self.out.setToolTip("Where transcripts are saved. Empty = Documents\\SOT Transcripts.")
        f.addRow("Default output folder:", path_row(self.out, button("Browse…", self.browse_out)))
        self.next_to = QCheckBox("Save next to the media file instead")
        f.addRow("", self.next_to)
        self.theme = combo([("system", "Follow Windows"), ("light", "Light"), ("dark", "Dark")])
        f.addRow("Theme:", self.theme)
        self.font = QSpinBox()
        self.font.setRange(9, 20)
        self.font.setSuffix(" pt")
        f.addRow("Font size in the Transcript tab:", self.font)
        self.ticks = {}
        for key, text, tip in [
            ("general.always_on_top", "Always on top", "Keep the window above other windows."),
            ("general.remember_recent", "Remember recent files", "Turning this off also clears the list."),
            ("general.open_last", "Open the last file at start-up", ""),
            ("general.keep_awake", "Keep the PC awake during a job", "Stops Windows from sleeping while a file is transcribed."),
            ("general.background_priority", "Background priority during a job", "Lets you keep working while a long file runs."),
            ("general.notify", "Notify when a job finishes", "A Windows notification when the window is minimised."),
            ("general.ask_unsaved", "Ask before closing with unsaved edits", "")]:
            cb = QCheckBox(text)
            cb.setToolTip(tip)
            self.ticks[key] = cb
            f.addRow("", cb)
        if paths.using_fallback():
            note = QLabel(f"The program folder is read-only, so settings are kept in {paths.settings_dir()}.")
            note.setWordWrap(True)
            f.addRow("", note)
        self.load(dlg.work)

    def load(self, s):
        self.data.setText(s.data_folder())
        self.out.setText(paths.from_stored(s.get("general.output_folder")) or paths.default_output_folder())
        self.next_to.setChecked(s.get("general.save_next_to_media"))
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.get("general.theme"))))
        self.font.setValue(int(s.get("general.font_size")))
        for k, cb in self.ticks.items():
            cb.setChecked(bool(s.get(k)))

    def collect(self, s):
        out = self.out.text().strip()
        s.set("general.output_folder", "" if os.path.normcase(out) == os.path.normcase(paths.default_output_folder())
              else paths.to_stored(out))
        s.set("general.save_next_to_media", self.next_to.isChecked())
        s.set("general.theme", self.theme.currentData())
        s.set("general.font_size", self.font.value())
        for k, cb in self.ticks.items():
            s.set(k, cb.isChecked())
        if not s.get("general.remember_recent"):
            s.set("general.recent", [])

    def browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "Default output folder", self.out.text())
        if d:
            self.out.setText(d)

    def change_data(self):
        """FR-OPT-05: Move my data there / Start fresh there."""
        old = self.data.text()
        new = QFileDialog.getExistingDirectory(self, "Data folder", old)
        if not new or os.path.normcase(os.path.abspath(new)) == os.path.normcase(os.path.abspath(old)):
            return
        if not paths._writable(new):
            QMessageBox.warning(self, "Data folder", f"{new} cannot be written.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Data folder")
        box.setText(f"Use {new} for logs, temp files and the job journal?")
        b_move = box.addButton("Move my data there", QMessageBox.AcceptRole)
        b_fresh = box.addButton("Start fresh there", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() not in (b_move, b_fresh):
            return
        if box.clickedButton() is b_move:
            def work(progress):
                for sub in ("logs", "jobs", "temp"):
                    src = os.path.join(old, sub)
                    if os.path.isdir(src):
                        copy_tree(src, os.path.join(new, sub), progress)
                for sub in ("jobs", "temp"):  # logs stay readable by the current log handler; old ones age out
                    src = os.path.join(old, sub)
                    if os.path.isdir(src):
                        shutil.rmtree(src, ignore_errors=True)
            _, err = run_with_progress(self, "Moving data", work)
            if err:
                QMessageBox.warning(self, "Data folder", f"Nothing was removed. The move stopped: {err}")
                return
        stored = paths.to_stored(new)
        self.dlg.work.set("general.data_folder", stored)
        self.data.setText(new)


# ====================================================================== Transcription (section 6) + model manager
class TranscriptionTab(QWidget):
    COLS = ["Model", "Languages", "Size", "Licence", "Location", "Status", ""]

    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        self.mgr = dlg.manager
        v = QVBoxLayout(self)
        top = QGridLayout()
        self.lang = combo(LANGS, tip="Auto-detect checks the first speech in the file.")
        self.model = QComboBox()
        self.model.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(12)
        self.model.setToolTip("Automatic picks the first installed model in the language's row below.")
        self.speed = combo([("fast", "Fast (beam 1)"), ("accurate", "Accurate (beam 5)")],
                           tip="Whisper only. Accurate is about twice as slow.")
        self.threads = QSpinBox()
        self.threads.setRange(0, 16)
        self.threads.setSpecialValueText("Auto (all cores)")
        self.threads.setToolTip("CPU threads for recognition. Reloads the model.")
        self.s_yue = combo(SCRIPTS)
        self.s_zh = combo(SCRIPTS)
        top.addWidget(QLabel("Language:"), 0, 0)
        top.addWidget(self.lang, 0, 1)
        top.addWidget(QLabel("Model:"), 0, 2)
        top.addWidget(self.model, 0, 3)
        top.addWidget(QLabel("Speed (Whisper):"), 1, 0)
        top.addWidget(self.speed, 1, 1)
        top.addWidget(QLabel("CPU threads:"), 1, 2)
        top.addWidget(self.threads, 1, 3)
        top.addWidget(QLabel("Cantonese script:"), 2, 0)
        top.addWidget(self.s_yue, 2, 1)
        top.addWidget(QLabel("Mandarin script:"), 2, 2)
        top.addWidget(self.s_zh, 2, 3)
        top.setColumnStretch(3, 1)
        top.setColumnStretch(1, 1)
        v.addLayout(top)

        g = QGroupBox("Automatic model choice (the first installed model in each row is used)")
        gl = QGridLayout(g)
        self.per = {}
        for c, h in enumerate(["1st choice", "2nd choice", "3rd choice"]):
            gl.addWidget(QLabel(f"<b>{h}</b>"), 0, c + 1)
        for r, (lang, name) in enumerate(LANGS[1:], 1):
            gl.addWidget(QLabel(name + ":"), r, 0)
            self.per[lang] = []
            for c in range(3):
                cb = QComboBox()
                cb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                cb.setMinimumContentsLength(12)
                gl.addWidget(cb, r, c + 1)
                self.per[lang].append(cb)
        note = QLabel("<i>Auto-detect: language check with the smallest installed Whisper model, then the "
                      "row above; SenseVoiceSmall when no Whisper model is installed.</i>")
        note.setWordWrap(True)
        gl.addWidget(note, 5, 0, 1, 3)
        gl.addWidget(button("Use model list order", self.reset_order,
                            "Follow the order in the model list, including future model list updates."), 5, 3,
                     Qt.AlignRight | Qt.AlignTop)
        v.addWidget(g)

        g = QGroupBox("Model folders")
        gl = QFormLayout(g)
        self.dl = QLineEdit()
        self.dl.setReadOnly(True)
        gl.addRow("Model download folder:", path_row(
            self.dl, button("Change…", self.change_download), button("Open", lambda: system.open_folder(self.dl.text()))))
        self.locs = QListWidget()
        self.locs.setMaximumHeight(70)
        locw = QWidget()
        lh = QHBoxLayout(locw)
        lh.setContentsMargins(0, 0, 0, 0)
        lh.addWidget(self.locs, 1)
        lb = QVBoxLayout()
        lb.addWidget(button("Add folder…", self.add_location,
                            "Any folder: another drive, a NAS share, a USB stick. Models there are used in place."))
        lb.addWidget(button("Remove", self.remove_location, "Stops using the folder; no file is deleted."))
        lb.addWidget(button("Refresh", self.refresh))
        lh.addLayout(lb)
        gl.addRow("Other model locations:", locw)
        v.addWidget(g)

        g = QGroupBox("Models")
        gv = QVBoxLayout(g)
        lrow = QHBoxLayout()
        self.list_label = QLabel()
        lrow.addWidget(self.list_label)
        lrow.addStretch(1)
        lrow.addWidget(button("Import model list\u2026", self.import_list,
                              "Use a newer model list file (models.json) you were given: new or updated models "
                              "appear below. Your installed models are kept."))
        self.b_builtin = button("Use built-in list", self.builtin_list, "Go back to the list built into the program.")
        lrow.addWidget(self.b_builtin)
        gv.addLayout(lrow)
        self.list_problem = QLabel()
        self.list_problem.setWordWrap(True)
        gv.addWidget(self.list_problem)
        rh = QHBoxLayout()
        self.rec_btn = button("Download recommended set", lambda: self.download_recommended())
        self.lang.currentIndexChanged.connect(self._update_rec_btn)  # Cantonese/Mandarin adds SenseVoiceSmall
        rh.addWidget(self.rec_btn)
        self.disk = QLabel()
        rh.addStretch(1)
        rh.addWidget(self.disk)
        gv.addLayout(rh)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setWordWrap(False)
        hh = self.table.horizontalHeader()
        for c in range(len(self.COLS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        for c, wdt in ((0, 215), (3, 105), (4, 110), (5, 110), (6, 205)):
            hh.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, wdt)
        self.table.setMinimumHeight(260)
        gv.addWidget(self.table)
        bh = QHBoxLayout()
        bh.addWidget(button("Add model from folder…", self.add_from_folder))
        bh.addWidget(button("Import model pack…", self.import_pack, "Bring models from a .zip made on another PC."))
        bh.addWidget(button("Export model pack…", self.export_pack,
                            "Put the selected installed models in one .zip for a PC without internet."))
        bh.addStretch(1)
        gv.addLayout(bh)
        self.offline_note = QLabel()
        gv.addWidget(self.offline_note)
        v.addWidget(g)
        self.rows = {}
        self._reset_order = False
        self.load(dlg.work)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(500)

    # ------------------------------------------------------------ load / collect
    def load(self, s):
        self.lang.setCurrentIndex(max(0, self.lang.findData(s.get("transcription.language"))))
        self.speed.setCurrentIndex(max(0, self.speed.findData(s.get("transcription.speed"))))
        self.threads.setValue(int(s.get("transcription.threads")))
        self.s_yue.setCurrentIndex(max(0, self.s_yue.findData(s.get("transcription.script_yue"))))
        self.s_zh.setCurrentIndex(max(0, self.s_zh.findData(s.get("transcription.script_zh"))))
        self.refresh(rescan=False)

    def collect(self, s):
        s.set("transcription.language", self.lang.currentData())
        s.set("transcription.model", self.model.currentData() or "auto")
        s.set("transcription.speed", self.speed.currentData())
        s.set("transcription.threads", self.threads.value())
        s.set("transcription.script_yue", self.s_yue.currentData())
        s.set("transcription.script_zh", self.s_zh.currentData())
        per = {}
        listed = self.mgr.listdata.get("automatic", {})
        for lang, cbs in self.per.items():
            chosen = [cb.currentData() for cb in cbs if cb.currentData()]
            if chosen != list(listed.get(lang, []))[:3]:
                per[lang] = chosen  # only your own changes are kept; the rest follows the model list
        s.set("transcription.per_language", per)

    def refresh(self, rescan=True):
        if rescan:
            self.mgr.scan()
        s = self.dlg.work
        models = self.mgr.models
        # model combo
        cur = self.model.currentData() if self.model.count() else s.get("transcription.model")
        self.model.clear()
        self.model.addItem("Automatic", "auto")
        for m in sorted(self.mgr.installed(), key=lambda m: m.name):
            self.model.addItem(m.name, m.key)
        self.model.setCurrentIndex(max(0, self.model.findData(cur)))
        # per-language rows
        for lang, cbs in self.per.items():
            wanted = [cb.currentData() for cb in cbs] if cbs[0].count() and not self._reset_order else \
                (self._order(lang) + [None] * 3)[:3]
            # catalogue models once (installed here or found in another location), plus unknown location models
            choices = [m for m in models.values() if lang in m.languages and
                       (m.source == "download" or (not m.catalog_id and (m.installed or m.status == "Location offline")))]
            for cb, want in zip(cbs, wanted):
                cb.blockSignals(True)
                cb.clear()
                cb.addItem("(none)", None)
                ready = lambda m: m.installed or (m.catalog_id and self.mgr.by_catalog(m.catalog_id))
                for m in sorted(choices, key=lambda m: (not ready(m), m.name)):
                    note = "" if m.installed else "  (other location)" if ready(m) else "  (not installed)"
                    cb.addItem(m.name + note, m.key)
                cb.setCurrentIndex(max(0, cb.findData(want)))
                cb.blockSignals(False)
        self._reset_order = False
        li = self.mgr.list_info
        src = "built into the program" if li["source"] == "built-in" else "imported"
        self.list_label.setText(f"Model list <b>{li['data']['list_version']}</b> ({src}) \u00b7 "
                                f"{len(li['data']['models'])} models")
        self.list_label.setToolTip(li["path"])
        self.list_problem.setText(f"<span style='color:#c0392b'>{li['problem']}</span>" if li["problem"] else "")
        self.list_problem.setVisible(bool(li["problem"]))
        self.b_builtin.setEnabled(li["source"] == "imported" or os.path.isfile(self._override()))
        self.dl.setText(self.dlg.settings.download_folder())
        self.locs.clear()
        for loc in self.dlg.settings.locations():
            self.locs.addItem(loc)
        self._update_rec_btn()
        self.build_table()

    def build_table(self):
        rank = {"Must": 0, "Should": 1, "Could": 2}
        pos = {e["id"]: i for i, e in enumerate(self.mgr.catalog)}  # the model list's order (English first)
        mdl = sorted(self.mgr.models.values(),
                     key=lambda m: (m.source != "download", not m.installed,
                                    rank.get((m.entry or {}).get("offered", "Could"), 2),
                                    pos.get((m.entry or {}).get("id"), len(pos)), m.name))
        self.table.setRowCount(len(mdl))
        self.rows = {}
        for r, m in enumerate(mdl):
            e = m.entry or {}
            items = [m.name, " ".join(x.upper() for x in m.languages), human_size(m.size or e.get("size", 0)),
                     e.get("license", "see model folder"), "Download folder" if m.source == "download" else m.location,
                     m.status]
            for c, t in enumerate(items):
                it = QTableWidgetItem(t)
                it.setData(Qt.UserRole, m.key)
                if c in (3, 4) and not it.toolTip():
                    it.setToolTip(t)
                if c == 0 and e.get("note"):
                    it.setToolTip(e["note"])
                if c == 4:
                    it.setToolTip(m.folder)
                if c == 1:
                    it.setToolTip(", ".join(LANG_NAMES.get(x, x) for x in m.languages))
                self.table.setItem(r, c, it)
            if m.status == "Update available":
                self.table.item(r, 5).setForeground(QColor("#c07000"))
                self.table.item(r, 5).setToolTip("A newer revision is in the model list; this one still works.")
            if m.status in ("Downloading", "Paused"):  # FR: progress bar in the Status cell
                self.table.setCellWidget(r, 5, self._bar(m.progress, m.status))
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(2, 0, 2, 0)
            h.setSpacing(2)
            if m.source == "download":
                b_dl = QPushButton("Download")
                b_dl.clicked.connect(lambda _=False, k=m.key: self.download(k))
                listed = m.key in self.mgr.by_id
                b_dl.setEnabled(listed and m.status in ("Not installed", "Paused", "Damaged", "Update available")
                                and not self.dlg.settings.get("advanced.offline"))
                if m.status == "Paused":
                    b_dl.setText("Resume")
                elif m.status == "Update available":
                    b_dl.setText("Update")
                    b_dl.setToolTip("Download the revision in the model list. The model you have keeps working "
                                    "until the new files are in place.")
                b_pause = QPushButton("Pause")
                b_pause.clicked.connect(lambda _=False, k=m.key: self.mgr.pause_download(k))
                b_pause.setEnabled(m.status == "Downloading")
                b_del = QPushButton("Delete")
                b_del.clicked.connect(lambda _=False, k=m.key: self.delete(k))
                b_del.setEnabled(m.status in ("Installed", "Update available", "Paused", "Damaged"))
                for b in (b_dl, b_pause, b_del):
                    b.setStyleSheet("padding: 1px 6px;")
                    h.addWidget(b)
            else:
                b_rm = QPushButton("Remove this location")
                b_rm.clicked.connect(lambda _=False, loc=m.location: self.remove_location(loc))
                h.addWidget(b_rm)
            self.table.setCellWidget(r, 6, w)
            self.rows[m.key] = r
        self.table.resizeRowsToContents()
        folder = self.dlg.settings.download_folder()
        self.disk.setText(f"Free on the download drive: {human_size(free_disk(folder))}")
        off = self.dlg.settings.get("advanced.offline")
        self.offline_note.setText("<i>Offline Mode is on: Download is greyed out. Other model locations, Add model "
                                  "from folder and Import still work.</i>" if off else "")
        self.rec_btn.setEnabled(not off)

    def _order(self, lang):
        return self.mgr.automatic_order(lang) if not self._reset_order else \
            list(self.mgr.listdata.get("automatic", {}).get(lang, []))

    def _override(self):
        from .. import modellist
        return modellist.override_path()

    def reset_order(self):
        self._reset_order = True
        self.refresh(rescan=False)

    def import_list(self):
        if any(t.is_alive() for t, _, _ in self.mgr.downloads.values()):
            QMessageBox.warning(self, "Import model list", "Pause the running downloads first.")
            return
        if import_model_list(self, self.mgr):
            self.refresh(rescan=False)
            self.dlg.settings_changed_models = True

    def builtin_list(self):
        from .. import modellist
        if QMessageBox.question(self, "Use built-in list", "Stop using the imported model list and go back to the "
                                "list built into the program?\n\nInstalled models are kept.") != QMessageBox.Yes:
            return
        modellist.restore_builtin()
        self.mgr.reload_list()
        self.refresh()
        self.dlg.settings_changed_models = True

    def _bar(self, frac, status="Downloading"):
        bar = QProgressBar()
        bar.setRange(0, 1000)
        bar.setValue(int(frac * 1000))
        bar.setFormat(f"{status} {frac:.0%}")
        bar.setTextVisible(True)
        return bar

    def focus_model(self, key):
        r = self.rows.get(key)
        if r is not None:
            self.table.selectRow(r)
            self.table.scrollToItem(self.table.item(r, 0))

    def tick(self):
        busy = False
        changed = False
        for cid, (t, stop, state) in list(self.mgr.downloads.items()):
            r = self.rows.get(cid)
            if t.is_alive():
                busy = True
                if r is not None:
                    pct = state["done"] / state["total"] if state["total"] else 0
                    bar = self.table.cellWidget(r, 5)
                    if bar is None:
                        self.table.setCellWidget(r, 5, self._bar(pct))
                    else:
                        bar.setValue(int(pct * 1000))
                        bar.setFormat(f"Downloading {pct:.0%}")
            elif not state.get("reported"):
                state["reported"] = True
                changed = True
                if state.get("error"):
                    QMessageBox.warning(self, "Download", f"{self.mgr.by_id[cid]['name']}: {state['error']}")
        if changed:
            self.refresh()
            self.dlg.settings_changed_models = True

    # ------------------------------------------------------------ actions
    def download(self, cid):
        e = self.mgr.by_id[cid]
        dest = self.mgr.folder_for(e)
        upd = self.mgr.find(cid) and self.mgr.find(cid).status == "Update available"
        text = (f"<b>{e['name']}</b>{' - update' if upd else ''}<br><br>"
                f"Source: huggingface.co/{e['repo']} (revision {e['revision'][:10]})<br>"
                f"Size: {human_size(e['size'])}<br>Licence: <a href='{e.get('license_url', '')}'>{e['license']}</a><br>"
                f"Destination: {dest}<br><br>Every file is checked by SHA-256. Download now?")
        box = QMessageBox(QMessageBox.Question, "Download model", text, QMessageBox.Yes | QMessageBox.No, self)
        box.setTextFormat(Qt.RichText)
        if box.exec() != QMessageBox.Yes:
            return
        try:
            self.mgr.start_download(cid)
        except Exception as ex:
            QMessageBox.warning(self, "Download", str(ex))
            return
        self.build_table()

    def _cantonese_mandarin(self):
        """The recommended set adds the Cantonese/Mandarin models when that is the chosen language."""
        return self.lang.currentData() in ("yue", "zh")

    def _update_rec_btn(self, *_):
        rec = self.mgr.recommended(self._cantonese_mandarin())
        self.rec_btn.setText(f"Download recommended set for this PC "
                             f"({human_size(sum(self.mgr.by_id[c]['size'] for c in rec))})")
        self.rec_btn.setToolTip(" + ".join(self.mgr.by_id[c]["name"] for c in rec))

    def download_recommended(self, cantonese_mandarin=None):
        if cantonese_mandarin is None:
            cantonese_mandarin = self._cantonese_mandarin()
        rec = [c for c in self.mgr.recommended(cantonese_mandarin) if not self.mgr.by_catalog(c)]
        if not rec:
            QMessageBox.information(self, "Models", "The recommended set is already installed.")
            return
        size = sum(self.mgr.by_id[c]["size"] for c in rec)
        names = "\n".join(f"  {self.mgr.by_id[c]['name']} - {human_size(self.mgr.by_id[c]['size'])}, "
                          f"{self.mgr.by_id[c]['license']}" for c in rec)
        if QMessageBox.question(self, "Download recommended set",
                                f"Download from huggingface.co (pinned revisions, SHA-256 checked):\n\n{names}\n\n"
                                f"Total {human_size(size)} to {self.dlg.settings.download_folder()}") != QMessageBox.Yes:
            return
        for c in rec:
            try:
                self.mgr.start_download(c)
            except Exception as ex:
                QMessageBox.warning(self, "Download", str(ex))
                break
        self.build_table()

    def delete(self, key):
        m = self.mgr.find(key)
        if QMessageBox.question(self, "Delete model", f"Delete {m.name} from\n{m.folder}?") != QMessageBox.Yes:
            return
        self.mgr.pause_download(key)
        try:
            self.mgr.delete(m)
        except Exception as ex:
            QMessageBox.warning(self, "Delete model", str(ex))
        self.refresh()
        self.dlg.settings_changed_models = True

    def add_location(self):
        d = QFileDialog.getExistingDirectory(self, "Add a folder with models")
        if not d:
            return
        try:
            found = self.mgr.add_location(d)
        except Exception as ex:
            QMessageBox.warning(self, "Other model locations", str(ex))
            return
        self.refresh()
        self.dlg.settings_changed_models = True
        if found:
            QMessageBox.information(self, "Other model locations",
                                    f"Found {len(found)} model folder(s):\n" + "\n".join(f"  {f} ({KIND_LABEL[k]})" for f, k in found))
        else:
            QMessageBox.information(self, "Other model locations",
                                    "No model was found in that folder or its subfolders yet. It is kept and searched "
                                    "again at start-up and on Refresh.")

    def remove_location(self, loc=None):
        if loc is None:
            it = self.locs.currentItem()
            if not it:
                return
            loc = it.text()
        if QMessageBox.question(self, "Remove location", f"Stop using models in\n{loc}?\n\nNo file is deleted.") \
                != QMessageBox.Yes:
            return
        self.mgr.remove_location(loc)
        self.refresh()
        self.dlg.settings_changed_models = True

    def change_download(self):
        old = self.dlg.settings.download_folder()
        new = QFileDialog.getExistingDirectory(self, "Model download folder", old)
        if not new or os.path.normcase(os.path.abspath(new)) == os.path.normcase(os.path.abspath(old)):
            return
        if not paths._writable(new):
            QMessageBox.warning(self, "Model download folder", f"{new} cannot be written.")
            return
        if any(t.is_alive() for t, _, _ in self.mgr.downloads.values()):
            QMessageBox.warning(self, "Model download folder", "Pause the running downloads first.")
            return
        box = QMessageBox(self)
        box.setWindowTitle("Model download folder")
        box.setText(f"Use {new} for downloaded models?")
        b_move = box.addButton("Move my models there", QMessageBox.AcceptRole)
        b_use = box.addButton("Just use the new folder", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() not in (b_move, b_use):
            return
        move = box.clickedButton() is b_move
        _, err = run_with_progress(self, "Moving models", lambda p: self.mgr.move_download_folder(new, move, p))
        if err:
            QMessageBox.warning(self, "Model download folder", f"Nothing was removed. The move stopped: {err}")
        self.dlg.settings.save()
        self.refresh()
        self.dlg.settings_changed_models = True

    def add_from_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add model from folder")
        if not d:
            return
        from ..engines import detect_type
        kind, info = detect_type(d)
        if not kind:
            QMessageBox.warning(self, "Add model", "This folder does not hold a model this program can use.\n\n"
                                "Files expected:\n" + "\n".join("  " + x for x in info))
            return
        box = QMessageBox(self)
        box.setWindowTitle("Add model")
        box.setText(f"Found a {KIND_LABEL[kind]} model in\n{d}")
        b_copy = box.addButton("Copy into the download folder", QMessageBox.AcceptRole)
        b_use = box.addButton("Use it where it is", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() not in (b_copy, b_use):
            return
        _, err = run_with_progress(self, "Adding model",
                                   lambda p: self.mgr.add_model_folder(d, box.clickedButton() is b_copy, p))
        if err:
            QMessageBox.warning(self, "Add model", err)
        self.dlg.settings.save()
        self.refresh()
        self.dlg.settings_changed_models = True

    def import_pack(self):
        p, _ = QFileDialog.getOpenFileName(self, "Import model pack", "", "Model pack (*.zip)")
        if not p:
            return
        res, err = run_with_progress(self, "Importing models", lambda pr: self.mgr.import_pack(p, pr))
        if err:
            QMessageBox.warning(self, "Import model pack", err)
        else:
            QMessageBox.information(self, "Import model pack", "Imported: " + ", ".join(res))
        self.dlg.settings.save()
        self.refresh()
        self.dlg.settings_changed_models = True

    def export_pack(self):
        keys = []
        for idx in self.table.selectionModel().selectedRows():
            k = self.table.item(idx.row(), 0).data(Qt.UserRole)
            m = self.mgr.find(k)
            if m and m.installed:
                keys.append(k)
        if not keys:
            QMessageBox.information(self, "Export model pack", "Select one or more installed models in the list first.")
            return
        size = sum(self.mgr.find(k).size for k in keys)
        p, _ = QFileDialog.getSaveFileName(self, "Export model pack", "sot-models.zip", "Model pack (*.zip)")
        if not p:
            return
        if free_disk(os.path.dirname(p)) < size + 1e8:
            QMessageBox.warning(self, "Export model pack", f"Not enough free space: the pack needs {human_size(size)}.")
            return
        _, err = run_with_progress(self, "Exporting models", lambda pr: self.mgr.export_pack(keys, p, pr))
        if err:
            QMessageBox.warning(self, "Export model pack", err)
        else:
            QMessageBox.information(self, "Export model pack", f"Saved {p} ({human_size(size)}).")


# ====================================================================== Vocabulary (section 8)
class VocabularyTab(QWidget):
    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        v = QVBoxLayout(self)
        v.addWidget(QLabel("<b>Hotwords: Whisper only. Replacements: all models.</b>"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Vocabulary set:"))
        self.set_combo = QComboBox()
        self.set_combo.currentTextChanged.connect(self.switch_set)
        row.addWidget(self.set_combo, 1)
        for text, slot in (("New", self.new_set), ("Rename", self.rename_set), ("Duplicate", self.dup_set),
                           ("Delete", self.del_set)):
            row.addWidget(button(text, slot))
        v.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Use this set for:"))
        self.scope = combo([("all", "All languages")] + LANGS[1:])
        row.addWidget(self.scope)
        row.addStretch(1)
        v.addLayout(row)
        g = QGroupBox("Hotwords (Whisper): names, products and jargon, one per line, most important first")
        gl = QVBoxLayout(g)
        hr = QHBoxLayout()
        hr.addWidget(QLabel("Fill from an agenda, attendee list or e-mail:"))
        hr.addWidget(button("Names from documents\u2026", self.names_from_files,
                            "Word, Excel, PDF, text or saved e-mail (.eml): people, organisations and special terms "
                            "are found for you to review."))
        hr.addWidget(button("Names from pasted text\u2026", self.names_from_paste,
                            "Paste an e-mail or agenda; the names in it are found for you to review."))
        hr.addStretch(1)
        gl.addLayout(hr)
        self.hot = QPlainTextEdit()
        self.hot.setPlaceholderText("Marta Quinteiro\nTeodoro Valente\nNovalite")
        self.hot.textChanged.connect(self.count_hot)
        gl.addWidget(self.hot)
        self.hot_count = QLabel()
        gl.addWidget(self.hot_count)
        v.addWidget(g, 1)
        g = QGroupBox("Replacements (all models): run after recognition, in list order")
        gl = QVBoxLayout(g)
        self.rep = QTableWidget(0, 4)
        self.rep.setHorizontalHeaderLabels(["Heard", "Correct", "Whole word", "Match case"])
        self.rep.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.rep.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.rep.verticalHeader().hide()
        gl.addWidget(self.rep)
        row = QHBoxLayout()
        row.addWidget(button("Add row", lambda: self.add_row()))
        row.addWidget(button("Remove row", self.remove_row))
        row.addWidget(button("Move up", lambda: self.move_row(-1)))
        row.addWidget(button("Move down", lambda: self.move_row(1)))
        row.addStretch(1)
        row.addWidget(button("Import…", self.import_lists, "Hotwords from .txt, replacements from .csv."))
        row.addWidget(button("Export…", self.export_lists))
        row.addWidget(button("Test…", self.test))
        gl.addLayout(row)
        v.addWidget(g, 2)
        self.sets = {}
        self.current = None
        self.load(dlg.work)

    def load(self, s):
        self.sets = copy.deepcopy(s.get("vocabulary.sets")) or {"Default": {"scope": "all", "hotwords": [], "replacements": []}}
        self.current = None
        self.set_combo.blockSignals(True)
        self.set_combo.clear()
        self.set_combo.addItems(list(self.sets))
        self.set_combo.blockSignals(False)
        active = s.get("vocabulary.active")
        self.set_combo.setCurrentText(active if active in self.sets else next(iter(self.sets)))
        self.switch_set(self.set_combo.currentText())

    def store_current(self):
        if self.current is None or self.current not in self.sets:
            return
        reps = []
        for r in range(self.rep.rowCount()):
            heard = (self.rep.item(r, 0).text() if self.rep.item(r, 0) else "").strip()
            corr = self.rep.item(r, 1).text() if self.rep.item(r, 1) else ""
            if not heard:
                continue
            reps.append({"heard": heard, "correct": corr,
                         "whole": self.rep.item(r, 2).checkState() == Qt.Checked,
                         "case": self.rep.item(r, 3).checkState() == Qt.Checked})
        self.sets[self.current] = {"scope": self.scope.currentData(),
                                   "hotwords": [l.strip() for l in self.hot.toPlainText().splitlines() if l.strip()],
                                   "replacements": reps}

    def switch_set(self, name):
        if not name:
            return
        self.store_current()
        self.current = name
        vs = self.sets.get(name, {})
        self.scope.setCurrentIndex(max(0, self.scope.findData(vs.get("scope", "all"))))
        self.hot.setPlainText("\n".join(vs.get("hotwords", [])))
        self.rep.setRowCount(0)
        for r in vs.get("replacements", []):
            self.add_row(r.get("heard", ""), r.get("correct", ""), r.get("whole", True), r.get("case", False))
        self.count_hot()

    def collect(self, s):
        self.store_current()
        s.set("vocabulary.sets", copy.deepcopy(self.sets))
        s.set("vocabulary.active", self.current or "Default")

    def count_hot(self):
        words = " ".join(self.hot.toPlainText().split()).split()
        n = len(words)
        color = "#c07000" if n > HOTWORD_LIMIT * 0.9 else "palette(text)"
        msg = f"{n} of about {HOTWORD_LIMIT} words"
        if n > HOTWORD_LIMIT:
            msg += " - cut: " + " ".join(words[HOTWORD_LIMIT:])[:200]
        self.hot_count.setText(f"<span style='color:{color}'>{msg}</span>")

    def add_row(self, heard="", correct="", whole=True, case=False):
        r = self.rep.rowCount()
        self.rep.insertRow(r)
        self.rep.setItem(r, 0, QTableWidgetItem(heard))
        self.rep.setItem(r, 1, QTableWidgetItem(correct))
        for c, val in ((2, whole), (3, case)):
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            it.setCheckState(Qt.Checked if val else Qt.Unchecked)
            self.rep.setItem(r, c, it)
        return r

    def remove_row(self):
        rows = sorted({i.row() for i in self.rep.selectedIndexes()}, reverse=True)
        for r in rows:
            self.rep.removeRow(r)

    def move_row(self, d):
        r = self.rep.currentRow()
        t = r + d
        if r < 0 or t < 0 or t >= self.rep.rowCount():
            return
        vals = [(self.rep.item(x, 0).text(), self.rep.item(x, 1).text(), self.rep.item(x, 2).checkState(),
                 self.rep.item(x, 3).checkState()) for x in (r, t)]
        for x, v in zip((t, r), vals):
            self.rep.item(x, 0).setText(v[0])
            self.rep.item(x, 1).setText(v[1])
            self.rep.item(x, 2).setCheckState(v[2])
            self.rep.item(x, 3).setCheckState(v[3])
        self.rep.setCurrentCell(t, self.rep.currentColumn())

    def names_from_files(self):
        from .. import names
        files, _ = QFileDialog.getOpenFileNames(self, "Documents with names", "", names.FILE_FILTER)
        if files:
            self._names_dialog(files, None)

    def names_from_paste(self):
        text, ok = QInputDialog.getMultiLineText(self, "Paste text", "Paste an e-mail, agenda or attendee list:")
        if ok and text.strip():
            self._names_dialog([], text)

    def _names_dialog(self, files, pasted):
        from .namesdlg import NamesDialog
        self.store_current()
        dlg = NamesDialog(self, self.sets, self.current, files, pasted)
        if not dlg.exec():
            return
        self.sets = dlg.apply_to(self.sets)
        target = dlg.result_set
        if dlg.result_new:
            self.set_combo.blockSignals(True)
            self.set_combo.addItem(target)
            self.set_combo.blockSignals(False)
        self.current = None  # the tab shows the sets as they are now
        if dlg.make_active.isChecked() or target == self.set_combo.currentText():
            self.set_combo.setCurrentText(target)
        self.switch_set(self.set_combo.currentText())
        self.dlg.work.set("vocabulary.active", self.set_combo.currentText())

    def add_heard(self, text):
        """Edit > Add Selection to Vocabulary (FR-VOC-05)."""
        r = self.add_row(text.strip(), "")
        self.rep.setCurrentCell(r, 1)
        self.rep.editItem(self.rep.item(r, 1))

    def new_set(self):
        name, ok = QInputDialog.getText(self, "New vocabulary set", "Name:")
        if ok and name.strip() and name not in self.sets:
            self.store_current()
            self.sets[name] = {"scope": "all", "hotwords": [], "replacements": []}
            self.set_combo.addItem(name)
            self.set_combo.setCurrentText(name)

    def rename_set(self):
        old = self.current
        name, ok = QInputDialog.getText(self, "Rename vocabulary set", "Name:", text=old)
        if ok and name.strip() and name not in self.sets:
            self.store_current()
            self.sets = {(name if k == old else k): v for k, v in self.sets.items()}
            self.current = name
            i = self.set_combo.currentIndex()
            self.set_combo.blockSignals(True)
            self.set_combo.setItemText(i, name)
            self.set_combo.blockSignals(False)

    def dup_set(self):
        self.store_current()
        name = self.current + " copy"
        while name in self.sets:
            name += " copy"
        self.sets[name] = copy.deepcopy(self.sets[self.current])
        self.set_combo.addItem(name)
        self.set_combo.setCurrentText(name)

    def del_set(self):
        if len(self.sets) <= 1:
            QMessageBox.information(self, "Vocabulary", "At least one set is needed.")
            return
        if QMessageBox.question(self, "Delete set", f"Delete the set '{self.current}'?") != QMessageBox.Yes:
            return
        name = self.current
        self.current = None
        del self.sets[name]
        i = self.set_combo.findText(name)
        self.set_combo.removeItem(i)

    def import_lists(self):
        p, _ = QFileDialog.getOpenFileName(self, "Import", "", "Hotwords or replacements (*.txt *.csv)")
        if not p:
            return
        try:
            if p.lower().endswith(".csv"):
                with open(p, encoding="utf-8-sig", newline="") as f:
                    for row in csv.reader(f):
                        if not row or row[0].strip().lower() == "heard":
                            continue
                        whole = (row[2].strip().lower() not in ("0", "no", "false")) if len(row) > 2 else True
                        case = (row[3].strip().lower() in ("1", "yes", "true")) if len(row) > 3 else False
                        self.add_row(row[0], row[1] if len(row) > 1 else "", whole, case)
            else:
                with open(p, encoding="utf-8-sig") as f:
                    extra = [l.strip() for l in f if l.strip()]
                cur = self.hot.toPlainText().rstrip()
                self.hot.setPlainText((cur + "\n" if cur else "") + "\n".join(extra))
        except (OSError, UnicodeDecodeError) as ex:
            QMessageBox.warning(self, "Import", str(ex))

    def export_lists(self):
        self.store_current()
        d = QFileDialog.getExistingDirectory(self, "Export hotwords (.txt) and replacements (.csv) to")
        if not d:
            return
        vs = self.sets[self.current]
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in self.current)
        with open(os.path.join(d, f"{safe}_hotwords.txt"), "w", encoding="utf-8", newline="\r\n") as f:
            f.write("\n".join(vs["hotwords"]) + "\n")
        with open(os.path.join(d, f"{safe}_replacements.csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, lineterminator="\r\n")
            w.writerow(["Heard", "Correct", "Whole word", "Match case"])
            for r in vs["replacements"]:
                w.writerow([r["heard"], r["correct"], "yes" if r.get("whole", True) else "no",
                            "yes" if r.get("case") else "no"])
        QMessageBox.information(self, "Export", f"Saved {safe}_hotwords.txt and {safe}_replacements.csv in {d}.")

    def test(self):
        self.store_current()
        text, ok = QInputDialog.getMultiLineText(self, "Test replacements", "Paste a sentence:")
        if not ok or not text:
            return
        reps = self.sets[self.current]["replacements"]
        out, log = apply_replacements(text, reps)
        if not log:
            for script in ("hk", "cn"):
                o2, l2 = apply_replacements(convert_script(text, script), reps, script)
                if l2:
                    out, log = o2, l2
                    break
        detail = "\n".join(f"Replaced '{h}' → '{c}' ×{n}" for h, c, n in log) or "No replacement matched."
        QMessageBox.information(self, "Test replacements", f"{out}\n\n{detail}")


# ====================================================================== Output (section 9)
class OutputTab(QWidget):
    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        f = QFormLayout(self)
        self.autosave = combo([("off", "Off"), ("session", "Session only"), ("session+exports", "Session + default exports")])
        f.addRow("Auto-save after each job:", self.autosave)
        fw = QWidget()
        fg = QGridLayout(fw)
        fg.setContentsMargins(0, 0, 0, 0)
        self.formats = {}
        for i, (k, (label, ext)) in enumerate(FORMATS.items()):
            cb = QCheckBox(f"{label} ({os.path.splitext(ext)[1]})")
            self.formats[k] = cb
            fg.addWidget(cb, i // 4, i % 4)
        f.addRow("Default export formats:", fw)
        self.save_to = combo([("output", "Output folder (General)"), ("media", "Next to the media file"), ("ask", "Ask each time")])
        f.addRow("Save to:", self.save_to)
        self.pattern = QLineEdit()
        self.pattern.setToolTip("Tokens: {name} {date} {time} {model} {lang}")
        self.preview = QLabel()
        self.pattern.textChanged.connect(self.update_preview)
        f.addRow("File name pattern:", self.pattern)
        f.addRow("", self.preview)
        self.exists = combo([("ask", "Ask"), ("number", "Add a number"), ("overwrite", "Overwrite")])
        f.addRow("If the file exists:", self.exists)
        self.times = combo([("none", "None"), ("paragraph", "Each paragraph"), ("segment", "Each segment")])
        f.addRow("Times in text formats:", self.times)
        self.tfmt = combo([("hh:mm:ss", "hh:mm:ss"), ("mm:ss", "mm:ss"), ("hh:mm:ss.mmm", "hh:mm:ss.mmm")])
        f.addRow("Time format:", self.tfmt)
        pw = QWidget()
        ph = QHBoxLayout(pw)
        ph.setContentsMargins(0, 0, 0, 0)
        self.pause = QDoubleSpinBox()
        self.pause.setRange(0.5, 30)
        self.pause.setSuffix(" s")
        self.chars = QSpinBox()
        self.chars.setRange(100, 5000)
        self.chars.setSingleStep(50)
        self.chars.setSuffix(" characters")
        ph.addWidget(QLabel("New paragraph after a pause of"))
        ph.addWidget(self.pause)
        ph.addWidget(QLabel("or after"))
        ph.addWidget(self.chars)
        ph.addStretch(1)
        f.addRow("Paragraphs:", pw)
        sw = QWidget()
        sh = QHBoxLayout(sw)
        sh.setContentsMargins(0, 0, 0, 0)
        self.sub_chars = QSpinBox()
        self.sub_chars.setRange(16, 80)
        self.sub_lines = QSpinBox()
        self.sub_lines.setRange(1, 3)
        self.sub_max = QDoubleSpinBox()
        self.sub_max.setRange(2, 15)
        self.sub_min = QDoubleSpinBox()
        self.sub_min.setRange(0.3, 5)
        for lbl, wdg in (("chars/line", self.sub_chars), ("lines", self.sub_lines), ("longest s", self.sub_max),
                         ("shortest s", self.sub_min)):
            sh.addWidget(wdg)
            sh.addWidget(QLabel(lbl))
        sh.addStretch(1)
        f.addRow("Subtitle limits:", sw)
        self.enc = combo([("utf-8", "UTF-8"), ("utf-8-sig", "UTF-8 with BOM")],
                         tip="CSV always has a BOM so Excel shows Chinese.")
        f.addRow("Text encoding:", self.enc)
        self.stats_header = QCheckBox("Statistics header in TXT and DOCX")
        f.addRow("", self.stats_header)
        self.page = combo([("Letter", "Letter"), ("A4", "A4")])
        f.addRow("Page size (DOCX):", self.page)
        self.fillers = QCheckBox("Remove filler words")
        self.filler_list = QLineEdit()
        self.filler_list.setToolTip("Comma-separated.")
        f.addRow("", self.fillers)
        f.addRow("Filler words:", self.filler_list)
        self.widgets = {"output.autosave": self.autosave, "output.save_to": self.save_to, "output.pattern": self.pattern,
                        "output.if_exists": self.exists, "output.times": self.times, "output.time_format": self.tfmt}
        self.load(dlg.work)

    def load(self, s):
        self.autosave.setCurrentIndex(max(0, self.autosave.findData(s.get("output.autosave"))))
        for k, cb in self.formats.items():
            cb.setChecked(k in s.get("output.formats"))
        save_to = "media" if s.get("general.save_next_to_media") else s.get("output.save_to")
        self.save_to.setCurrentIndex(max(0, self.save_to.findData(save_to)))
        self.pattern.setText(s.get("output.pattern"))
        self.exists.setCurrentIndex(max(0, self.exists.findData(s.get("output.if_exists"))))
        self.times.setCurrentIndex(max(0, self.times.findData(s.get("output.times"))))
        self.tfmt.setCurrentIndex(max(0, self.tfmt.findData(s.get("output.time_format"))))
        self.pause.setValue(float(s.get("output.para_pause")))
        self.chars.setValue(int(s.get("output.para_chars")))
        self.sub_chars.setValue(int(s.get("output.sub_chars")))
        self.sub_lines.setValue(int(s.get("output.sub_lines")))
        self.sub_max.setValue(float(s.get("output.sub_max")))
        self.sub_min.setValue(float(s.get("output.sub_min")))
        self.enc.setCurrentIndex(max(0, self.enc.findData(s.get("output.encoding"))))
        self.stats_header.setChecked(s.get("output.stats_header"))
        self.page.setCurrentIndex(max(0, self.page.findData(s.get("output.page_size"))))
        self.fillers.setChecked(s.get("output.remove_fillers"))
        self.filler_list.setText(", ".join(s.get("output.filler_list")))
        self.update_preview()

    def collect(self, s):
        s.set("output.autosave", self.autosave.currentData())
        s.set("output.formats", [k for k, cb in self.formats.items() if cb.isChecked()])
        s.set("output.save_to", self.save_to.currentData())
        if self.save_to.currentData() == "media":
            s.set("general.save_next_to_media", True)
        elif s.get("general.save_next_to_media"):
            s.set("output.save_to", "media")
        s.set("output.pattern", self.pattern.text().strip() or DEFAULTS["output.pattern"])
        s.set("output.if_exists", self.exists.currentData())
        s.set("output.times", self.times.currentData())
        s.set("output.time_format", self.tfmt.currentData())
        s.set("output.para_pause", self.pause.value())
        s.set("output.para_chars", self.chars.value())
        s.set("output.sub_chars", self.sub_chars.value())
        s.set("output.sub_lines", self.sub_lines.value())
        s.set("output.sub_max", self.sub_max.value())
        s.set("output.sub_min", self.sub_min.value())
        s.set("output.encoding", self.enc.currentData())
        s.set("output.stats_header", self.stats_header.isChecked())
        s.set("output.page_size", self.page.currentData())
        s.set("output.remove_fillers", self.fillers.isChecked())
        s.set("output.filler_list", [x.strip() for x in self.filler_list.text().split(",") if x.strip()])

    def update_preview(self):
        meta = {"stem": "board-meeting", "model_short": "sensevoice-small-int8", "language": "en"}
        self.preview.setText(f"<i>Preview: {base_name(meta, self.pattern.text() or DEFAULTS['output.pattern'])}.txt</i>")

    def refresh_bold(self):
        pass


# ====================================================================== Advanced (section 10)
META = {
    "advanced.beam": ("Beam size", "int", (0, 10), "0 = follow Speed (Fast 1, Accurate 5). Larger is slower and a little more accurate."),
    "advanced.temperature_fallback": ("Temperature fallback", "bool", None, "Retry a window at 0.2, 0.4… 1.0 when it looks wrong."),
    "advanced.compression_ratio": ("Compression-ratio limit", "float", (1.5, 3.0, 0.1), "Windows whose text repeats itself more than this are retried."),
    "advanced.log_prob": ("Log-probability limit", "float", (-2.0, 0.0, 0.1), "Windows less certain than this are retried."),
    "advanced.no_speech": ("No-speech limit", "float", (0.0, 1.0, 0.05), "Windows more likely silent than this are skipped."),
    "advanced.carry_previous": ("Carry previous text into the next window", "bool", None, "Warning: can cause repeat loops such as 'Thank you for watching'."),
    "advanced.repetition_penalty": ("Repetition penalty", "float", (1.0, 1.2, 0.01), "Above 1.00 discourages repeated words."),
    "advanced.word_timestamps": ("Word timestamps", "bool", None, "Tighter subtitle splits; a little slower."),
    "advanced.skip_silence": ("Skip long silences inside a window (s)", "float", (0.5, 10.0, 0.5), "Only with word timestamps."),
    "advanced.vad_threshold": ("Speech threshold", "float", (0.1, 0.9, 0.05), "Lower finds quieter speech but also more noise."),
    "advanced.vad_min_silence_ms": ("Shortest silence that splits speech (ms)", "int", (100, 2000), ""),
    "advanced.vad_pad_ms": ("Padding around speech (ms)", "int", (0, 1000), "Keeps the first and last syllables."),
    "advanced.max_piece_s": ("Longest piece (SenseVoice, Parakeet, Moonshine) (s)", "int", (5, 25), ""),
    "advanced.normalise": ("Normalise volume", "bool", None, "Raises quiet recordings so speech is easier to hear for the model."),
    "advanced.target_dbfs": ("Target level (dBFS)", "float", (-40.0, -10.0, 1.0), ""),
    "advanced.max_gain_db": ("Largest boost (dB)", "float", (0.0, 50.0, 1.0), ""),
    "advanced.stereo": ("Stereo to mono", "choice", [("average", "Average both channels"), ("left", "Left"), ("right", "Right")], "Pick one side when each speaker has a microphone channel."),
    "advanced.noise_reduction": ("Noise reduction", "choice", [("off", "Off"), ("light", "Light"), ("strong", "Strong")],
                                 "For noisy places (cafes, conference floors). Off for clean recordings: filtering "
                                 "clean speech can lose quiet words."),
    "advanced.noise_in_player": ("Hear noise reduction in the player", "bool", None,
                                 "Play the cleaned audio, to judge whether the filter helps."),
    "advanced.filter_known": ("Drop known filler phrases", "bool", None, "Removes phrases models invent in silence."),
    "advanced.filter_list": ("Phrases", "list", None, "One per line."),
    "advanced.repeat_limit": ("Drop a phrase repeated more than N times in a row", "int", (2, 10), ""),
    "advanced.compute_type": ("Compute type", "choice", [("int8", "int8"), ("int8_float32", "int8 + float32"), ("float32", "float32")], "Whisper only. int8 is fastest; float32 uses twice the memory. Reloads the model."),
    "advanced.keep_loaded": ("Keep the model loaded between files", "bool", None, ""),
    "advanced.priority": ("Process priority during a job", "choice", [("normal", "Normal"), ("below", "Below normal")], ""),
    "advanced.offline": ("Offline mode", "bool", None, "Blocks every network call (same as the Options menu tick)."),
    "advanced.proxy": ("Proxy", "choice", [("system", "System"), ("none", "None")], "Used only for model downloads."),
}
WHISPER_ONLY = {"Decoding"}


class AdvancedTab(QWidget):
    def __init__(self, dlg):
        super().__init__()
        self.dlg = dlg
        v = QVBoxLayout(self)
        self.inputs = {}
        self.labels = {}
        grid = QGridLayout()
        for i, (group, keys) in enumerate(GROUPS.items()):
            title = group + (" (Whisper only)" if group in WHISPER_ONLY else "")
            g = QGroupBox(title)
            fl = QFormLayout(g)
            for k in keys:
                label, kind, rng, tip = META[k]
                w = self._make(kind, rng)
                w.setToolTip(tip)
                lab = QLabel(label + ":")
                lab.setToolTip(tip)
                fl.addRow(lab, w)
                self.inputs[k] = (kind, w)
                self.labels[k] = lab
            fl.addRow("", button("Restore this group", lambda _=False, ks=keys: self.restore(ks)))
            grid.addWidget(g, i // 2, i % 2)
            if group == "Decoding":
                self.decoding_box = g
        v.addLayout(grid)
        # storage / tools / file types / first run
        g = QGroupBox("Storage")
        h = QHBoxLayout(g)
        self.storage = QLabel()
        h.addWidget(self.storage, 1)
        h.addWidget(button("Open data folder", lambda: system.open_folder(self.dlg.settings.data_folder())))
        h.addWidget(button("Clear temp now", self.clear_temp))
        v.addWidget(g)
        g = QGroupBox("Tools")
        h = QHBoxLayout(g)
        h.addWidget(button("Environment check…", self.env_check))
        h.addWidget(button("Privacy check…", self.privacy_check))
        h.addWidget(button("Copy diagnostic summary", self.copy_diag))
        h.addWidget(button("Open logs folder", lambda: system.open_folder(os.path.join(self.dlg.settings.data_folder(), "logs"))))
        h.addStretch(1)
        v.addWidget(g)
        g = QGroupBox("File types and first-run guide")
        h = QHBoxLayout(g)
        self.ft_label = QLabel()
        h.addWidget(self.ft_label)
        self.b_reg = button("Register", self.register, "Adds this program to 'Open with' for WAV, MP3, MP4, MKV and M4A (current user only).")
        self.b_unreg = button("Unregister", self.unregister)
        h.addWidget(self.b_reg)
        h.addWidget(self.b_unreg)
        h.addStretch(1)
        h.addWidget(button("Show first-run guide again", self.first_run))
        v.addWidget(g)
        v.addStretch(1)
        for k, (kind, w) in self.inputs.items():
            sig = {"bool": "toggled", "int": "valueChanged", "float": "valueChanged", "choice": "currentIndexChanged",
                   "list": "textChanged"}[kind]
            getattr(w, sig).connect(lambda *_, key=k: self.bold(key))
        self.load(dlg.work)

    def _make(self, kind, rng):
        if kind == "bool":
            return QCheckBox()
        if kind == "int":
            w = QSpinBox()
            w.setRange(*rng)
            if rng[0] == 0 and rng[1] == 10:
                w.setSpecialValueText("Follow Speed")
            return w
        if kind == "float":
            w = QDoubleSpinBox()
            w.setRange(rng[0], rng[1])
            w.setSingleStep(rng[2])
            w.setDecimals(2)
            return w
        if kind == "choice":
            return combo(rng)
        w = QPlainTextEdit()
        w.setMaximumHeight(80)
        return w

    def value(self, k):
        kind, w = self.inputs[k]
        if kind == "bool":
            return w.isChecked()
        if kind == "int":
            return w.value()
        if kind == "float":
            return round(w.value(), 3)
        if kind == "choice":
            return w.currentData()
        return [l.strip() for l in w.toPlainText().splitlines() if l.strip()]

    def put(self, k, val):
        kind, w = self.inputs[k]
        w.blockSignals(True)
        if kind == "bool":
            w.setChecked(bool(val))
        elif kind in ("int", "float"):
            w.setValue(val)
        elif kind == "choice":
            w.setCurrentIndex(max(0, w.findData(val)))
        else:
            w.setPlainText("\n".join(val))
        w.blockSignals(False)
        self.bold(k)

    def load(self, s):
        for k in self.inputs:
            self.put(k, s.get(k))
        self.refresh_info()

    def collect(self, s):
        for k in self.inputs:
            s.set(k, self.value(k))

    def bold(self, k):
        f = self.labels[k].font()
        f.setBold(self.value(k) != DEFAULTS[k])  # FR-ADV-01
        self.labels[k].setFont(f)

    def refresh_bold(self):
        for k in self.inputs:
            self.bold(k)

    def restore(self, keys):
        for k in keys:
            self.put(k, copy.deepcopy(DEFAULTS[k]))

    def refresh_info(self):
        s = self.dlg.settings
        dl = s.download_folder()
        tmp = system.temp_folder(s)
        self.storage.setText(f"Model folder {human_size(system.folder_size(dl)) if os.path.isdir(dl) else '0 MB'} · "
                             f"temp folder {human_size(system.folder_size(tmp)) if os.path.isdir(tmp) else '0 MB'}")
        reg = system.file_types_registered()
        self.ft_label.setText(("'Open with': registered" if reg else "'Open with': not registered") if system.IS_WIN
                              else "'Open with' registration: Windows only")
        self.b_reg.setEnabled(system.IS_WIN and not reg)
        self.b_unreg.setEnabled(system.IS_WIN and reg)
        model = self.dlg.work.get("transcription.model")
        m = self.dlg.manager.find(model) if model and model != "auto" else None
        whisper = m is None or m.kind == "whisper"
        self.decoding_box.setEnabled(whisper)  # FR-ADV-02
        self.decoding_box.setToolTip("" if whisper else "Whisper only: the chosen model is " + m.name)

    def clear_temp(self):
        tmp = system.temp_folder(self.dlg.settings)
        shutil.rmtree(tmp, ignore_errors=True)
        self.refresh_info()

    def env_check(self):
        from .dialogs import ReportDialog
        ReportDialog(self, "Environment check", system.environment_report(self.dlg.settings, self.dlg.manager)).exec()

    def privacy_check(self):
        from .dialogs import ReportDialog
        self.dlg.collect()
        ReportDialog(self, "Privacy check", system.privacy_report(self.dlg.work)).exec()

    def copy_diag(self):
        text = system.environment_report(self.dlg.settings, self.dlg.manager) + "\n\n" + \
            system.privacy_report(self.dlg.settings)
        QApplication.clipboard().setText(text)
        QMessageBox.information(self, "Diagnostic summary", "Copied to the clipboard. It holds no transcript text.")

    def register(self):
        try:
            system.register_file_types()
        except Exception as ex:
            QMessageBox.warning(self, "File types", str(ex))
        self.refresh_info()

    def unregister(self):
        try:
            system.unregister_file_types()
        except Exception as ex:
            QMessageBox.warning(self, "File types", str(ex))
        self.refresh_info()

    def first_run(self):
        self.dlg.show_first_run.emit()
