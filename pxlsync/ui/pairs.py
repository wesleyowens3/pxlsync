"""FreeFileSync-style folder pair panel: sources on the left, destinations on the right."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QLabel, QLineEdit, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from .widgets import button, label, section

ROW_H = 38
BROWSE_W = 96
MID_W = 70
ICON_W = 30
MAX_VISIBLE_ROWS = 5


class PathEdit(QLineEdit):
    """Line edit that accepts a folder dragged from Finder / Explorer."""

    dropped = Signal()

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText(text)
        self.setCursorPosition(0)  # show the start of long paths, like FreeFileSync
        self.setToolTip(text)

    def __init__(self, placeholder: str):
        super().__init__()
        self.setPlaceholderText(placeholder)
        self.setAcceptDrops(True)

    def _folder(self, event) -> str | None:
        urls = event.mimeData().urls() if event.mimeData().hasUrls() else []
        if urls and urls[0].isLocalFile():
            p = Path(urls[0].toLocalFile())
            return str(p if p.is_dir() else p.parent)
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if self._folder(event):
            event.acceptProposedAction()
            self.setProperty("drop", True)
            self.style().polish(self)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self.setProperty("drop", False)
        self.style().polish(self)

    def dropEvent(self, event) -> None:  # noqa: N802
        self.setProperty("drop", False)
        self.style().polish(self)
        if folder := self._folder(event):
            self.setText(folder)
            event.acceptProposedAction()
            self.dropped.emit()


class FolderPairs(QWidget):
    """Edits a list of (source, destination) folder pairs. Emits ``changed`` after edits."""

    changed = Signal()

    def __init__(self, pairs: list[tuple[str, str]] | None = None):
        super().__init__()
        self.lefts: list[PathEdit] = []
        self.rights: list[PathEdit] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 10, 22, 12)
        outer.setSpacing(4)

        head = QGridLayout()
        head.setHorizontalSpacing(6)
        self.add_btn = button("+", "icon", lambda: self.add_pair(focus=True))
        self.add_btn.setToolTip("Add a folder pair")
        head.addWidget(self.add_btn, 0, 0)
        head.addLayout(self._title("Source"), 0, 1)
        self.swap_btn = button("⇄", "icon", self.swap_all)
        self.swap_btn.setToolTip("Swap source and destination of every pair")
        head.addWidget(self.swap_btn, 0, 3, Qt.AlignmentFlag.AlignCenter)
        head.addLayout(self._title("Destination"), 0, 4)
        self._match_columns(head)
        outer.addLayout(head)

        self.rows_host = QWidget()
        self.grid = QGridLayout(self.rows_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(6)
        self.scroll = QScrollArea()
        self.scroll.setWidget(self.rows_host)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        outer.addWidget(self.scroll)

        for left, right in pairs or [("", "")]:
            self._append(left, right)
        self._rebuild()

    # ---------- public ----------
    def pairs(self) -> list[tuple[str, str]]:
        return [(l.text().strip(), r.text().strip()) for l, r in zip(self.lefts, self.rights)]

    def add_pair(self, focus: bool = False) -> None:
        self._append("", "")
        self._rebuild()
        if focus:
            self.lefts[-1].setFocus()
            self.scroll.ensureWidgetVisible(self.lefts[-1])
        self.changed.emit()

    def remove_pair(self, index: int) -> None:
        if len(self.lefts) == 1:
            self.lefts[0].clear()
            self.rights[0].clear()
        else:
            for w in (self.lefts.pop(index), self.rights.pop(index)):
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._rebuild()
        self.changed.emit()

    def swap(self, index: int) -> None:
        l, r = self.lefts[index], self.rights[index]
        a, b = l.text(), r.text()
        l.setText(b)
        r.setText(a)
        self.changed.emit()

    def swap_all(self) -> None:
        for l, r in zip(self.lefts, self.rights):
            a, b = l.text(), r.text()
            l.setText(b)
            r.setText(a)
        self.changed.emit()

    # ---------- internals ----------
    def _append(self, left: str, right: str) -> None:
        l = PathEdit("Folder with the media to send")
        r = PathEdit("Where it should end up (NAS, drive, share)")
        l.setParent(self.rows_host)
        r.setParent(self.rows_host)
        l.setText(left)
        r.setText(right)
        for e in (l, r):
            e.editingFinished.connect(self._edited)
            e.dropped.connect(self.changed.emit)
        self.lefts.append(l)
        self.rights.append(r)

    def _edited(self) -> None:
        if self.sender().isModified():
            self.sender().setModified(False)
            self.changed.emit()

    def _browse(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text() or str(Path.home()))
        if d:
            edit.setText(d)
            self.changed.emit()

    @staticmethod
    def _title(text: str):
        from .widgets import row
        return row(section(text), label("drag & drop or browse", "hint"), None, spacing=12)

    @staticmethod
    def _match_columns(grid: QGridLayout) -> None:
        """Header and rows share widths: [+/−] [path] [browse] [mid] [path] [browse]."""
        for col, width in ((0, ICON_W), (2, BROWSE_W), (3, MID_W), (5, BROWSE_W)):
            grid.setColumnMinimumWidth(col, width)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(4, 1)

    def _browse_btn(self, edit: QLineEdit):
        b = button("Browse", "ghost", lambda _=False: self._browse(edit))
        b.setFixedWidth(BROWSE_W)
        return b

    def _rebuild(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w and w not in self.lefts and w not in self.rights:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        n = len(self.lefts)
        for i, (l, r) in enumerate(zip(self.lefts, self.rights)):
            rm = button("−", "icon", lambda _=False, i=i: self.remove_pair(i))
            rm.setToolTip("Remove this pair" if n > 1 else "Clear this pair")
            self.grid.addWidget(rm, i, 0)
            self.grid.addWidget(l, i, 1)
            self.grid.addWidget(self._browse_btn(l), i, 2)
            mid = QWidget()
            mid.setFixedWidth(MID_W)
            ml = QGridLayout(mid)
            ml.setContentsMargins(0, 0, 0, 0)
            ml.setHorizontalSpacing(4)
            num = QLabel(str(i + 1))
            num.setObjectName("pairnum")
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sw = button("⇄", "icon", lambda _=False, i=i: self.swap(i))
            sw.setToolTip("Swap this pair")
            ml.addWidget(num, 0, 0)
            ml.addWidget(sw, 0, 1)
            self.grid.addWidget(mid, i, 3, Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(r, i, 4)
            self.grid.addWidget(self._browse_btn(r), i, 5)
        self._match_columns(self.grid)
        self.scroll.setFixedHeight(min(n, MAX_VISIBLE_ROWS) * ROW_H + 2)
