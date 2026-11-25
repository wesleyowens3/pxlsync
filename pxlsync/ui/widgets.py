"""Small building blocks and a background-task helper."""

from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QWidget,
)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)


class Task(QRunnable):
    """Run ``fn(progress)`` off the UI thread; results come back as Qt signals."""

    def __init__(self, fn: Callable):
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn(lambda d, t: self.signals.progress.emit(d, t))
        except Exception as exc:  # shown to the user, never swallowed
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.signals.done.emit(result)


_tasks: set[Task] = set()  # keep Python wrappers alive until they finish


def run_task(fn, on_done, on_failed, on_progress=None) -> None:
    task = Task(fn)
    task.setAutoDelete(False)
    _tasks.add(task)
    task.signals.done.connect(lambda r: (_tasks.discard(task), on_done(r)))
    task.signals.failed.connect(lambda e: (_tasks.discard(task), on_failed(e)))
    if on_progress:
        task.signals.progress.connect(on_progress)
    QThreadPool.globalInstance().start(task)


def label(text: str, name: str | None = None) -> QLabel:
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    return w


def section(text: str) -> QLabel:
    return label(text.upper(), "section")


def button(text: str, kind: str | None = None, slot=None) -> QPushButton:
    b = QPushButton(text.upper() if kind != "chip" else text)
    if kind:
        b.setObjectName(kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if kind == "chip":  # style sheets don't size these reliably; do it from the text
        b.setMinimumWidth(b.fontMetrics().horizontalAdvance(text) + 18)
    if slot:
        b.clicked.connect(slot)
    return b


def hrule() -> QFrame:
    f = QFrame()
    f.setObjectName("rule")
    return f


def vrule() -> QFrame:
    f = QFrame()
    f.setObjectName("vrule")
    return f


def row(*widgets, spacing: int = 8, stretch_at: int | None = None) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(0, 0, 0, 0)
    for i, w in enumerate(widgets):
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w, 1 if i == stretch_at else 0)
        else:
            lay.addLayout(w)
    return lay


def table(headers: list[str], stretch_col: int | None = None) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels([h.upper() for h in headers])
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setShowGrid(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setWordWrap(False)
    t.verticalHeader().setDefaultSectionSize(28)
    hdr = t.horizontalHeader()
    hdr.setHighlightSections(False)
    hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    if stretch_col is not None:
        hdr.setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)
    return t


def cell(text: object, color: str | None = None, bold: bool = False, tip: str | None = None) -> QTableWidgetItem:
    item = QTableWidgetItem("" if text is None else str(text))
    if color:
        item.setForeground(QColor(color))
    if bold:
        f = item.font()
        f.setBold(True)
        item.setFont(f)
    if tip:
        item.setToolTip(tip)
    return item


def fill(t: QTableWidget, rows: list[list[QTableWidgetItem]]) -> None:
    t.setUpdatesEnabled(False)
    t.setRowCount(len(rows))
    for r, items in enumerate(rows):
        for c, item in enumerate(items):
            t.setItem(r, c, item)
    t.setUpdatesEnabled(True)
