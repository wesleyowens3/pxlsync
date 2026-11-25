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
        self.rows: list[Row] = []
        self.visible: list[Row] = []
        self.shown = {k for k, *_ in BUCKETS} - {B_SAME}
        self.codec: dict[str, bool] = {}  # rel.casefold() -> passed?
        self.bold = QFont()
        self.bold.setBold(True)

    def set_rows(self, rows: list[Row]) -> None:
        self.rows = rows
        self.refilter()

    def refilter(self) -> None:
        self.beginResetModel()
        self.visible = [r for r in self.rows if bucket(r) in self.shown]
        self.endResetModel()

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
        r = self.visible[index.row()]
        col = index.column()
        b = bucket(r)
        side = r.left if col < self.MID else r.right
        if role == Qt.ItemDataRole.DisplayRole:
            if col == self.MID:
                if b == B_LEFT:
                    return "←  UPDATE" if r.left else "←  NEW"
                return ACTION_TEXT[b][0]
            if col == 3:
                ok = self.codec.get(r.left.rel.casefold()) if r.left else None
                return "" if ok is None else ("PASS" if ok else "FAIL")
            if side is None:
                return ""
            c = col if col < self.MID else col - 5
            return [side.rel, human(side.size), when(side.mtime)][c]
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == self.MID:
                return QColor(theme.LEFT if b == B_LEFT else ACTION_TEXT[b][1])
            if col == 3:
                ok = self.codec.get(r.left.rel.casefold()) if r.left else None
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
        s = app.settings
        self.comparison: Comparison | None = None
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
        self.log.setMaximumHeight(64)
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

    def mark_stale(self, message: str) -> None:
        self.comparison = None
        self.model.set_rows([])
        self.summary.setText(message.upper())
        self.progress_text.setText("") if hasattr(self, "progress_text") else None
        self.summary.setStyleSheet(f"color: {theme.MUTED};")
        self._update_chips()
        self.update_sync_button()

    def on_show(self) -> None:
        self.update_sync_button()

    def codec_changed(self) -> None:
        if self.comparison:
            self.replan()

    def _toggle(self, key: str, on: bool) -> None:
        (self.model.shown.add if on else self.model.shown.discard)(key)
        self.model.refilter()

    def _update_chips(self) -> None:
        counts = self.model.counts()
        for key, text, _ in BUCKETS:
            self.chips[key].setText(f"{text}  {counts[key]}")

    def failing_rels(self) -> set[str]:
        """Source-relative paths of files that failed the codec check (if it ran on this source)."""
        sess, src = self.app.session, self.app.source()
        if not sess.results or not src or self.app.scanned_root != src:
            return set()
        return {p.relative_to(src).as_posix() for p in sess.failing() if p.is_relative_to(src)}

    def _codec_map(self) -> dict[str, bool]:
        sess, src = self.app.session, self.app.source()
        if not sess.results or not src or self.app.scanned_root != src:
            return {}
        return {p.relative_to(src).as_posix().casefold(): r.ok for p, r in sess.results.items()
                if p.is_relative_to(src)}

    def replan(self, reset_overrides: bool = False) -> None:
        if not self.comparison:
            return
        rows = self.comparison.rows
        if reset_overrides:
            for r in rows:
                r.overridden = False
        plan(rows, Mode(self.mode.currentText()))
        if self.policy.currentText() != POLICY_WARN:
            hold(rows, self.failing_rels(), "failed the codec check")
        self.model.codec = self._codec_map()
        self.model.set_rows(rows)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        self._update_chips()
        rows = self.comparison.rows if self.comparison else []
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
        out = []
        src, dst = self.app.source(), self.app.dest()
        if not src or not src.is_dir():
            out.append("Choose a source folder that exists.")
        if not dst:
            out.append("Choose a destination folder.")
        elif src and dst.resolve() == src.resolve():
            out.append("Source and destination are the same folder.")
        elif src and (dst.resolve().is_relative_to(src.resolve()) or src.resolve().is_relative_to(dst.resolve())):
            out.append("One folder is inside the other.")
        return out

    def update_sync_button(self) -> None:
        cmp = self.comparison
        ok = bool(cmp) and not self.busy and any(r.action != Action.NONE for r in cmp.rows)
        tip = ""
        if cmp and self.policy.currentText() == POLICY_BLOCK:
            blocked = [r for r in cmp.rows if r.held]
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
        src, dst = self.app.source(), self.app.dest()
        method = Method(self.method.currentText())
        extra = parse_excludes(self.excludes.text())
        self.cancel_flag.clear()
        self.progress.setRange(0, 0)  # busy indicator
        self._set_busy(True, "Scanning folders…")
        cancelled = self.cancel_flag.is_set

        def work(progress):
            return compare(src, dst, method, extra, lambda _m, n: progress(n, 0), cancelled)

        def done(cmp: Comparison):
            self._set_busy(False)
            self.comparison = cmp
            if cmp.right_missing:
                self.log.appendPlainText(f"Destination doesn't exist yet; it will be created: {dst}")
            self.replan(reset_overrides=True)
            n = len(cmp.rows)
            self.log.appendPlainText(f"Compared {n} files  ·  {src}  ⇄  {dst}")

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
        rows = [self.model.visible[i] for i in sel]
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
        cmp = self.comparison
        problems = self.problems() if cmp else ["Compare first."]
        if problems:
            return self.app.error("\n".join(problems))
        acting = [r for r in cmp.rows if r.action != Action.NONE]
        to_r = [r for r in acting if r.action == Action.TO_RIGHT]
        to_l = [r for r in acting if r.action == Action.TO_LEFT]
        dels = [r for r in acting if r.action in (Action.DELETE_RIGHT, Action.DELETE_LEFT)]
        lines = [f"Copy {len(to_r)} files to the destination ({human(syncer.total_bytes(to_r))})"]
        if to_l:
            lines.append(f"Copy {len(to_l)} files back to the source ({human(syncer.total_bytes(to_l))})")
        if dels:
            lines.append(f"Remove {len(dels)} files  →  {self.deletion.currentText().lower()}")
        held = [r for r in cmp.rows if r.held]
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

        def work(progress):
            def say(done, total, msg):
                self._last = msg
                progress(int(done * 1000 / max(total, 1)), 1000)
            return syncer.run(cmp, remover, say, cancelled, prune_empty_right_dirs=prune)

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
        sess, src, dst = self.app.session, self.app.scanned_root, self.app.dest()
        if not sess.files or not dst or src != self.app.source():
            return self.app.error("Run the codec check (01 CHECK) on this source first.")
        if not self.app.ffprobe:
            return self.app.error("ffprobe is not installed.")
        held = {r.casefold() for r in self.failing_rels()} if self.policy.currentText() != POLICY_WARN else set()
        pairs = [(p, dst / p.relative_to(src)) for p in sess.files
                 if p.relative_to(src).as_posix().casefold() not in held]
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
