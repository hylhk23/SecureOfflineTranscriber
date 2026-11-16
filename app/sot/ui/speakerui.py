"""Speaker identification windows (version 2.4.0): the Identify Speakers dialog, the Speakers panel beside the
transcript, the consent dialog for saving a voice, and the Voice Library dialog."""
import os
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout,
                               QWidget, QStyle)

from .. import media, speakers, voicelib

DEFAULT_NAME = re.compile(r"^Speaker \d+$")
CHANNEL_TAG_KEYS = ("vrp_channels", "ichn")  # Voice Recorder Pro 1.0.3: MP3/FLAC tag, WAV LIST INFO chunk


def channel_tag(info):
    """The recorder's note that a file keeps the microphone left and the computer audio right, if present."""
    for k, v in (info or {}).get("tags", {}).items():
        if k.lower() in CHANNEL_TAG_KEYS and "L=" in v and "R=" in v:
            return v
    return ""


def is_stereo(info, track=0):
    a = (info or {}).get("audio") or []
    ch = (a[track]["channels"] if track < len(a) else "").lower()
    return "stereo" in ch or ch.startswith("2")


def split_default(settings, info, track=0):
    """Whether Identify Speakers starts with 'left = me, right = others' for this file."""
    if not is_stereo(info, track):
        return False
    mode = settings.get("speakers.channels")
    if mode == "split":
        return True
    if mode == "mixed":
        return False
    return bool(channel_tag(info))


# ====================================================================== Identify Speakers dialog
class IdentifyDialog(QDialog):
    def __init__(self, parent, settings, info, track, people, seg_model, voice_model):
        super().__init__(parent)
        self.setWindowTitle("Identify Speakers")
        self.settings = settings
        f = QFormLayout(self)
        f.addRow("Recording:", QLabel(f"{os.path.basename(info['path'])} · {media.fmt_clock(info['duration'])}"))
        self.count = QComboBox()
        self.count.addItem("Automatic", 0)
        for n in range(2, 11):
            self.count.addItem(str(n), n)
        self.count.setCurrentIndex(max(0, self.count.findData(int(settings.get("speakers.count")))))
        self.count.setToolTip("How many people speak, everyone included. Telling the program improves the result.")
        f.addRow("Number of speakers:", self.count)
        self.stereo = is_stereo(info, track)
        self.channels = QComboBox()
        self.channels.addItem("Mixed: voices on one track", "mixed")
        self.channels.addItem("Separate: left = me (microphone), right = others (computer audio)", "split")
        self.channels.setCurrentIndex(1 if split_default(settings, info, track) else 0)
        self.channels.setEnabled(self.stereo)
        tag = channel_tag(info)
        note = ("Found in the file's tags: " + tag) if tag else (
            "This file has two channels but no tag saying which is which." if self.stereo else
            "One channel: the voices are told apart by sound alone.")
        f.addRow("Channels:", self.channels)
        self.note = QLabel(note)
        self.note.setWordWrap(True)
        f.addRow("", self.note)
        self.left_name = QLineEdit(settings.get("speakers.left_name") or "Me")
        self.left_name.setMaxLength(voicelib.MAX_NAME)
        f.addRow("Name for the left channel:", self.left_name)
        self.use_lib = QCheckBox(f"Match voices in my voice library ({len(people)} "
                                 f"{'person' if len(people) == 1 else 'people'} saved with this voice model)")
        self.use_lib.setChecked(bool(settings.get("speakers.use_library")) and bool(people))
        self.use_lib.setEnabled(bool(people))
        f.addRow("", self.use_lib)
        f.addRow("Models:", QLabel(f"{seg_model} · {voice_model}"))
        self.channels.currentIndexChanged.connect(self._sync)
        self._sync()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Identify speakers")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _sync(self, *_):
        self.left_name.setEnabled(self.channels.currentData() == "split" and self.stereo)

    def values(self):
        return {"count": self.count.currentData(), "split": self.channels.currentData() == "split" and self.stereo,
                "left_name": " ".join(self.left_name.text().split()) or "Me", "use_library": self.use_lib.isChecked()}


