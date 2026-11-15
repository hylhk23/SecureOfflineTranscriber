"""The AI summary tab (version 2.3.0): shows the summary written after the transcript is complete.

The summary is Markdown. Times in it ([hh:mm:ss]) are links that play the recording from there, so every point
can be checked against what was said. Edit switches to the Markdown text; Done shows it formatted again."""
import html
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget,
                               QTextBrowser, QVBoxLayout, QWidget)

TIME_RE = re.compile(r"\[(\d{1,2}):(\d{2}):(\d{2})\]")


def to_html(md_text):
    """Small Markdown subset (headings, bullets, bold, paragraphs) to HTML, with clickable [hh:mm:ss] times."""
    out = []
    in_list = False
    para = []

    def inline(t):
        t = html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<i>\1</i>", t)
        return TIME_RE.sub(lambda m: f'<a href="time:{int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))}">'
                                     f'[{m.group(1)}:{m.group(2)}:{m.group(3)}]</a>', t)

    def flush_para():
        if para:
            out.append("<p>" + inline(" ".join(para)) + "</p>")
            para.clear()

    for line in (md_text or "").splitlines():
        s = line.strip()
        m = re.match(r"^(#{1,6})\s*(.*)$", s)
        bullet = re.match(r"^([-*•]|\d+[.)])\s+(.*)$", s)
        if m:
            flush_para()
            if in_list:
                out.append("</ul>")
                in_list = False
            level = min(4, max(2, len(m.group(1)) + 1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
        elif bullet:
            flush_para()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>" + inline(bullet.group(2)) + "</li>")
        elif not s:
            flush_para()
            if in_list:
                out.append("</ul>")
                in_list = False
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            para.append(s)
    flush_para()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


class SummaryView(QWidget):
    time_clicked = Signal(float)
    run_requested = Signal()
    stop_requested = Signal()
    copy_requested = Signal()
    save_requested = Signal()
    options_requested = Signal()
    edited = Signal()

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 4, 0, 0)
        top = QHBoxLayout()
        self.state_label = QLabel("No summary yet.")
        self.state_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(12)
        self.progress.setMaximumWidth(200)
        self.progress.setVisible(False)
        self.b_run = QPushButton("Write summary")
        self.b_run.setToolTip("Write the AI summary of the transcript on screen (Transcribe > Write AI Summary, Ctrl+M).")
        self.b_run.clicked.connect(self.run_requested)
        self.b_stop = QPushButton("Stop")
        self.b_stop.setToolTip("Stop writing the summary (Transcribe > Stop AI Summary). The transcript is not affected.")
        self.b_stop.clicked.connect(self.stop_requested)
        self.b_edit = QPushButton("Edit")
        self.b_edit.setCheckable(True)
        self.b_edit.setToolTip("Correct the summary as text (Markdown). Done shows it formatted again.")
        self.b_edit.toggled.connect(self._toggle_edit)
        self.b_copy = QPushButton("Copy")
        self.b_copy.setToolTip("Copy the summary (formatted and as plain text).")
        self.b_copy.clicked.connect(self.copy_requested)
        self.b_save = QPushButton("Save As…")
        self.b_save.setToolTip("Save the summary as Markdown (.md), plain text (.txt) or Word (.docx).")
        self.b_save.clicked.connect(self.save_requested)
        self.b_opts = QPushButton("Options…")
        self.b_opts.setToolTip("Options > AI Summary: model, language, length, sections and more.")
        self.b_opts.clicked.connect(self.options_requested)
        top.addWidget(self.state_label, 1)
        top.addWidget(self.progress)
        for b in (self.b_run, self.b_stop, self.b_edit, self.b_copy, self.b_save, self.b_opts):
            top.addWidget(b)
        v.addLayout(top)
        self.stack = QStackedWidget()
        self.browser = QTextBrowser()
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.anchorClicked.connect(self._anchor)
        self.editor = QPlainTextEdit()
        self.editor.textChanged.connect(self._edited)
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.editor)
        v.addWidget(self.stack, 1)
        self.foot = QLabel(" ")
        self.foot.setWordWrap(True)
        self.foot.setStyleSheet("color: palette(placeholder-text);")
        v.addWidget(self.foot)
        self._text = ""
        self._loading = False
        self._dirty = False
        self.set_state("empty")

    # ---------------------------------------------------------------- content
    def text(self):
        if self.stack.currentIndex() == 1:
            return self.editor.toPlainText()
        return self._text

    def set_text(self, md_text, running=False):
        self._text = md_text or ""
        self._render(running)

    def append_text(self, chunk):
        self._text += chunk
        self._dirty = True

    def flush(self, running=True):
        """Show text that arrived since the last call (called by the window's timer while the summary is written)."""
        if self._dirty:
            self._dirty = False
            self._render(running)

    def _render(self, running=False):
        if self.stack.currentIndex() == 1 and not running:
            self._loading = True
            self.editor.setPlainText(self._text)
            self._loading = False
            return
        bar = self.browser.verticalScrollBar()
        at_end = bar.value() >= bar.maximum() - 4
        css = ("<style>h2{margin-top:10px;margin-bottom:4px} h3{margin-top:8px;margin-bottom:2px} "
               "li{margin-bottom:3px} a{text-decoration:none}</style>")
        self.browser.setHtml(css + to_html(self._text) + ("<p><i>…</i></p>" if running else ""))
        if running and at_end:
            bar.setValue(bar.maximum())

    def set_font_size(self, pt):
        f = self.browser.font()
        f.setPointSize(int(pt))
        self.browser.setFont(f)
        f2 = QFont(self.editor.font())
        f2.setPointSize(int(pt))
        self.editor.setFont(f2)

    def set_state(self, state, message="", footer=None):
        """empty / waiting / running / done / stopped / failed / no_model."""
        running = state == "running"
        self.progress.setVisible(running)
        self.b_stop.setVisible(running)
        self.b_run.setVisible(not running)
        has = bool(self._text.strip())
        self.b_run.setText("Write again" if has and state in ("done", "stopped", "failed") else "Write summary")
        for b in (self.b_copy, self.b_save, self.b_edit):
            b.setEnabled(has and not running)
        if running and self.b_edit.isChecked():
            self.b_edit.setChecked(False)
        default = {"empty": "No summary yet.",
                   "waiting": "The summary is written when the transcript is complete.",
                   "running": "Writing the summary…",
                   "done": "Summary written on this computer.",
                   "stopped": "Stopped. The text written so far is shown.",
                   "failed": "The summary could not be written.",
                   "no_model": "No summary model is installed. Open Options > AI Summary to download one."}[state]
        self.state_label.setText(message or default)
        if footer is not None:
            self.foot.setText(footer)
        if state in ("empty", "no_model") and not has:
            self.browser.setHtml("<p style='color:gray'>" + html.escape(default) + "</p>" +
                                 ("<p style='color:gray'>The summary is written by a small language model on this "
                                  "computer after the transcript is complete. Nothing leaves the computer.</p>"))

    def set_progress(self, frac):
        self.progress.setValue(int(max(0.0, min(1.0, frac)) * 1000))

    def set_busy_label(self, text):
        self.state_label.setText(text)

    def clear(self):
        self._text = ""
        if self.b_edit.isChecked():
            self.b_edit.setChecked(False)
        self.foot.setText(" ")
        self.set_state("empty")

    # ---------------------------------------------------------------- interaction
    def _anchor(self, url):
        if url.scheme() == "time":
            try:
                self.time_clicked.emit(float(url.path() or url.toString().split(":", 1)[1]))
            except ValueError:
                pass

    def _toggle_edit(self, on):
        if on:
            self._loading = True
            self.editor.setPlainText(self._text)
            self._loading = False
            self.stack.setCurrentIndex(1)
            self.b_edit.setText("Done")
            self.editor.setFocus()
        else:
            self._text = self.editor.toPlainText()
            self.stack.setCurrentIndex(0)
            self.b_edit.setText("Edit")
            self._render(False)

    def _edited(self):
        if not self._loading:
            self.edited.emit()
