"""The compare grid: one row per file, source on the left, destination on the right."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor, QFont

from .. import syncer
from ..compare import Action, Comparison, Row
from . import theme

# show/hide buckets for the filter chips
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
ACTION_TEXT = {
    B_NEW_R: ("→  NEW", theme.PASS),
    B_UPD_R: ("→  UPDATE", theme.TEXT),
    B_DEL: ("✕  DELETE", theme.FAIL),
    B_CONFLICT: ("!  CONFLICT", theme.WARN),
    B_HELD: ("■  HELD", theme.WARN),
    B_SAME: ("=", theme.MUTED),
}
CODEC_NAMES = {"prores": "ProRes", "dnxhd": "DNx", "h264": "H.264", "hevc": "HEVC", "av1": "AV1",
               "vp9": "VP9", "cfhd": "CineForm", "qtrle": "Animation", "aac": "AAC", "mp3": "MP3"}

C_SRC, C_SSIZE, C_SDATE, C_CODEC, C_FORMAT, C_NEW, C_ACT, C_DST, C_DSIZE, C_DDATE = range(10)
HEADERS = ["SOURCE", "SIZE", "MODIFIED", "CODEC", "FORMAT", "NEW NAME", "ACTION", "DESTINATION", "SIZE", "MODIFIED"]


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


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


def when(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d  %H:%M")


def format_text(info) -> str:
    if info is None or info.error:
        return ""
    parts = []
    if info.video_codec:
        name = CODEC_NAMES.get(info.video_codec, info.video_codec)
        prof = (info.video_profile or "").replace("DNXHR ", "HR ").replace("DNXHD", "HD")
        parts.append(f"{name} {prof}".strip())
        if info.resolution:
            parts.append(info.resolution.replace("x", "×"))
        if info.fps_text:
            parts.append(info.fps_text)
    elif info.audio_codec:
        parts.append(CODEC_NAMES.get(info.audio_codec, info.audio_codec))
        if info.sample_rate:
            parts.append(f"{info.sample_rate / 1000:g} kHz")
    return " · ".join(parts)


class CompareModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.comparisons: list[Comparison] = []
        self.rows: list[Row] = []
        self.visible: list[Row | int] = []  # an int is the header line of that folder pair
        self.shown = {k for k, *_ in BUCKETS} - {B_SAME}
        self.bold = QFont()
        self.bold.setBold(True)

    def set_comparisons(self, comparisons: list[Comparison]) -> None:
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

    def counts(self) -> dict[str, int]:
        out = {k: 0 for k, *_ in BUCKETS}
        for r in self.rows:
            out[bucket(r)] += 1
        return out

    def _header_text(self, i: int) -> str:
        cmp = self.comparisons[i]
        acting = [r for r in cmp.rows if r.action != Action.NONE]
        copies = [r for r in acting if r.action in (Action.TO_RIGHT, Action.TO_LEFT)]
        info = (f"{len(copies)} to copy ({human(syncer.total_bytes(copies))}) · {len(acting) - len(copies)} to remove"
                if acting else "nothing to do")
        return f"PAIR {i + 1}   {cmp.left_root.name}  →  {cmp.right_root.name}     ·  {info}"

    # ---- Qt model API ----
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.visible)

    def columnCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return len(HEADERS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return HEADERS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        item = self.visible[index.row()]
        col = index.column()
        if isinstance(item, int):  # folder pair header line
            if role == Qt.ItemDataRole.DisplayRole and col == 0:
                return self._header_text(item)
            if role == Qt.ItemDataRole.ToolTipRole:
                cmp = self.comparisons[item]
                return f"{cmp.left_root}\n→ {cmp.right_root}"
            if role == Qt.ItemDataRole.TextAlignmentRole:
                return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            if role == Qt.ItemDataRole.FontRole:
                return self.bold
            if role == Qt.ItemDataRole.ForegroundRole:
                return QColor(theme.TEXT)
            if role == Qt.ItemDataRole.BackgroundRole:
                return QColor(theme.BOX)
            return None

        r, b = item, bucket(item)
        res = r.meta.get("codec")  # CheckResult or None
        info = r.meta.get("info")
        problem = r.meta.get("problem", "")
        if role == Qt.ItemDataRole.DisplayRole:
            if col == C_ACT:
                if b == B_LEFT:
                    return "←  UPDATE" if r.left else "←  NEW"
                return ACTION_TEXT[b][0]
            if col == C_CODEC:
                return "" if res is None else ("PASS" if res.ok else "FAIL")
            if col == C_FORMAT:
                return format_text(info)
            if col == C_NEW:
                if problem:
                    return f"! {problem}"
                return r.left.rel if r.left and r.left.orig else ""
            side = r.left if col < C_ACT else r.right
            if side is None:
                return ""
            if col in (C_SRC, C_DST):
                return side.actual if col == C_SRC else side.rel
            if col in (C_SSIZE, C_DSIZE):
                return human(side.size)
            return when(side.mtime)
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == C_ACT:
                return QColor(theme.LEFT if b == B_LEFT else ACTION_TEXT[b][1])
            if col == C_CODEC:
                return QColor(theme.MUTED if res is None else theme.PASS if res.ok else theme.FAIL)
            if col == C_NEW:
                return QColor(theme.FAIL if problem else theme.TEXT)
            if col == C_FORMAT:
                return QColor(theme.MUTED)
            if b == B_SAME:
                return QColor(theme.MUTED)
            if (col < C_ACT and r.action == Action.DELETE_LEFT) or (col > C_ACT and r.action == Action.DELETE_RIGHT):
                return QColor(theme.FAIL)
            if (col < C_ACT and r.action == Action.TO_RIGHT) or (col > C_ACT and r.action == Action.TO_LEFT):
                return QColor(theme.TEXT)
            return QColor(theme.MUTED)
        if role == Qt.ItemDataRole.FontRole and col in (C_ACT, C_CODEC):
            return self.bold
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if col in (C_ACT, C_CODEC):
                return Qt.AlignmentFlag.AlignCenter
            if col in (C_SSIZE, C_DSIZE):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.ToolTipRole:
            tip = [r.left.actual if r.left else r.rel]
            if r.left and r.left.orig:
                tip.append(f"will be renamed to {r.left.rel}")
            tip.append(f"compare: {r.category.value}")
            if res is not None and not res.ok:
                tip += [f"codec: {x}" for x in res.reasons]
            if problem:
                tip.append(f"not renamed: {problem}")
            if r.note:
                tip.append(r.note)
            if r.overridden:
                tip.append("action set by hand")
            return "\n".join(tip)
        if role == Qt.ItemDataRole.BackgroundRole and col == C_ACT:
            return QColor(theme.BOX)
        return None