# ====================================================================== Speakers panel
class SpeakersPanel(QWidget):
    """One row per speaker: marker, name (type or pick from the library), match and talk time, play, remember."""
    rename = Signal(str, str)          # speaker id, new name
    play = Signal(str)
    merge_requested = Signal()
    save_requested = Signal()
    identify_requested = Signal()
    COLS = ["", "Name", "Talk", "", "Remember"]

    def __init__(self, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 0, 0, 0)
        self.title = QLabel("<b>Speakers</b>")
        v.addWidget(self.title)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        hh = self.table.horizontalHeader()
        for c in range(len(self.COLS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        v.addWidget(self.hint)
        row = QHBoxLayout()
        for text, sig, tip in (("Merge…", self.merge_requested, "Join two speakers that are the same person."),
                               ("Save voices…", self.save_requested, "Save the voices ticked under Remember to the "
                                "voice library, with each person's consent."),
                               ("Identify again", self.identify_requested, "Run speaker identification again "
                                "(Transcribe > Identify Speakers).")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(sig.emit)
            row.addWidget(b)
        v.addLayout(row)
        self.remember = {}
        self.combos = {}
        self.setMinimumWidth(300)

    def set_rows(self, rows, library_names, min_save_s, dark):
        """rows: [{"id", "name", "shape", "light", "dark", "detail", "seconds", "share", "turns", "can_save",
        "why_not", "remember"}]"""
        for r in range(self.table.rowCount()):  # old cell widgets leave at once: a replaced widget is only deleted
            for c in range(self.table.columnCount()):  # when the event loop runs, and is drawn until then
                w = self.table.cellWidget(r, c)
                if w is not None:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
        self.table.clearContents()
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        self.remember, self.combos = {}, {}
        for r, row in enumerate(rows):
            mk = QLabel(row["shape"])
            mk.setAlignment(Qt.AlignCenter)
            mk.setStyleSheet(f"color: {row['dark'] if dark else row['light']}; font-size: 14pt;")
            mk.setToolTip(f"Marker of {row['name']} in the transcript")
            self.table.setCellWidget(r, 0, mk)
            cb = QComboBox()
            cb.setEditable(True)
            cb.setInsertPolicy(QComboBox.NoInsert)
            names = [row["name"]] + [n for n in library_names if n != row["name"]]
            cb.addItems(names)
            cb.setCurrentText(row["name"])
            cb.lineEdit().setMaxLength(voicelib.MAX_NAME)
            cb.setToolTip("Type the person's name, or pick someone from your voice library. "
                          + (f"Alt+{r + 1} gives the paragraph at the cursor this speaker." if r < 9 else ""))
            sid = row["id"]
            cb.activated.connect(lambda _i, s=sid, c=cb: self.rename.emit(s, c.currentText()))
            cb.lineEdit().editingFinished.connect(lambda s=sid, c=cb: self.rename.emit(s, c.currentText()))
            self.combos[sid] = cb
            w = QWidget()
            h = QVBoxLayout(w)
            h.setContentsMargins(2, 2, 2, 2)
            h.setSpacing(0)
            h.addWidget(cb)
            det = QLabel(row["detail"])
            det.setStyleSheet("color: palette(text);")
            h.addWidget(det)
            self.table.setCellWidget(r, 1, w)
            talk = QTableWidgetItem(f"{row['share']:.0%}\n{row['turns']} turn{'' if row['turns'] == 1 else 's'}")
            talk.setToolTip(f"{media.fmt_clock(row['seconds'])} of speech in {row['turns']} turns")
            self.table.setItem(r, 2, talk)
            pb = QToolButton()
            pb.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
            pb.setToolTip(f"Play a few seconds of {row['name']}")
            pb.setAccessibleName(f"Play {row['name']}")
            pb.clicked.connect(lambda _=False, s=sid: self.play.emit(s))
            self.table.setCellWidget(r, 3, pb)
            ch = QCheckBox()
            ch.setChecked(bool(row.get("remember")) and row["can_save"])
            ch.setEnabled(row["can_save"])
            ch.setToolTip("Tick, then press Save voices… to keep this voice in your voice library."
                          if row["can_save"] else row["why_not"])
            ch.setAccessibleName(f"Remember the voice of {row['name']}")
            self.remember[sid] = ch
            self.table.setCellWidget(r, 4, ch)
        self.table.resizeRowsToContents()
        self.title.setText(f"<b>Speakers</b> ({len(rows)})")
        self.hint.setText("Type a name to rename a speaker everywhere. Click a name in the transcript to change one "
                          "paragraph. A paragraph marked (?) may belong to someone else.")

    def ticked(self):
        return [sid for sid, ch in self.remember.items() if ch.isChecked() and ch.isEnabled()]


def detail_text(sp):
    """'Voice library: high match' / 'Not in the voice library' / 'Left channel (microphone)'"""
    if sp.get("id") == speakers.ME:
        return "Left channel (microphone)"
    if sp.get("level"):
        return f"Voice library: {sp['level']} match ({sp.get('score', 0):.2f})"
    if sp.get("person"):
        return "In the voice library"
    return "Not in the voice library" if sp.get("searched") else "Voice library not checked"


# ====================================================================== consent
class ConsentDialog(QDialog):
    """Asked before a voice is saved: the person must have agreed."""

    def __init__(self, parent, name, existing=None):
        super().__init__(parent)
        self.setWindowTitle("Save voice")
        v = QVBoxLayout(self)
        if existing:
            text = (f"Add this recording's voice to <b>{name}</b> in your voice library?<br><br>"
                    f"Consent recorded: {existing.get('consent') or 'no date'}. More recordings of the same person "
                    "make matching more reliable.")
        else:
            text = (f"Save the voiceprint of <b>{name}</b> to your voice library?<br><br>"
                    "A voiceprint is <b>biometric personal data</b>: 192 numbers that describe a voice, made from "
                    "this recording. It is stored only on this computer, in the data folder (voices.json), never "
                    "sent anywhere, and is used only to suggest names in later recordings. No audio is kept. You can "
                    "delete it at any time in Options > Voice Library.<br><br>Save a voice only with the person's "
                    "agreement.")
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        v.addWidget(lab)
        self.agree = QCheckBox(f"{name} agreed to have their voiceprint saved" if not existing else
                               f"{name} still agrees to have their voiceprint saved")
        v.addWidget(self.agree)
        self.note = QLineEdit(existing.get("note", "") if existing else "")
        self.note.setPlaceholderText("Note, for example organisation or role (optional)")
        self.note.setMaxLength(voicelib.MAX_NOTE)
        v.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.b_save = bb.button(QDialogButtonBox.Save)
        self.b_save.setEnabled(False)
        self.agree.toggled.connect(self.b_save.setEnabled)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self.setMinimumWidth(460)


# ====================================================================== Voice Library
class VoiceLibraryDialog(QDialog):
    COLS = ["Name", "Note", "Recordings", "Speech", "Consent", "Last matched", "Voice model"]

    def __init__(self, parent, path, model_names):
        super().__init__(parent)
        self.setWindowTitle("Voice Library")
        self.path = path
        self.model_names = model_names
        self.lib, problems = voicelib.load(path)
        self.resize(820, 460)
        v = QVBoxLayout(self)
        intro = QLabel("Voiceprints of people you named and saved with their consent. They are kept only on this "
                       f"computer, in <code>{path}</code>, and used only to suggest names when speakers are "
                       "identified. No audio is kept.")
        intro.setWordWrap(True)
        intro.setTextInteractionFlags(Qt.TextSelectableByMouse)
        v.addWidget(intro)
        self.problems = QLabel("\n".join(problems[:6]))
        self.problems.setWordWrap(True)
        self.problems.setVisible(bool(problems))
        v.addWidget(self.problems)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.table.horizontalHeader()
        for c in range(len(self.COLS)):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        v.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot, tip in (
                ("Rename…", self.rename, "Change the name of the selected person."),
                ("Note…", self.edit_note, "Change the note (organisation, role)."),
                ("Merge…", self.merge, "Join two entries of the same person."),
                ("Delete", self.delete, "Delete the selected people's voiceprints. This cannot be undone."),
                ("Delete all", self.delete_all, "Delete every voiceprint in the library. This cannot be undone."),
                ("Export…", self.export, "Save the selected people to a file, to move them to another computer."),
                ("Import…", self.import_, "Add the people of a voice library file.")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        v.addLayout(row)
        self.changed = False
        self.fill()

    def fill(self):
        ppl = sorted(self.lib["people"], key=lambda p: p["name"].casefold())
        self.table.setRowCount(len(ppl))
        for r, p in enumerate(ppl):
            vals = [p["name"], p.get("note", ""), str(len(p["samples"])), media.fmt_clock(voicelib.speech_seconds(p)),
                    p.get("consent") or "not recorded", p.get("last_matched") or "never",
                    self.model_names.get(p["model"], p["model"])]
            for c, t in enumerate(vals):
                it = QTableWidgetItem(t)
                it.setData(Qt.UserRole, p["id"])
                self.table.setItem(r, c, it)

    def selected(self):
        return list(dict.fromkeys(self.table.item(i.row(), 0).data(Qt.UserRole)
                                  for i in self.table.selectedIndexes()))

    def _save(self):
        try:
            voicelib.save(self.lib, self.path)
            self.changed = True
        except OSError as ex:
            QMessageBox.warning(self, "Voice Library", f"The voice library could not be saved: {ex}")
        self.fill()

    def rename(self):
        sel = self.selected()
        if len(sel) != 1:
            return
        p = voicelib.find(self.lib, sel[0])
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=p["name"])
        if ok and name.strip():
            voicelib.rename(self.lib, p["id"], name)
            self._save()

    def edit_note(self):
        sel = self.selected()
        if len(sel) != 1:
            return
        p = voicelib.find(self.lib, sel[0])
        note, ok = QInputDialog.getText(self, "Note", "Note:", text=p.get("note", ""))
        if ok:
            voicelib.set_note(self.lib, p["id"], note)
            self._save()

    def merge(self):
        sel = self.selected()
        if len(sel) != 2:
            QMessageBox.information(self, "Merge", "Select the two entries of the same person.")
            return
        a, b = voicelib.find(self.lib, sel[0]), voicelib.find(self.lib, sel[1])
        if QMessageBox.question(self, "Merge", f"Join {b['name']} into {a['name']}?") != QMessageBox.Yes:
            return
        try:
            voicelib.merge(self.lib, a["id"], b["id"])
        except ValueError as ex:
            QMessageBox.warning(self, "Merge", str(ex))
            return
        self._save()

    def delete(self):
        sel = self.selected()
        if not sel:
            return
        names = ", ".join(voicelib.find(self.lib, s)["name"] for s in sel)
        if QMessageBox.question(self, "Delete", f"Delete the voiceprints of {names}?\n\nThis cannot be undone.") \
                != QMessageBox.Yes:
            return
        for s in sel:
            voicelib.delete(self.lib, s)
        self._save()

    def delete_all(self):
        if not self.lib["people"]:
            return
        if QMessageBox.question(self, "Delete all", f"Delete all {len(self.lib['people'])} voiceprints in the voice "
                                "library?\n\nThis cannot be undone.") != QMessageBox.Yes:
            return
        voicelib.delete_all(self.lib)
        self._save()

    def export(self):
        sel = self.selected() or [p["id"] for p in self.lib["people"]]
        if not sel:
            return
        if QMessageBox.question(self, "Export", f"Export the voiceprints of {len(sel)} "
                                f"{'person' if len(sel) == 1 else 'people'} to a file?\n\nThe file holds biometric "
                                "data: keep it safe and give it only to someone the people agreed to.") \
                != QMessageBox.Yes:
            return
        p, _ = QFileDialog.getSaveFileName(self, "Export voices", "voices-export.json", "Voice library (*.json)")
        if p:
            try:
                n = voicelib.export_people(self.lib, set(sel), p)
            except OSError as ex:
                QMessageBox.warning(self, "Export", str(ex))
                return
            QMessageBox.information(self, "Export", f"Exported {n} {'person' if n == 1 else 'people'}.")

    def import_(self):
        p, _ = QFileDialog.getOpenFileName(self, "Import voices", "", "Voice library (*.json)")
        if not p:
            return
        added, updated, problems = voicelib.import_people(self.lib, p)
        if added or updated:
            self._save()
        QMessageBox.information(self, "Import", f"Added {added}, updated {updated}."
                                + ("\n\n" + "\n".join(problems[:6]) if problems else ""))
