"""Small dialogs: About, Keyboard Shortcuts, Find/Replace, Go to Time, Export, File Properties,
report windows and the first-run guide."""
import os
import re
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QHeaderView)

from .. import APP_NAME, COPYRIGHT_YEAR, PUBLISHER, VERSION, WEBSITE, media, paths
from ..exporters import FORMATS
from ..system import build_date

SHORTCUTS = [
    ("File", "Open Media", "Ctrl+O"), ("File", "Open Output Folder", "Ctrl+Shift+O"),
    ("File", "Save Transcript", "Ctrl+S"), ("File", "Save Transcript As", "Ctrl+Shift+S"),
    ("File", "Export", "Ctrl+E"), ("File", "Copy Transcript", "Ctrl+Shift+C"),
    ("File", "File Properties", "Alt+Enter"), ("File", "Close", "Ctrl+W"), ("File", "Exit", "Ctrl+Q"),
    ("Edit", "Undo / Redo", "Ctrl+Z / Ctrl+Y"), ("Edit", "Cut / Copy / Paste", "Ctrl+X / Ctrl+C / Ctrl+V"),
    ("Edit", "Select All", "Ctrl+A"), ("Edit", "Find / Find Next", "Ctrl+F / F3"), ("Edit", "Replace", "Ctrl+H"),
    ("Edit", "Go to Time", "Ctrl+G"),
    ("Transcribe", "Transcribe / Resume", "F5"), ("Transcribe", "Stop", "Esc"),
    ("Transcribe", "Play / Pause", "F9 (Space outside the text)"),
    ("Transcribe", "Replay Sentence", "F6"), ("Transcribe", "Previous / Next Unsure Line", "F7 / F8"),
    ("Transcribe", "Skip Back / Forward 5 s", "Ctrl+Left / Ctrl+Right"),
    ("Transcript", "Play the sentence under the mouse", "Ctrl+click"),
    ("Options", "All Settings", "Ctrl+,"), ("Help", "Help Contents", "F1"), ("Help", "Keyboard Shortcuts", "Ctrl+/"),
]


def lib_versions():
    from ..system import lib_versions_list
    fv, flic = media.version_info()
    return ", ".join(lib_versions_list()[:7] + [lib_versions_list()[-1]]), flic or "LGPL-2.1"


class AboutDialog(QDialog):
    def __init__(self, parent, icon_path):
        super().__init__(parent)
        self.setWindowTitle(f"About {APP_NAME}")
        self.setMinimumWidth(480)
        self.setMaximumWidth(560)
        libs, ffmpeg_lic = lib_versions()
        lay = QHBoxLayout(self)
        ic = QLabel()
        if icon_path and os.path.exists(icon_path):
            ic.setPixmap(QIcon(icon_path).pixmap(64, 64))
        ic.setAlignment(Qt.AlignTop)
        lay.addWidget(ic)
        col = QVBoxLayout()
        lay.addLayout(col, 1)
        title = QLabel(f"<b style='font-size:15pt'>{APP_NAME} {VERSION}</b>")
        col.addWidget(title)
        don = QLabel(f"Support this program with a donation: <a href='{WEBSITE}'>{WEBSITE}</a>")
        don.setOpenExternalLinks(True)  # the only link that leaves the PC, and only on click (FR-HELP-02)
        col.addWidget(don)
        body = QLabel(
            "Offline speech-to-text for Windows: time-stamped transcripts from recorded audio and video files, "
            f"processed entirely on this PC.<br><br>Build {build_date()}.<br>{libs}<br><br>"
            "Speech recognition by OpenAI Whisper (MIT) through faster-whisper and CTranslate2 (MIT); "
            "SenseVoiceSmall by FunAudioLLM (FunASR model licence), Parakeet-TDT by NVIDIA (CC-BY-4.0) and "
            "Moonshine by Useful Sensors (MIT) through sherpa-onnx (Apache-2.0) and ONNX Runtime (MIT); voice "
            f"detection by Silero VAD (MIT). Uses Qt for Python (LGPL-3.0), FFmpeg ({ffmpeg_lic}; unmodified "
            "ffmpeg.exe), sounddevice (MIT) with PortAudio (MIT), NumPy (BSD-3), OpenCC (Apache-2.0) and "
            "python-docx (MIT). Licence texts are in THIRD-PARTY-LICENSES.txt. Not affiliated with or endorsed "
            "by OpenAI, Alibaba, NVIDIA or Useful Sensors.")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        col.addWidget(body)
        fine = QLabel(
            f"<i>Copyright © {COPYRIGHT_YEAR} {PUBLISHER}. All rights reserved. This program comes with "
            "ABSOLUTELY NO WARRANTY! It is provided “as is”, to the extent permitted by law. "
            f"{PUBLISHER} is not liable for any loss or damage arising from its use, including incomplete or "
            "inaccurate transcripts. Check transcripts against the recording before relying on them. You are "
            "responsible for using this program lawfully, including obtaining any consent required to record or "
            f"transcribe a conversation, and you agree to indemnify {PUBLISHER} against any claim arising from your "
            "use of it. Use it at your own risk.</i>")
        fine.setWordWrap(True)
        fine.setTextInteractionFlags(Qt.TextSelectableByMouse)
        fine.setStyleSheet("color: gray; font-size: 8.5pt")
        col.addWidget(fine)
        bb = QDialogButtonBox(QDialogButtonBox.Ok)
        bb.accepted.connect(self.accept)
        col.addWidget(bb)


class ShortcutsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts")
        self.resize(460, 560)
        lay = QVBoxLayout(self)
        t = QTableWidget(len(SHORTCUTS), 3)
        t.setHorizontalHeaderLabels(["Menu", "Action", "Keys"])
        for r, row in enumerate(SHORTCUTS):
            for c, v in enumerate(row):
                t.setItem(r, c, QTableWidgetItem(v))
        t.verticalHeader().hide()
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(t)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


class FindReplaceDialog(QDialog):
    def __init__(self, parent, editor, replace=False):
        super().__init__(parent)
        self.editor = editor
        self.setWindowTitle("Replace" if replace else "Find")
        g = QGridLayout(self)
        self.find_edit = QLineEdit()
        self.repl_edit = QLineEdit()
        self.case = QCheckBox("Match case")
        self.whole = QCheckBox("Whole word")
        g.addWidget(QLabel("Find:"), 0, 0)
        g.addWidget(self.find_edit, 0, 1)
        if replace:
            g.addWidget(QLabel("Replace with:"), 1, 0)
            g.addWidget(self.repl_edit, 1, 1)
        g.addWidget(self.case, 2, 0)
        g.addWidget(self.whole, 2, 1)
        row = QHBoxLayout()
        b_next = QPushButton("Find Next")
        b_next.setDefault(True)
        b_next.clicked.connect(self.find_next)
        row.addWidget(b_next)
        if replace:
            b_rep = QPushButton("Replace")
            b_rep.clicked.connect(self.replace_one)
            b_all = QPushButton("Replace All")
            b_all.clicked.connect(self.replace_all)
            row.addWidget(b_rep)
            row.addWidget(b_all)
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.close)
        row.addWidget(b_close)
        g.addLayout(row, 3, 0, 1, 2)
        sel = editor.textCursor().selectedText()
        if sel:
            self.find_edit.setText(sel)

    def flags(self):
        from PySide6.QtGui import QTextDocument
        f = QTextDocument.FindFlag(0)
        if self.case.isChecked():
            f |= QTextDocument.FindCaseSensitively
        if self.whole.isChecked():
            f |= QTextDocument.FindWholeWords
        return f

    def find_next(self):
        text = self.find_edit.text()
        if not text:
            return False
        if self.editor.find(text, self.flags()):
            return True
        from PySide6.QtGui import QTextCursor
        self.editor.moveCursor(QTextCursor.Start)
        return self.editor.find(text, self.flags())

    def replace_one(self):
        cur = self.editor.textCursor()
        if cur.hasSelection() and cur.selectedText().lower() == self.find_edit.text().lower():
            cur.insertText(self.repl_edit.text())
        self.find_next()

    def replace_all(self):
        from PySide6.QtGui import QTextCursor
        text = self.find_edit.text()
        if not text:
            return
        n = 0
        cur = self.editor.textCursor()
        cur.beginEditBlock()
        self.editor.moveCursor(QTextCursor.Start)
        while self.editor.find(text, self.flags()):
            self.editor.textCursor().insertText(self.repl_edit.text())
            n += 1
        cur.endEditBlock()
        QMessageBox.information(self, "Replace", f"Replaced {n} time(s).")


