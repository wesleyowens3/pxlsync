"""04 COMPARE + SYNC: FreeFileSync-style side-by-side compare, then synchronize."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QFile, QModelIndex, QSettings, Qt
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGridLayout, QHeaderView, QLineEdit,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QTableView,
)

from .. import syncer
from ..compare import (
    MODE_HELP, Action, Comparison, Method, Mode, Row, compare, hold, parse_excludes, plan,
    possible_actions,
)
from ..probe import probe_many
from . import theme
from .main_window import Page
from .widgets import button, label, row, run_task, section

POLICY_BLOCK = "Block the sync"
POLICY_SKIP = "Hold back failing files"
POLICY_WARN = "Sync them anyway"

DEL_FOLDER = "Keep in .pxlsync-trash folder"
DEL_SYSTEM = "Move to Trash / Recycle Bin"
DEL_PERMANENT = "Delete permanently"

# grid "buckets", used for the show/hide chips
B_NEW_R, B_UPD_R, B_LEFT, B_DEL, B_CONFLICT, B_HELD, B_SAME = (
    "new", "update", "left", "delete", "conflict", "held", "same")
BUCKETS = [  # key, chip label, colour
    (B_NEW_R, "→ NEW", theme.PASS),
    (B_UPD_R, "→ UPDATE", theme.TEXT),
    (B_LEFT, "← TO SOURCE", theme.LEFT),
    (B_DEL, "✕ DELETE", theme.FAIL),
    (B_CONFLICT, "! CONFLICT", theme.WARN),
    (B_HELD, "■ HELD", theme.WARN),
    (B_SAME, "= NO ACTION", theme.MUTED),
]


def bucket(r: Row) -> str:
    if r.action == Action.TO_RIGHT:
        return B_UPD_R if r.right else B_NEW_R
    if r.action == Action.TO_LEFT:
        return B_LEFT
    if r.action in (Action.DELETE_RIGHT, Action.DELETE_LEFT):
        return B_DEL
    if r.held:
        return B_HELD
    if r.conflict:
        return B_CONFLICT
    return B_SAME


ACTION_TEXT = {
    B_NEW_R: ("→  NEW", theme.PASS),
    B_UPD_R: ("→  UPDATE", theme.TEXT),
    B_DEL: ("✕  DELETE", theme.FAIL),
    B_CONFLICT: ("!  CONFLICT", theme.WARN),
    B_HELD: ("■  HELD", theme.WARN),
    B_SAME: ("=", theme.MUTED),
}


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d  %H:%M")


class CompareModel(QAbstractTableModel):
    HEADERS = ["SOURCE", "SIZE", "MODIFIED", "CODEC", "ACTION", "DESTINATION", "SIZE", "MODIFIED"]
    MID = 4

    def __init__(self) -> None:
        super().__init__()
        self.comparisons: list[Comparison] = []
        self.rows: list[Row] = []
        self.visible: list[Row | int] = []  # an int is the header line of that folder pair
        self.shown = {k for k, *_ in BUCKETS} - {B_SAME}
        self.codec: dict[tuple[int, str], bool] = {}  # (pair, rel.casefold()) -> passed?
        self.bold = QFont()
        self.bold.setBold(True)

    def set_rows(self, comparisons: list[Comparison]) -> None:
        self.comparisons = comparisons
        self.rows = [r for c in comparisons for r in c.rows]
        self.refilter()

    def refilter(self) -> None:
        self.beginResetModel()
        self.visible = []
        grouped = len(self.comparisons) > 1
        for i, cmp in enumerate(self.comparisons):
            if grouped:
                self.visible.append(i)
            self.visible += [r for r in cmp.rows if bucket(r) in self.shown]
        self.endResetModel()

    def row_at(self, i: int) -> Row | None:
        item = self.visible[i]
        return None if isinstance(item, int) else item

    def header_rows(self) -> list[int]:
        return [n for n, item in enumerate(self.visible) if isinstance(item, int)]

    def _header_text(self, i: int) -> str:
        cmp = self.comparisons[i]
        acting = [r for r in cmp.rows if r.action != Action.NONE]
        copies = [r for r in acting if r.action in (Action.TO_RIGHT, Action.TO_LEFT)]
        info = (f"{len(copies)} to copy ({human(syncer.total_bytes(copies))}) · {len(acting) - len(copies)} to remove"
                if acting else "nothing to do")
        return f"PAIR {i + 1}     {cmp.left_root}   →   {cmp.right_root}        {info}"

    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.visible)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEADERS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole and section == self.MID:
            return Qt.AlignmentFlag.AlignCenter
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        item = self.visible[index.row()]
        col = index.column()
        if isinstance(item, int):  # folder pair header line
            if role == Qt.ItemDataRole.DisplayRole and col == 0:
                return self._header_text(item)
            if role == Qt.ItemDataRole.FontRole:
                return self.bold
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(theme.TEXT)
            if role == Qt.ItemDataRole.BackgroundRole:
                return QColor(theme.BOX)
            return None
        r = item
        b = bucket(r)
        side = r.left if col < self.MID else r.right
        if role == Qt.ItemDataRole.DisplayRole:
            if col == self.MID:
                if b == B_LEFT:
                    return "←  UPDATE" if r.left else "←  NEW"
                return ACTION_TEXT[b][0]
            if col == 3:
                ok = self.codec.get((r.pair, r.left.rel.casefold())) if r.left else None
                return "" if ok is None else ("PASS" if ok else "FAIL")
            if side is None:
                return ""
            c = col if col < self.MID else col - 5
            return [side.rel, human(side.size), when(side.mtime)][c]
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == self.MID:
                return QColor(theme.LEFT if b == B_LEFT else ACTION_TEXT[b][1])
            if col == 3:
                ok = self.codec.get((r.pair, r.left.rel.casefold())) if r.left else None
                return QColor(theme.MUTED if ok is None else theme.PASS if ok else theme.FAIL)
            if b == B_SAME:
                return QColor(theme.MUTED)
            if (col < self.MID and r.action == Action.DELETE_LEFT) or (col > self.MID and r.action == Action.DELETE_RIGHT):
                return QColor(theme.FAIL)
            # highlight the side that is newer / being copied from
            if (col < self.MID and r.action == Action.TO_RIGHT) or (col > self.MID and r.action == Action.TO_LEFT):
                return QColor(theme.TEXT)
            return QColor(theme.MUTED)
        if role == Qt.ItemDataRole.FontRole and col in (self.MID, 3):
            return self.bold
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col == self.MID or col == 3:
                return Qt.AlignmentFlag.AlignCenter
            if col in (1, 6):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ToolTipRole:
            tip = [r.rel, f"compare: {r.category.value}"]
            if r.note:
                tip.append(r.note)
            if r.overridden:
                tip.append("action set by hand")
            return "\n".join(tip)
        if role == Qt.ItemDataRole.BackgroundRole and col == self.MID:
            return QColor(theme.BOX)
        return None

    def counts(self) -> dict[str, int]:
        out = {k: 0 for k, *_ in BUCKETS}
        for r in self.rows:
            out[bucket(r)] += 1
        return out


class SyncPage(Page):
    def __init__(self, app):
        super().__init__(app, "Compare + sync",
                         "Source on the left, destination on the right. Nothing is copied until you press "
                         "SYNCHRONIZE. Right-click rows to change what happens to them.")
        # Like FreeFileSync: no page title, the grid gets the room. The intro becomes a tooltip.
        self.title_label.hide()
        self.blurb_label.hide()
        self.lay.setContentsMargins(26, 14, 26, 14)
        s = app.settings
        self.comparisons: list[Comparison] = []
        self.cancel_flag = threading.Event()
        self.busy = False

        self.method = QComboBox()
        self.method.addItems([m.value for m in Method])
        self.method.setCurrentText(s.value("cmp_method", Method.TIME_SIZE.value))
        self.mode = QComboBox()
        self.mode.addItems([m.value for m in Mode])
        for i, m in enumerate(Mode):
            self.mode.setItemData(i, MODE_HELP[m], Qt.ItemDataRole.ToolTipRole)
        self.mode.setCurrentText(s.value("sync_mode", Mode.MIRROR.value))
        if self.mode.currentIndex() < 0:
            self.mode.setCurrentIndex(0)
        self.mode_help = label(MODE_HELP[Mode(self.mode.currentText())], "hint")
        self.mode.currentTextChanged.connect(lambda t: self.mode_help.setText(MODE_HELP[Mode(t)]))
        self.policy = QComboBox()
        self.policy.addItems([POLICY_BLOCK, POLICY_SKIP, POLICY_WARN])
        self.policy.setCurrentText(s.value("sync_policy", POLICY_BLOCK))
        self.deletion = QComboBox()
        self.deletion.addItems([DEL_FOLDER, DEL_SYSTEM, DEL_PERMANENT])
        self.deletion.setItemData(0, "Removed and overwritten files are moved to a dated .pxlsync-trash folder "
                                  "on the same drive. Safest; works on NAS and server shares.", Qt.ItemDataRole.ToolTipRole)
        self.deletion.setItemData(1, "Uses the macOS Trash / Windows Recycle Bin. Network drives may not have one.",
                                  Qt.ItemDataRole.ToolTipRole)
        self.deletion.setItemData(2, "No way back.", Qt.ItemDataRole.ToolTipRole)
        self.deletion.setCurrentText(s.value("sync_deletion", DEL_FOLDER))
        self.excludes = QLineEdit(s.value("sync_excludes", ""))
        self.excludes.setPlaceholderText("Extra files to ignore, e.g.  *.tmp; *.lrf; Proxies/   (.DS_Store etc. are always ignored)")
        self.method.currentTextChanged.connect(lambda _=None: self.mark_stale("Compare method changed: compare again."))
        self.excludes.editingFinished.connect(lambda: self.mark_stale("Exclusions changed: compare again."))
        self.mode.currentTextChanged.connect(lambda _=None: self.replan(reset_overrides=True))
        self.policy.currentTextChanged.connect(lambda _=None: self.replan())

        self.compare_btn = button("Compare", "primary", self.run_compare)
        self.sync_btn = button("Synchronize", "primary", self.run_sync)
        self.compare_btn.setMinimumWidth(150)
        self.compare_btn.setToolTip("Compare every folder pair. Nothing is copied until you press SYNCHRONIZE.\n"
                                    "Right-click rows in the grid to change what happens to them.")
        self.sync_btn.setMinimumWidth(170)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        grid.addWidget(self.compare_btn, 0, 0, 2, 1)
        grid.addWidget(section("Compare by"), 0, 1)
        grid.addWidget(self.method, 0, 2)
        grid.addWidget(section("Sync mode"), 1, 1)
        grid.addWidget(self.mode, 1, 2)
        grid.addWidget(section("Failed codec"), 0, 3)
        grid.addWidget(self.policy, 0, 4)
        grid.addWidget(section("Removed files"), 1, 3)
        grid.addWidget(self.deletion, 1, 4)
        grid.addWidget(self.sync_btn, 0, 5, 2, 1)
        grid.addWidget(section("Ignore"), 2, 1)
        grid.addWidget(self.excludes, 2, 2, 1, 3)
        grid.addWidget(self.mode_help, 3, 2, 1, 3)
        grid.setColumnStretch(2, 1)
        grid.setColumnStretch(4, 1)
        for b in (self.compare_btn, self.sync_btn):
            b.setSizePolicy(b.sizePolicy().horizontalPolicy(), b.sizePolicy().Policy.Expanding)
        self.lay.addLayout(grid)

        # show/hide chips with counts
        self.chips: dict[str, QPushButton] = {}
        self.summary = label("", "summary")
        chip_row = [section("Show")]
        for key, text, color in BUCKETS:
            c = button(text, "filter")
            c.setCheckable(True)
            c.setChecked(key != B_SAME)
            c.setStyleSheet(f"QPushButton:checked {{ color: {color}; border-color: {color}; }}")
            c.toggled.connect(lambda on, k=key: self._toggle(k, on))
            self.chips[key] = c
            chip_row.append(c)
        self.lay.addLayout(row(*chip_row, None, spacing=6))

        self.model = CompareModel()
        self.view = QTableView()
        self.view.setModel(self.model)
        self.model.modelReset.connect(self._apply_spans)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(26)
        self.view.setShowGrid(False)
        self.view.setAlternatingRowColors(True)
        self.view.setWordWrap(False)
        self.view.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        hdr = self.view.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for col in range(self.model.columnCount()):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.lay.addWidget(self.view, 1)

        self.progress = QProgressBar()
        self.progress_text = label("", "hint")
        self.cancel_btn = button("Cancel", "ghost", self.cancel_flag.set)
        self.verify_btn = button("Verify destination", "ghost", self.verify)
        self.lay.addLayout(row(self.summary, self.progress_text, None, self.progress, self.cancel_btn, self.verify_btn))
        self.progress.setFixedWidth(260)
        self._set_busy(False)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Log")
        self.log.setMaximumHeight(56)
        self.lay.addWidget(self.log)
        self.mark_stale("Press COMPARE to see what will be synced.")

    # ---------- state ----------
    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.progress_text.setText(text)
        self.compare_btn.setEnabled(not busy)
        for w in (self.method, self.mode, self.policy, self.deletion, self.excludes):
            w.setEnabled(not busy)
        self.update_sync_button()

    def _apply_spans(self) -> None:
        self.view.clearSpans()
        for n in self.model.header_rows():
            self.view.setSpan(n, 0, 1, self.model.columnCount())

    def all_rows(self) -> list[Row]:
        return [r for c in self.comparisons for r in c.rows]

    def mark_stale(self, message: str) -> None:
        self.comparisons = []
        self.model.set_rows([])
        self.summary.setText(message.upper())
        self.progress_text.setText("") if hasattr(self, "progress_text") else None
        self.summary.setStyleSheet(f"color: {theme.MUTED};")
        self._update_chips()
        self.update_sync_button()

    def on_show(self) -> None:
        self.update_sync_button()

    def codec_changed(self) -> None:
        if self.comparisons:
            self.replan()

    def _toggle(self, key: str, on: bool) -> None:
        (self.model.shown.add if on else self.model.shown.discard)(key)
        self.model.refilter()

    def _update_chips(self) -> None:
        counts = self.model.counts()
        for key, text, _ in BUCKETS:
            self.chips[key].setText(f"{text}  {counts[key]}")

    def _codec_results(self, root: Path):
        """(source-relative path, passed?) for scanned files inside ``root``."""
        sess = self.app.session
        if not sess.results or not self.app.scan_current():
            return []
        return [(p.relative_to(root).as_posix(), r.ok) for p, r in sess.results.items() if p.is_relative_to(root)]

    def replan(self, reset_overrides: bool = False) -> None:
        if not self.comparisons:
            return
        mode = Mode(self.mode.currentText())
        codec: dict[tuple[int, str], bool] = {}
        for i, cmp in enumerate(self.comparisons):
            if reset_overrides:
                for r in cmp.rows:
                    r.overridden = False
            plan(cmp.rows, mode)
            results = self._codec_results(cmp.left_root)
            codec.update({(i, rel.casefold()): ok for rel, ok in results})
            if self.policy.currentText() != POLICY_WARN:
                hold(cmp.rows, {rel for rel, ok in results if not ok}, "failed the codec check")
        self.model.codec = codec
        self.model.set_rows(self.comparisons)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        self._update_chips()
        rows = self.all_rows()
        acting = [r for r in rows if r.action != Action.NONE]
        copies = [r for r in acting if r.action in (Action.TO_RIGHT, Action.TO_LEFT)]
        deletes = [r for r in acting if r.action in (Action.DELETE_RIGHT, Action.DELETE_LEFT)]
        if not rows:
            text, color = "BOTH FOLDERS ARE EMPTY", theme.MUTED
        elif not acting:
            held = sum(r.held for r in rows)
            conflicts = sum(r.conflict for r in rows)
            if held or conflicts:
                parts = [f"{held} HELD BACK"] if held else []
                parts += [f"{conflicts} CONFLICTS"] if conflicts else []
                text, color = "NOTHING TO COPY  ·  " + "  ·  ".join(parts), theme.WARN
            else:
                text, color = "IN SYNC  ·  NOTHING TO DO", theme.PASS
        else:
            text = f"{len(copies)} TO COPY ({human(syncer.total_bytes(copies))})  ·  {len(deletes)} TO REMOVE"
            color = theme.TEXT
        self.summary.setText(text)
        self.summary.setStyleSheet(f"color: {color};")
        self.update_sync_button()

    def problems(self) -> list[str]:
        pairs = self.app.pairs()
        if not pairs:
            return ["Add a folder pair at the top: a source on the left, a destination on the right."]
        out = []
        for n, (src, dst) in enumerate(pairs, start=1):
            if not src:
                out.append(f"Pair {n}: choose a source folder.")
            elif not src.is_dir():
                out.append(f"Pair {n}: source folder doesn't exist ({src}).")
            if not dst:
                out.append(f"Pair {n}: choose a destination folder.")
            elif src:
                s_, d_ = src.resolve(), dst.resolve()
                if s_ == d_:
                    out.append(f"Pair {n}: source and destination are the same folder.")
                elif d_.is_relative_to(s_) or s_.is_relative_to(d_):
                    out.append(f"Pair {n}: one folder is inside the other.")
        # A destination must not be written by two pairs, or be inside any source.
        dests = [(n, d.resolve()) for n, (_, d) in enumerate(pairs, start=1) if d]
        for i, (n1, d1) in enumerate(dests):
            for n2, d2 in dests[i + 1:]:
                if d1 == d2 or d1.is_relative_to(d2) or d2.is_relative_to(d1):
                    out.append(f"Pairs {n1} and {n2} write to the same destination.")
            for n2, (s2, _) in enumerate(pairs, start=1):
                if s2 and n2 != n1 and (d1 == s2.resolve() or d1.is_relative_to(s2.resolve())):
                    out.append(f"Pair {n1}'s destination is inside pair {n2}'s source.")
        return out

    def update_sync_button(self) -> None:
        rows = self.all_rows()
        ok = bool(rows) and not self.busy and any(r.action != Action.NONE for r in rows)
        tip = ""
        if rows and self.policy.currentText() == POLICY_BLOCK:
            blocked = [r for r in rows if r.held]
            if blocked:
                ok, tip = False, f"{len(blocked)} files failed the codec check. Fix them or change 'Failed codec'."
                self.summary.setText(f"BLOCKED  ·  {len(blocked)} FILES FAILED THE CODEC CHECK")
                self.summary.setStyleSheet(f"color: {theme.FAIL};")
        self.sync_btn.setEnabled(ok)
        self.sync_btn.setToolTip(tip or ("" if ok else "Compare first"))

    # ---------- compare ----------
    def run_compare(self) -> None:
        if problems := self.problems():
            return self.app.error("\n".join(problems))
        self.app.save_settings()
        pairs = self.app.pairs()
        method = Method(self.method.currentText())
        extra = parse_excludes(self.excludes.text())
        self.cancel_flag.clear()
        self.progress.setRange(0, 0)  # busy indicator
        self._set_busy(True, "Scanning folders…")
        cancelled = self.cancel_flag.is_set

        def work(progress):
            return [compare(src, dst, method, extra, lambda _m, n: progress(n, 0), cancelled, pair=i)
                    for i, (src, dst) in enumerate(pairs)]

        def done(comparisons: list[Comparison]):
            self._set_busy(False)
            self.comparisons = comparisons
            for i, cmp in enumerate(comparisons, start=1):
                if cmp.right_missing:
                    self.log.appendPlainText(f"Pair {i}: destination doesn't exist yet, it will be created: {cmp.right_root}")
            self.replan(reset_overrides=True)
            n = sum(len(c.rows) for c in comparisons)
            self.log.appendPlainText(f"Compared {n} files in {len(comparisons)} folder pair(s)")

        def failed(msg):
            self._set_busy(False)
            if msg == "Cancelled":
                self.mark_stale("Compare cancelled.")
            else:
                self.app.error(f"Compare failed: {msg}")

        run_task(work, done, failed, lambda n, _t: self.progress_text.setText(f"Scanning… {n} files"))

    # ---------- manual actions ----------
    def _menu(self, pos) -> None:
        sel = sorted({i.row() for i in self.view.selectionModel().selectedRows()})
        rows = [r for i in sel if (r := self.model.row_at(i))]
        if not rows:
            return
        menu = QMenu(self)
        labels = {
            Action.TO_RIGHT: "Copy to destination  →",
            Action.TO_LEFT: "Copy to source  ←",
            Action.DELETE_RIGHT: "Delete from destination  ✕",
            Action.DELETE_LEFT: "Delete from source  ✕",
            Action.NONE: "Do nothing",
        }
        for act, text in labels.items():
            fits = [r for r in rows if act in possible_actions(r)]
            if fits:
                a = QAction(f"{text}   ({len(fits)})", menu)
                a.triggered.connect(lambda _=False, act=act, fits=fits: self._set_action(fits, act))
                menu.addAction(a)
        menu.addSeparator()
        reset = QAction("Back to default", menu)
        reset.triggered.connect(lambda: self._reset(rows))
        menu.addAction(reset)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _set_action(self, rows: list[Row], act: Action) -> None:
        for r in rows:
            r.action, r.overridden, r.held = act, True, False
            r.conflict = False
        self.model.refilter()
        self._refresh_summary()

    def _reset(self, rows: list[Row]) -> None:
        for r in rows:
            r.overridden = False
        self.replan()

    # ---------- sync ----------
    def _remover(self):
        choice = self.deletion.currentText()
        if choice == DEL_PERMANENT:
            return syncer.delete_permanently
        if choice == DEL_SYSTEM:
            def to_trash(root: Path, rel: str) -> None:
                ok = QFile.moveToTrash(str(root / rel))
                if isinstance(ok, tuple):
                    ok = ok[0]
                if not ok:
                    raise OSError(f"could not move {rel} to the Trash")
            return to_trash
        return syncer.TrashFolder()

    def run_sync(self) -> None:
        comparisons = self.comparisons
        problems = self.problems() if comparisons else ["Compare first."]
        if problems:
            return self.app.error("\n".join(problems))
        all_rows = self.all_rows()
        acting = [r for r in all_rows if r.action != Action.NONE]
        to_r = [r for r in acting if r.action == Action.TO_RIGHT]
        to_l = [r for r in acting if r.action == Action.TO_LEFT]
        dels = [r for r in acting if r.action in (Action.DELETE_RIGHT, Action.DELETE_LEFT)]
        lines = [f"{len(comparisons)} folder pair(s)",
                 f"Copy {len(to_r)} files to the destination ({human(syncer.total_bytes(to_r))})"]
        if to_l:
            lines.append(f"Copy {len(to_l)} files back to the source ({human(syncer.total_bytes(to_l))})")
        if dels:
            lines.append(f"Remove {len(dels)} files  →  {self.deletion.currentText().lower()}")
        held = [r for r in all_rows if r.held]
        if held:
            lines.append(f"Hold back {len(held)} files that failed the codec check")
        if QMessageBox.question(self, "pxlsync", "\n".join(lines) + "\n\nStart sync?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.app.save_settings()
        self.cancel_flag.clear()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self._set_busy(True, "Syncing…")
        remover = self._remover()
        prune = Mode(self.mode.currentText()) == Mode.MIRROR
        cancelled = self.cancel_flag.is_set
        self._last = ""

        grand_total = max(syncer.total_bytes(acting), 1)

        def work(progress):
            total = syncer.SyncStats()
            for i, cmp in enumerate(comparisons, start=1):
                before = total.bytes

                def say(done, _t, msg, before=before, i=i):
                    self._last = f"Pair {i}: {msg}" if len(comparisons) > 1 else msg
                    progress(int((before + done) * 1000 / grand_total), 1000)

                stats = syncer.run(cmp, remover, say, cancelled, prune_empty_right_dirs=prune)
                total.copied += stats.copied
                total.deleted += stats.deleted
                total.bytes += stats.bytes
                total.errors += [f"pair {i}: {e}" for e in stats.errors] if len(comparisons) > 1 else stats.errors
                if stats.cancelled:
                    total.cancelled = True
                    break
            return total

        def done(stats: syncer.SyncStats):
            self._set_busy(False)
            status = "cancelled" if stats.cancelled else "done"
            self.log.appendPlainText(
                f"■ Sync {status}: {stats.copied} copied ({human(stats.bytes)}), "
                f"{stats.deleted} removed, {len(stats.errors)} errors")
            for e in stats.errors[:50]:
                self.log.appendPlainText(f"  ✕ {e}")
            self.run_compare()  # show the result, like FreeFileSync does

        def failed(msg):
            self._set_busy(False)
            self.app.error(f"Sync stopped: {msg}")
            self.run_compare()

        def progress(d, _t):
            self.progress.setValue(d)
            self.progress_text.setText(self._last)

        run_task(work, done, failed, progress)

    # ---------- verify ----------
    def verify(self) -> None:
        """Probe the media copies in the destination and compare codecs with the source."""
        sess = self.app.session
        if not sess.files or not self.app.scan_current():
            return self.app.error("Run the codec check (01 CHECK) on these source folders first.")
        if not self.app.ffprobe:
            return self.app.error("ffprobe is not installed.")
        skip_failing = self.policy.currentText() != POLICY_WARN
        pairs = []  # (source file, its copy in the destination), for every folder pair
        for src, dst in self.app.pairs():
            if not src or not dst:
                continue
            for p in sess.files:
                if p.is_relative_to(src) and not (skip_failing and (r := sess.results.get(p)) and not r.ok):
                    pairs.append((p, dst / p.relative_to(src)))
        present = [(s, d) for s, d in pairs if d.exists()]
        missing = [d for _, d in pairs if not d.exists()]
        self.log.appendPlainText(f"Verifying {len(present)} media files in the destination…")
        self.verify_btn.setEnabled(False)
        ffprobe = self.app.ffprobe

        def done(infos):
            self.verify_btn.setEnabled(True)
            bad = 0
            keys = ("video_codec", "video_profile", "width", "height", "audio_codec")
            for (s, d), di in zip(present, infos):
                si = sess.infos.get(s)
                if di.error or (si and any(getattr(si, k) != getattr(di, k) for k in keys)):
                    bad += 1
                    self.log.appendPlainText(f"  ✕ {d}: {di.error or 'codec differs from source'}")
            for d in missing:
                self.log.appendPlainText(f"  ✕ missing: {d}")
            problems = bad + len(missing)
            self.log.appendPlainText(f"■ Verify: {len(present) - bad} OK, {problems} problems" if problems
                                     else f"■ Verify: all {len(present)} media files match the source")

        def failed(msg):
            self.verify_btn.setEnabled(True)
            self.log.appendPlainText(f"Verify failed: {msg}")

        run_task(lambda p: probe_many([d for _, d in present], ffprobe, p), done, failed)

    def save(self, s: QSettings) -> None:
        s.setValue("cmp_method", self.method.currentText())
        s.setValue("sync_mode", self.mode.currentText())
        s.setValue("sync_policy", self.policy.currentText())
        s.setValue("sync_deletion", self.deletion.currentText())
        s.setValue("sync_excludes", self.excludes.text())
