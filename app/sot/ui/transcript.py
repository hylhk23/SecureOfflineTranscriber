"""Transcript tab (FR-UI-05): editable, one block per paragraph/segment, clickable times, amber 'check' lines,
and proofreading: the sentence being played is highlighted and followed (version 2.1.2)."""
import re
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (QColor, QPalette, QTextBlockFormat, QTextBlockUserData, QTextCharFormat, QTextCursor,
                           QTextFormat)
from PySide6.QtWidgets import QTextEdit

from ..exporters import fmt_time

PREFIX = re.compile(r"^\s*\[\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?\]\s*")


class BlockData(QTextBlockUserData):
    """Times of a block, plus where each sentence (segment) sits in its text while the text is unedited."""

    def __init__(self, start, end, lang="", flag="", subs=None, text=""):
        super().__init__()
        self.start, self.end, self.lang, self.flag = start, end, lang, flag
        self.subs = subs or []
        self.text = text


class TranscriptEdit(QTextEdit):
    time_clicked = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setPlaceholderText("Open a recording (Ctrl+O) or drop it here. Everything stays on this PC.")
        self.setAcceptDrops(False)  # the window takes dropped files
        self.setMouseTracking(True)
        self.setUndoRedoEnabled(True)
        self._manual = 0.0      # last time the user scrolled or typed: following waits a moment
        self._hl = None         # (block number, sentence index) highlighted now
        self._cache = None      # sentences(), rebuilt after any change to the text
        self.verticalScrollBar().actionTriggered.connect(self._user_scrolled)
        self.document().contentsChanged.connect(self._invalidate)

    def _invalidate(self):
        self._cache = None

    def accent(self):
        return self.palette().color(QPalette.Link)

    # ------------------------------------------------------------------ content
    def _data(self, b):
        return BlockData(b["start"], b["end"], b.get("lang", ""), b.get("flag", ""), b.get("subs"),
                         b.get("orig_text", b["text"]))

    def set_blocks(self, blocks, keep_modified=False):
        self.blockSignals(True)
        self.clear()
        self._cache = None
        self._hl = None
        self.setExtraSelections([])
        cur = self.textCursor()
        amber = QColor(255, 196, 61, 70)
        for i, b in enumerate(blocks):
            if i:
                cur.insertBlock()
            bf = QTextBlockFormat()
            bf.setBottomMargin(6)
            if b.get("flag") == "check":
                bf.setBackground(amber)
            cur.setBlockFormat(bf)
            self._insert_time(cur, b["start"])
            cur.insertText(b["text"], QTextCharFormat())
            cur.block().setUserData(self._data(b))
        self._cache = None
        self.document().clearUndoRedoStacks()
        self.document().setModified(False)
        self.blockSignals(False)
        self.moveCursor(QTextCursor.Start)

    def _insert_time(self, cur, start):
        tf = QTextCharFormat()
        tf.setAnchor(True)
        tf.setAnchorHref(f"t:{start:.3f}")
        tf.setForeground(self.accent())
        tf.setToolTip("Play from here (Ctrl+click on any sentence plays it)")
        cur.insertText(f"[{fmt_time(start)}]", tf)
        cur.insertText(" ", QTextCharFormat())

    def append_block(self, b):
        """Live text while a job runs (FR-ASR-01)."""
        cur = QTextCursor(self.document())
        cur.movePosition(QTextCursor.End)
        if not self.document().isEmpty():
            cur.insertBlock()
        bf = QTextBlockFormat()
        bf.setBottomMargin(6)
        cur.setBlockFormat(bf)
        self._insert_time(cur, b["start"])
        cur.insertText(b["text"], QTextCharFormat())
        b = dict(b)
        b.setdefault("subs", [{"start": b["start"], "end": b["end"], "flag": b.get("flag", ""), "c0": 0,
                               "c1": len(b["text"])}])
        cur.block().setUserData(self._data(b))
        self._cache = None
        self.document().clearUndoRedoStacks()
        if time.monotonic() - self._manual > 4:
            self.ensureCursorVisible()

    def blocks(self):
        """The edited transcript (FR-OUT-05). New lines a user typed join the block above."""
        out = []
        block = self.document().begin()
        while block.isValid():
            text = PREFIX.sub("", block.text()).strip()
            data = block.userData()
            if isinstance(data, BlockData):
                out.append({"start": data.start, "end": data.end, "text": text, "lang": data.lang,
                            "flag": data.flag})
            elif text and out:
                out[-1]["text"] = (out[-1]["text"] + " " + text).strip()
            block = block.next()
        return [b for b in out if b["text"]]

    # ------------------------------------------------------------------ sentences (proofreading)
    def _prefix_len(self, block):
        m = PREFIX.match(block.text())
        return m.end() if m else 0

    def sentences(self):
        """Every sentence in order: start, end, flag, block, and its character range when the text is unedited."""
        if self._cache is not None:
            return self._cache
        out = []
        block = self.document().begin()
        while block.isValid():
            d = block.userData()
            if isinstance(d, BlockData):
                pl = self._prefix_len(block)
                body = block.text()[pl:]
                valid = bool(d.subs) and body.strip() == (d.text or "").strip()
                subs = d.subs if valid else [{"start": d.start, "end": d.end, "flag": d.flag, "c0": 0,
                                              "c1": len(body)}]
                if not valid and d.subs:  # edited paragraph: keep the sentence times, highlight the paragraph
                    subs = [dict(s, c0=0, c1=len(body)) for s in d.subs]
                for i, s in enumerate(subs):
                    out.append({"start": s["start"], "end": s["end"], "flag": s.get("flag", ""), "block": block,
                                "c0": pl + s["c0"], "c1": pl + s["c1"], "exact": valid, "index": i})
            block = block.next()
        self._cache = out
        return out

    def sentence_at_time(self, t, sents=None):
        best = None
        for s in sents if sents is not None else self.sentences():
            if s["start"] <= t + 0.05:
                best = s
            else:
                break
        return best

    def sentence_at_cursor(self, cur=None):
        cur = cur or self.textCursor()
        block = cur.block()
        pos = cur.position() - block.position()
        best = None
        for s in self.sentences():
            if s["block"] == block:
                if best is None or (s["exact"] and s["c0"] <= pos):
                    best = s
            elif best is not None:
                break
        return best

    def select_sentence(self, s):
        cur = QTextCursor(s["block"])
        cur.setPosition(s["block"].position() + s["c0"])
        cur.setPosition(s["block"].position() + s["c1"], QTextCursor.KeepAnchor)
        self.setTextCursor(cur)
        self.ensureCursorVisible()

    def highlight_time(self, t, follow=True):
        """Highlight the paragraph and the sentence being played; scroll so it stays in view."""
        s = self.sentence_at_time(t)
        if s is None:
            self.clear_highlight()
            return
        key = (s["block"].blockNumber(), s["index"], s["exact"])
        if key == self._hl:
            return
        self._hl = key
        hi = self.palette().color(QPalette.Highlight)
        para = QTextEdit.ExtraSelection()
        pcur = QTextCursor(s["block"])  # the whole paragraph, every line full width
        pcur.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
        para.cursor = pcur
        pc = QColor(hi)
        pc.setAlpha(28)
        para.format.setBackground(pc)
        para.format.setProperty(QTextFormat.FullWidthSelection, True)
        sels = [para]
        if s["exact"]:
            sent = QTextEdit.ExtraSelection()
            c = QTextCursor(s["block"])
            c.setPosition(s["block"].position() + s["c0"])
            c.setPosition(s["block"].position() + s["c1"], QTextCursor.KeepAnchor)
            sent.cursor = c
            sc = QColor(hi)
            sc.setAlpha(80)
            sent.format.setBackground(sc)
            sels.append(sent)
        self.setExtraSelections(sels)
        if follow:
            self._follow(s)

    def clear_highlight(self):
        if self._hl is not None:
            self._hl = None
            self.setExtraSelections([])

    def _follow(self, s):
        if time.monotonic() - self._manual < 4:  # the user is reading or typing elsewhere: do not jump
            return
        c = QTextCursor(s["block"])
        c.setPosition(s["block"].position() + s["c0"])
        r = self.cursorRect(c)
        h = self.viewport().height()
        if r.top() < h * 0.1 or r.bottom() > h * 0.85:
            bar = self.verticalScrollBar()
            bar.setValue(bar.value() + r.top() - int(h * 0.3))

    def _user_scrolled(self, *_):
        self._manual = time.monotonic()

    # ------------------------------------------------------------------ old helpers
    def block_at_time(self, t):
        block = self.document().begin()
        best = None
        while block.isValid():
            d = block.userData()
            if isinstance(d, BlockData) and d.start <= t + 0.01:
                best = block
            block = block.next()
        return best

    def go_to_time(self, t):
        b = self.block_at_time(t)
        if b:
            cur = QTextCursor(b)
            self.setTextCursor(cur)
            self.ensureCursorVisible()

    # ------------------------------------------------------------------ mouse and keys
    def mouseReleaseEvent(self, e):
        href = self.anchorAt(e.position().toPoint())
        super().mouseReleaseEvent(e)
        if e.button() != Qt.LeftButton or self.textCursor().hasSelection():
            return
        if href.startswith("t:"):
            self.time_clicked.emit(float(href[2:]))
        elif e.modifiers() & Qt.ControlModifier:  # Ctrl+click: play the sentence under the mouse
            s = self.sentence_at_cursor(self.cursorForPosition(e.position().toPoint()))
            if s:
                self.time_clicked.emit(s["start"])

    def mouseMoveEvent(self, e):
        href = self.anchorAt(e.position().toPoint())
        self.viewport().setCursor(Qt.PointingHandCursor if href.startswith("t:") else Qt.IBeamCursor)
        super().mouseMoveEvent(e)

    def wheelEvent(self, e):
        self._manual = time.monotonic()
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        if e.key() not in (Qt.Key_F5, Qt.Key_F6, Qt.Key_F7, Qt.Key_F8, Qt.Key_F9, Qt.Key_Escape):
            self._manual = time.monotonic()
        super().keyPressEvent(e)
