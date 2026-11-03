"""Names from your documents: review dialog (version 2.1.2)."""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from .. import names

HOTWORD_LIMIT = 100
NEW_SET = "\u0000new"


class NamesDialog(QDialog):
    """Shows the names found in documents; the ticked ones go into a vocabulary set as hotwords."""

    def __init__(self, parent, sets, active, paths=(), pasted=None):
        super().__init__(parent)
        self.setWindowTitle("Names from documents")
        self.resize(760, 620)
        self.sets = sets
        self.sources = []          # labels shown at the top
        self.texts = []            # (text, rows) of every source
        self.found = {}            # lower-case name -> candidate
        lay = QVBoxLayout(self)
        intro = QLabel("Names of people, organisations and special terms found in your documents. Ticked names go "
                       "into a vocabulary set as <b>hotwords</b>, so Whisper spells them right. Untick anything that "
                       "is not a name; double-click a name to correct its spelling.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        self.src_label = QLabel("No document yet.")
        self.src_label.setWordWrap(True)
        row.addWidget(self.src_label, 1)
        for text, slot in (("Add files…", self.add_files), ("Paste text…", self.paste_text)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Name (hotword)", "Kind", "Found", "Why"])
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.itemChanged.connect(lambda *_: self.update_count())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot in (("Tick all", lambda: self.tick(lambda c: True)),
                           ("Tick none", lambda: self.tick(lambda c: False)),
                           ("Tick suggested", lambda: self.tick(lambda c: c["checked"]))):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Add to vocabulary set:"))
        self.set_combo = QComboBox()
        for n in sets:
            self.set_combo.addItem(n, n)
        self.set_combo.addItem("New set…", NEW_SET)
        self.set_combo.setCurrentIndex(max(0, self.set_combo.findData(active)))
        self.set_combo.currentIndexChanged.connect(self._set_changed)
        row.addWidget(self.set_combo)
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("Name of the new set, e.g. Annual conference")
        row.addWidget(self.new_name, 1)
        lay.addLayout(row)
        self.first = QCheckBox("Put these names first (words after about 100 in a set are not used)")
        self.first.setChecked(True)
        lay.addWidget(self.first)
        self.make_active = QCheckBox("Use this set for the next transcriptions")
        self.make_active.setChecked(True)
        lay.addWidget(self.make_active)
        self.count = QLabel()
        lay.addWidget(self.count)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok_btn = bb.button(QDialogButtonBox.Ok)
        self.ok_btn.setText("Add to vocabulary")
        bb.accepted.connect(self.accept_names)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._set_changed()
        for p in paths:
            self.load_file(p)
        if pasted:
            self.load_text(pasted, "pasted text")
        self.refresh()

    # ------------------------------------------------------------ sources
    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Documents with names", "", names.FILE_FILTER)
        for f in files:
            self.load_file(f)
        self.refresh()

    def paste_text(self):
        text, ok = QInputDialog.getMultiLineText(self, "Paste text", "Paste an e-mail, agenda or attendee list:")
        if ok and text.strip():
            self.load_text(text, "pasted text")
            self.refresh()

    def load_file(self, path):
        try:
            text, rows = names.read_document(path)
        except names.DocumentError as ex:
            QMessageBox.warning(self, "Names from documents", str(ex))
            return
        self.texts.append((text, rows))
        self.sources.append(os.path.basename(path))
        if not self.new_name.text():
            self.new_name.setText(os.path.splitext(os.path.basename(path))[0][:40])

    def load_text(self, text, label):
        self.texts.append((text, []))
        self.sources.append(label)

    def refresh(self):
        merged = {}
        for text, rows in self.texts:
            for c in names.extract(text, rows):
                k = c["text"].lower()
                if k in merged:
                    m = merged[k]
                    m["count"] += c["count"]
                    m["checked"] = m["checked"] or c["checked"]
                    rank = {"Person": 0, "Organisation": 1, "Name": 2, "Term": 3}
                    if rank[c["kind"]] < rank[m["kind"]]:  # an attendee list or e-mail says more than capitals do
                        m["kind"], m["why"] = c["kind"], c["why"]
                else:
                    merged[k] = dict(c)
        # keep what the user already ticked or corrected
        old = {self.table.item(r, 0).data(Qt.UserRole): (self.table.item(r, 0).checkState(), self.table.item(r, 0).text())
               for r in range(self.table.rowCount())}
        self.found = merged
        kinds = {"Person": 0, "Name": 1, "Organisation": 2, "Term": 3}
        items = sorted(merged.values(), key=lambda c: (not c["checked"], kinds[c["kind"]], -c["count"], c["text"].lower()))
        self.table.blockSignals(True)
        self.table.setRowCount(len(items))
        for r, c in enumerate(items):
            it = QTableWidgetItem(c["text"])
            it.setData(Qt.UserRole, c["text"].lower())
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable)
            state, text = old.get(c["text"].lower(), (Qt.Checked if c["checked"] else Qt.Unchecked, c["text"]))
            it.setCheckState(state)
            it.setText(text)
            self.table.setItem(r, 0, it)
            for col, v in ((1, c["kind"]), (2, f"×{c['count']}"), (3, c["why"])):
                x = QTableWidgetItem(v)
                x.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                self.table.setItem(r, col, x)
        self.table.blockSignals(False)
        self.src_label.setText("Found in: " + ", ".join(self.sources) if self.sources else
                               "No document yet: use Add files… (Word, Excel, PDF, text, saved e-mail) or "
                               "Paste text…")
        self.update_count()

    # ------------------------------------------------------------ choosing
    def tick(self, rule):
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            c = self.found.get(self.table.item(r, 0).data(Qt.UserRole), {})
            self.table.item(r, 0).setCheckState(Qt.Checked if rule(c) else Qt.Unchecked)
        self.table.blockSignals(False)
        self.update_count()

    def selected(self):
        return [self.table.item(r, 0).text().strip() for r in range(self.table.rowCount())
                if self.table.item(r, 0).checkState() == Qt.Checked and self.table.item(r, 0).text().strip()]

    def _set_changed(self, *_):
        self.new_name.setVisible(self.set_combo.currentData() == NEW_SET)
        self.update_count()

    def target_existing(self):
        key = self.set_combo.currentData()
        return [] if key == NEW_SET else list(self.sets.get(key, {}).get("hotwords", []))

    def update_count(self):
        sel = self.selected()
        merged = names.merge_hotwords(self.target_existing(), sel, self.first.isChecked() if hasattr(self, "first") else True)
        total = names.word_count(merged)
        warn = total > HOTWORD_LIMIT
        msg = (f"{len(sel)} names ticked ({names.word_count(sel)} words). The set will have {total} of about "
               f"{HOTWORD_LIMIT} hotword words")
        if warn:
            msg += " - words after about 100 are not used, so keep the most important names first"
        self.count.setText(f"<span style='color:{'#c07000' if warn else 'palette(text)'}'>{msg}.</span>")
        self.ok_btn.setEnabled(bool(sel))

    def accept_names(self):
        key = self.set_combo.currentData()
        if key == NEW_SET:
            name = self.new_name.text().strip()
            if not name:
                QMessageBox.information(self, "Names from documents", "Type a name for the new set.")
                return
            if name in self.sets:
                QMessageBox.information(self, "Names from documents", f"A set called '{name}' already exists.")
                return
            key = name
        self.result_set = key
        self.result_new = self.set_combo.currentData() == NEW_SET
        self.result_names = self.selected()
        self.accept()

    def apply_to(self, sets):
        """Returns updated vocabulary sets with the chosen names added."""
        sets = {k: dict(v) for k, v in sets.items()}
        vs = sets.get(self.result_set) or {"scope": "all", "hotwords": [], "replacements": []}
        vs["hotwords"] = names.merge_hotwords(vs.get("hotwords", []), self.result_names, self.first.isChecked())
        sets[self.result_set] = vs
        return sets
