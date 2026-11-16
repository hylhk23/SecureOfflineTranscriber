"""Transcript tab: editable, one block per paragraph/segment, clickable times, amber 'check' lines,
and proofreading: the sentence being played is highlighted and followed (version 2.1.2).
Version 2.4.0: a paragraph can start with its speaker (marker shape, name, "(?)" when unsure), drawn as a link
"s:<speaker id>" after the time link; both are left out when the text is read back, so editing never mixes them
into the transcript. Names come from the window (speaker_names); the label is redrawn, never typed over."""
import re
import time

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import (QColor, QPalette, QTextBlockFormat, QTextBlockUserData, QTextCharFormat, QTextCursor,
                           QTextFormat)
from PySide6.QtWidgets import QTextEdit

from ..exporters import fmt_time

PREFIX = re.compile(r"^\s*\[\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?\]\s*")


class BlockData(QTextBlockUserData):
    """Times of a block, plus where each sentence (segment) sits in its text while the text is unedited."""

    def __init__(self, start, end, lang="", flag="", subs=None, text="", spk=None, spk_unsure=False):
        super().__init__()
        self.start, self.end, self.lang, self.flag = start, end, lang, flag
        self.subs = subs or []
        self.text = text
        self.spk, self.spk_unsure = spk, bool(spk_unsure)