def parse_time(s):
    parts = [p for p in re.split(r"[:\s]", s.strip()) if p]
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    t = 0.0
    for v in vals:
        t = t * 60 + v
    return t


class ExportDialog(QDialog):
    """File > Export (FR-OUT-03): ticks, folder and pattern pre-filled; changes apply to this export only."""

    def __init__(self, parent, settings, folder):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Formats:"))
        grid = QGridLayout()
        self.checks = {}
        chosen = settings.get("output.formats")
        for i, (k, (label, ext)) in enumerate(FORMATS.items()):
            cb = QCheckBox(f"{label} ({os.path.splitext(ext)[1]})")
            cb.setChecked(k in chosen)
            self.checks[k] = cb
            grid.addWidget(cb, i // 2, i % 2)
        lay.addLayout(grid)
        row = QHBoxLayout()
        row.addWidget(QLabel("Folder:"))
        self.folder = QLineEdit(folder)
        row.addWidget(self.folder, 1)
        b = QPushButton("Browse…")
        b.clicked.connect(self.browse)
        row.addWidget(b)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("File name pattern:"))
        self.pattern = QLineEdit(settings.get("output.pattern"))
        row.addWidget(self.pattern, 1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Export")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def browse(self):
        d = QFileDialog.getExistingDirectory(self, "Export to", self.folder.text())
        if d:
            self.folder.setText(d)

    def result_values(self):
        return [k for k, cb in self.checks.items() if cb.isChecked()], self.folder.text(), self.pattern.text()


class ReportDialog(QDialog):
    def __init__(self, parent, title, text):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 460)
        lay = QVBoxLayout(self)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        f = box.font()
        f.setFamily("Consolas")
        box.setFont(f)
        lay.addWidget(box)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        copy = bb.addButton("Copy", QDialogButtonBox.ActionRole)
        from PySide6.QtWidgets import QApplication
        copy.clicked.connect(lambda: QApplication.clipboard().setText(text))
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


def file_properties_text(info):
    lines = [f"File: {info['path']}", f"Size: {info['size']:,} bytes", f"Container: {info['format']}",
             f"Duration: {media.fmt_clock(info['duration'], ms=True)}", f"Video: {'yes' if info['video'] else 'no'}",
             ""]
    for i, a in enumerate(info["audio"]):
        lines.append(f"Audio track {i + 1}: {a['codec']}, {a['rate']} Hz, {a['channels']}"
                     + (f", language {a['lang']}" if a["lang"] else ""))
    return "\n".join(lines)


class FirstRunDialog(QDialog):
    """FR-MOD-01: no model installed yet."""

    def __init__(self, parent, rec_size, rec_names="", extra_text=""):
        super().__init__(parent)
        self.setWindowTitle("Welcome")
        self.choice = None
        lay = QVBoxLayout(self)
        what = f"{rec_names}, {rec_size}" if rec_names else rec_size
        msg = QLabel(f"<b>No model installed yet.</b><br><br>Download the recommended set ({what}), "
                     "or add a folder where you already keep models.<br><br>"
                     "Models are downloaded once; after that everything runs offline on this PC.")
        msg.setWordWrap(True)
        lay.addWidget(msg)
        self.extra = QCheckBox(extra_text) if extra_text else None  # version 2.1.4: English first
        if self.extra:
            lay.addWidget(self.extra)
        row = QHBoxLayout()
        for label, key in (("Download recommended set", "download"), ("Add a model folder…", "folder"),
                           ("Later", None)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            row.addWidget(b)
        lay.addLayout(row)

    def _pick(self, k):
        self.choice = k
        self.accept()

    def wants_cantonese_mandarin(self):
        return bool(self.extra and self.extra.isChecked())