class TranscriptEdit(QTextEdit):
    time_clicked = Signal(float)
    speaker_clicked = Signal(object, QPoint)   # (block, global position): the window shows the speaker menu
    context_hook = None                        # window function adding speaker items to the right-click menu

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
        self.speaker_names = {}  # speaker id -> name (2.4.0)
        self.speaker_order = []  # speaker ids in the order their markers were given
        self.label_style = "colon"
        self.verticalScrollBar().actionTriggered.connect(self._user_scrolled)
        self.document().contentsChanged.connect(self._invalidate)

    def _invalidate(self):
        self._cache = None

    def accent(self):
        return self.palette().color(QPalette.Link)

    # ------------------------------------------------------------------ content
    def _data(self, b):
        return BlockData(b["start"], b["end"], b.get("lang", ""), b.get("flag", ""), b.get("subs"),
                         b.get("orig_text", b["text"]), b.get("spk"), b.get("spk_unsure"))

    # ------------------------------------------------------------------ speaker labels (2.4.0)
    def _dark(self):
        return self.palette().color(QPalette.Base).lightness() < 128

    def marker_of(self, spk):
        from ..speakers import marker
        if spk not in self.speaker_order:
            self.speaker_order.append(spk)
        shape, light, dark = marker(self.speaker_order.index(spk))
        return shape, (dark if self._dark() else light)

    def _insert_speaker(self, cur, spk, unsure):
        if not spk:
            return
        name = self.speaker_names.get(spk) or spk
        shape, colour = self.marker_of(spk)
        href = f"s:{spk}"
        mf = QTextCharFormat()
        mf.setAnchor(True)
        mf.setAnchorHref(href)
        mf.setForeground(QColor(colour))
        mf.setToolTip("Speaker: click to rename or change (Edit > Rename Speaker, Alt+1 to Alt+9)")
        cur.insertText(shape + " ", mf)
        nf = QTextCharFormat(mf)
        nf.setForeground(self.palette().color(QPalette.Text))
        nf.setFontWeight(700)
        q = " (?)" if unsure else ""
        cur.insertText(f"[{name}]{q}" if self.label_style == "brackets" else f"{name}{q}:", nf)
        cur.insertText(" ", QTextCharFormat())

    def body_start(self, block):
        """Characters at the start of a block that are the time and speaker links (and the spaces after them)."""
        it = block.begin()
        pos = 0
        while not it.atEnd():
            frag = it.fragment()
            if not frag.isValid():
                break
            fmt = frag.charFormat()
            text = frag.text()
            if fmt.isAnchor() and fmt.anchorHref()[:2] in ("t:", "s:"):
                pos = frag.position() + frag.length() - block.position()
            elif not text.strip():
                pos = frag.position() + frag.length() - block.position()
            else:
                # a plain fragment that starts with spaces: skip them
                pos = frag.position() - block.position() + (len(text) - len(text.lstrip()))
                break
            it += 1
        if pos == 0:  # no links (pasted or typed text): fall back to the time pattern
            m = PREFIX.match(block.text())
            return m.end() if m else 0
        return pos

    def body_text(self, block):
        return block.text()[self.body_start(block):]

    def _speaker_range(self, block):
        """(start, end) document positions of a block's speaker label and the space after it; with no label, an
        empty range where one goes (after the time and its space). Neighbouring plain text can share a fragment
        with that space, so positions are counted in characters, not fragments."""
        base = block.position()
        text = block.text()
        time_end, a, e = 0, None, None
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            fmt = frag.charFormat()
            href = fmt.anchorHref() if fmt.isAnchor() else ""
            r0, r1 = frag.position() - base, frag.position() - base + frag.length()
            if href.startswith("t:"):
                time_end = r1
            elif href.startswith("s:"):
                a = r0 if a is None else a
                e = r1
            elif text[r0:r1].strip():
                break
            it += 1
        spaces = lambda i: len(text[i:]) - len(text[i:].lstrip(" "))
        if a is None:
            ins = time_end + spaces(time_end)
            return base + ins, base + ins
        return base + a, base + e + spaces(e)

    def set_block_speaker(self, block, spk, unsure=False):
        """Give one paragraph a speaker (or none) and redraw its label."""
        d = block.userData()
        if not isinstance(d, BlockData):
            return
        d.spk, d.spk_unsure = spk, bool(unsure)
        a, b = self._speaker_range(block)
        cur = QTextCursor(self.document())
        cur.setPosition(a)
        cur.setPosition(b, QTextCursor.KeepAnchor)
        cur.removeSelectedText()
        self._insert_speaker(cur, spk, unsure)
        self._cache = None

    def refresh_labels(self):
        """Redraw every speaker label (after a rename, merge or theme change). Clears Undo."""
        modified = self.document().isModified()
        self.document().setUndoRedoEnabled(False)
        block = self.document().begin()
        while block.isValid():
            d = block.userData()
            if isinstance(d, BlockData):
                self.set_block_speaker(block, d.spk, d.spk_unsure)
            block = block.next()
        self.document().setUndoRedoEnabled(True)
        self.document().setModified(modified)
        self._cache = None

    def blocks_with_speaker(self, spk):
        out = []
        block = self.document().begin()
        while block.isValid():
            d = block.userData()
            if isinstance(d, BlockData) and d.spk == spk:
                out.append(block)
            block = block.next()
        return out

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
            self._insert_speaker(cur, b.get("spk"), b.get("spk_unsure"))
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
        """Live text while a job runs."""
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
        """The edited transcript. New lines a user typed join the block above."""
        out = []
        block = self.document().begin()
        while block.isValid():
            data = block.userData()
            text = (self.body_text(block) if isinstance(data, BlockData) else PREFIX.sub("", block.text())).strip()
            if isinstance(data, BlockData):
                b = {"start": data.start, "end": data.end, "text": text, "lang": data.lang, "flag": data.flag}
                if data.spk:
                    b["spk"] = data.spk
                    b["spk_unsure"] = data.spk_unsure
                out.append(b)
            elif text and out:
                out[-1]["text"] = (out[-1]["text"] + " " + text).strip()
            block = block.next()
        return [b for b in out if b["text"]]

    # ------------------------------------------------------------------ sentences (proofreading)
    def _prefix_len(self, block):
        return self.body_start(block)

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
        elif href.startswith("s:"):
            block = self.cursorForPosition(e.position().toPoint()).block()
            self.speaker_clicked.emit(block, e.globalPosition().toPoint())
        elif e.modifiers() & Qt.ControlModifier:  # Ctrl+click: play the sentence under the mouse
            s = self.sentence_at_cursor(self.cursorForPosition(e.position().toPoint()))
            if s:
                self.time_clicked.emit(s["start"])

    def mouseMoveEvent(self, e):
        href = self.anchorAt(e.position().toPoint())
        self.viewport().setCursor(Qt.PointingHandCursor if href[:2] in ("t:", "s:") else Qt.IBeamCursor)
        super().mouseMoveEvent(e)

    def contextMenuEvent(self, e):
        menu = self.createStandardContextMenu(e.pos())
        if self.context_hook:
            self.context_hook(menu, self.cursorForPosition(e.pos()).block())
        menu.exec(e.globalPos())

    def wheelEvent(self, e):
        self._manual = time.monotonic()
        super().wheelEvent(e)

    def keyPressEvent(self, e):
        if e.key() not in (Qt.Key_F5, Qt.Key_F6, Qt.Key_F7, Qt.Key_F8, Qt.Key_F9, Qt.Key_Escape):
            self._manual = time.monotonic()
        super().keyPressEvent(e)
