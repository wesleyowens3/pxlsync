"""pxlsync main window: one screen, FreeFileSync-style.

Folder pairs on top, one toolbar (codec check, rename, organize, sync mode), and a
single compare grid. COMPARE scans codecs, plans the new names/folders and compares
against the destinations; SYNCHRONIZE applies the renames in the source and copies.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from PySide6.QtCore import QFile, QSettings, QStandardPaths, Qt
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTableView, QVBoxLayout, QWidget,
)

from .. import __version__, fileops, syncer
from ..check import CheckRule, check
from ..codecs import ANY_LABEL, load_presets
from ..compare import (
    MODE_HELP, Action, Category, Comparison, Method, Mode, Row, compare, hold, parse_excludes, plan,
    possible_actions,
)
from ..organize import DEFAULT_RULES, OrganizeRule
from ..pipeline import names_for_root, plan_source_moves
from ..probe import MediaInfo, find_ffprobe, probe_many, scan_media
from ..rename import existing_names
from . import theme
from .dialogs import DEL_FOLDER, DEL_PERMANENT, DEL_SYSTEM, OptionsDialog, OrganizeDialog
from .grid import B_SAME, BUCKETS, C_CODEC, C_DST, C_FORMAT, C_NEW, C_SRC, CompareModel, human
from .pairs import FolderPairs
from .widgets import button, hrule, label, row, run_task, section, vrule

POLICY_BLOCK = "Block sync if any fail"
POLICY_SKIP = "Hold back failing"
POLICY_WARN = "Sync failing anyway"

RENAME_PRESETS = [
    "{project}_{name}_v{v:3}",
    "{client}_{project}_{name}_{date}_v{v:3}",
    "{project}_{name}_{codec:upper}_{res}_v{v:3}",
    "{project}_{seq:4}_v{v:2}",
]
TOKENS = [
    ("{project}", "Project field"), ("{client}", "Client field"), ("{name}", "Original name"),
    ("{v:3}", "Version, 3 digits (continues from existing files)"), ("{seq:3}", "Counter"),
    ("{date}", "Today, YYYYMMDD"), ("{date:%Y-%m-%d}", "Today, YYYY-MM-DD"), ("{mdate}", "File's modified date"),
    ("{codec}", "Codec"), ("{profile}", "Codec profile"), ("{res}", "Resolution"), ("{fps}", "Frame rate"),
    ("{kind}", "video / audio / other"), ("{folder}", "Parent folder name"),
]


def config_dir() -> Path:
    d = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    d.mkdir(parents=True, exist_ok=True)
    return d


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("PXLBOX", "pxlsync")
        self.video_presets, self.audio_presets = load_presets(config_dir() / "codec_presets.json")
        self.ffprobe = find_ffprobe()
        self.probe_cache: dict[Path, tuple[int, float, MediaInfo]] = {}
        self.infos: dict[Path, MediaInfo] = {}
        self.results: dict = {}
        self.source_moves: list[fileops.Move] = []
        self.comparisons: list[Comparison] = []
        self.cancel_flag = threading.Event()
        self.busy = False
        self._last = ""
        self._load_options()

        self.setWindowTitle("pxlsync")
        self.setWindowIcon(QIcon(str(theme.ASSETS / "icon.png")))
        self.resize(1440, 900)
        self.setMinimumSize(1100, 680)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())
        outer.addWidget(hrule())
        outer.addWidget(self._pairs())
        outer.addWidget(hrule())
        body = QVBoxLayout()
        body.setContentsMargins(22, 14, 22, 12)
        body.setSpacing(10)
        body.addLayout(self._toolbar())
        body.addWidget(hrule())
        body.addLayout(self._chips())
        body.addWidget(self._grid(), 1)
        body.addLayout(self._status_row())
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Log")
        self.log.setMaximumHeight(56)
        body.addWidget(self.log)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)

        self.tool_status = QLabel(
            f"ffprobe: {'OK' if self.ffprobe else 'NOT FOUND, codec check off (brew install ffmpeg)'}")
        self.statusBar().addPermanentWidget(self.tool_status)
        self.statusBar().setSizeGripEnabled(False)
        self._update_columns()
        self._set_busy(False)
        self.mark_stale("Press COMPARE to see what will happen.")

    # ================= layout =================
    def _header(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(22, 14, 22, 14)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(theme.ASSETS / "logo.png")).scaledToHeight(
            34, Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(logo)
        lay.addSpacing(16)
        lay.addWidget(vrule())
        lay.addSpacing(16)
        lay.addWidget(label("PXLSYNC", "appname"))
        lay.addSpacing(10)
        lay.addWidget(label(f"v{__version__}", "tag"), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        self.project_edit = QLineEdit(self.settings.value("project", ""))
        self.project_edit.setPlaceholderText("e.g. PXL0142")
        self.client_edit = QLineEdit(self.settings.value("client", ""))
        self.client_edit.setPlaceholderText("e.g. ACME")
        for title, e in (("Project", self.project_edit), ("Client", self.client_edit)):
            e.setFixedWidth(160)
            e.editingFinished.connect(lambda e=e: self._edited(e, "Project / client changed"))
            lay.addWidget(section(title))
            lay.addWidget(e)
            lay.addSpacing(12)
        return w

    def _pairs(self) -> QWidget:
        saved = self.settings.value("pairs", "")
        try:
            pairs = [tuple(p) for p in json.loads(saved)] if saved else []
        except ValueError:
            pairs = []
        if not pairs:  # settings from before folder pairs existed
            pairs = [(self.settings.value("source", ""), self.settings.value("dest", ""))]
        self.pairs_panel = FolderPairs(pairs)
        self.pairs_panel.changed.connect(lambda: self.settings_changed("Folders changed"))
        return self.pairs_panel

    def _toolbar(self) -> QGridLayout:
        s = self.settings
        g = QGridLayout()
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)

        self.compare_btn = button("Compare", "primary", self.run_compare)
        self.compare_btn.setToolTip("Check codecs, plan new names and folders, and compare every folder pair.\n"
                                    "Nothing changes until you press SYNCHRONIZE.")
        self.sync_btn = button("Synchronize", "primary", self.run_sync)
        for b in (self.compare_btn, self.sync_btn):
            b.setMinimumWidth(160)
            b.setSizePolicy(b.sizePolicy().horizontalPolicy(), b.sizePolicy().Policy.Expanding)

        # codec check
        self.video_combo = QComboBox()
        self.video_combo.addItems([p.label for p in self.video_presets])
        self.video_combo.setCurrentText(s.value("video_preset", ANY_LABEL))
        self.video_combo.setToolTip("Video codec every file must use")
        self.audio_combo = QComboBox()
        self.audio_combo.addItems([p.label for p in self.audio_presets])
        self.audio_combo.setCurrentText(s.value("audio_preset", ANY_LABEL))
        self.audio_combo.setToolTip("Audio codec every file must use")
        # sync mode
        self.mode = QComboBox()
        self.mode.addItems([m.value for m in Mode])
        for i, m in enumerate(Mode):
            self.mode.setItemData(i, MODE_HELP[m], Qt.ItemDataRole.ToolTipRole)
        self.mode.setCurrentText(s.value("sync_mode", Mode.MIRROR.value))
        if self.mode.currentIndex() < 0:
            self.mode.setCurrentIndex(0)
        self.policy = QComboBox()
        self.policy.addItems([POLICY_BLOCK, POLICY_SKIP, POLICY_WARN])
        self.policy.setCurrentText(s.value("sync_policy", POLICY_BLOCK))
        self.policy.setToolTip("What happens to files that fail the codec check")
        for c in (self.video_combo, self.audio_combo, self.mode, self.policy):
            c.setMinimumWidth(190)

        # rename
        self.rename_chk = QCheckBox("RENAME")
        self.rename_chk.setChecked(s.value("rename_on", False, type=bool))
        self.rename_chk.setToolTip("Rename files to the template in the source when you synchronize")
        self.template = QComboBox()
        self.template.setEditable(True)
        self.template.setMinimumWidth(220)
        self.template.addItems(RENAME_PRESETS)
        self.template.setCurrentText(s.value("rename_template", RENAME_PRESETS[0]))
        self.template.lineEdit().editingFinished.connect(
            lambda: self._edited(self.template.lineEdit(), "Rename template changed"))
        self.template.activated.connect(lambda _=None: self.settings_changed("Rename template changed"))
        tokens = QPushButton("+ TOKEN")
        tokens.setObjectName("ghost")
        menu = QMenu(tokens)
        for tok, desc in TOKENS:
            act = QAction(f"{tok:<18} {desc}", menu)
            act.triggered.connect(lambda _=False, t=tok: self._insert_token(t))
            menu.addAction(act)
        tokens.setMenu(menu)
        self.rename_chk.toggled.connect(lambda _=None: self.settings_changed("Rename turned on/off"))
        # organize
        self.organize_chk = QCheckBox("ORGANIZE")
        self.organize_chk.setChecked(s.value("organize_on", False, type=bool))
        self.organize_chk.setToolTip("Sort files into folders in the source when you synchronize")
        self.organize_chk.toggled.connect(lambda _=None: self.settings_changed("Organize turned on/off"))
        saved = s.value("org_rules", "")
        try:
            self.rules = [OrganizeRule(**r) for r in json.loads(saved)] if saved else list(DEFAULT_RULES)
        except (ValueError, TypeError):
            self.rules = list(DEFAULT_RULES)
        # Earlier versions saved default rules that used {date} (today): switch those to {mdate}.
        if [(r.pattern, r.folder, r.codec) for r in self.rules] == [
                (r.pattern, r.folder.replace("{mdate", "{date"), r.codec) for r in DEFAULT_RULES]:
            self.rules = list(DEFAULT_RULES)
        self.rules_label = label("", "hint")
        rules_btn = button("Edit rules", "ghost", self.edit_rules)
        options_btn = button("⚙ Options", "ghost", self.edit_options)

        g.addWidget(self.compare_btn, 0, 0, 2, 1)
        g.addWidget(section("Codec"), 0, 1)
        g.addWidget(self.video_combo, 0, 2)
        g.addWidget(self.audio_combo, 0, 3)
        g.addWidget(section("Sync"), 1, 1)
        g.addWidget(self.mode, 1, 2)
        g.addWidget(self.policy, 1, 3)
        g.addWidget(vrule(), 0, 4, 2, 1)
        g.addWidget(self.rename_chk, 0, 5)
        g.addWidget(self.template, 0, 6)
        g.addWidget(tokens, 0, 7)
        g.addWidget(self.organize_chk, 1, 5)
        g.addWidget(self.rules_label, 1, 6)
        g.addWidget(rules_btn, 1, 7)
        g.addWidget(options_btn, 0, 8, 2, 1)
        g.addWidget(self.sync_btn, 0, 9, 2, 1)
        g.setColumnStretch(6, 1)

        self.video_combo.currentTextChanged.connect(lambda _=None: self.codec_changed())
        self.audio_combo.currentTextChanged.connect(lambda _=None: self.codec_changed())
        self.policy.currentTextChanged.connect(lambda _=None: self.codec_changed())
        self.mode.currentTextChanged.connect(lambda _=None: (self.save_settings(), self.replan(reset_overrides=True)))
        self._update_rules_label()
        self.toolbar_widgets = [self.video_combo, self.audio_combo, self.mode, self.policy, self.rename_chk,
                                self.template, tokens, self.organize_chk, rules_btn, options_btn]
        return g

    def _chips(self) -> QHBoxLayout:
        self.chips: dict[str, QPushButton] = {}
        items: list = [section("Show")]
        for key, text, color in BUCKETS:
            c = button(text, "filter")
            c.setCheckable(True)
            c.setChecked(key != B_SAME)
            c.setStyleSheet(f"QPushButton:checked {{ color: {color}; border-color: {color}; }}")
            c.toggled.connect(lambda on, k=key: self._toggle_bucket(k, on))
            self.chips[key] = c
            items.append(c)
        items.append(None)
        self.mode.currentTextChanged.connect(self._update_mode_hint)
        self._update_mode_hint()
        return row(*items, spacing=6)

    def _grid(self) -> QTableView:
        self.model = CompareModel()
        v = self.view = QTableView()
        v.setModel(self.model)
        self.model.modelReset.connect(self._apply_spans)
        v.verticalHeader().setVisible(False)
        v.verticalHeader().setDefaultSectionSize(26)
        v.setShowGrid(False)
        v.setAlternatingRowColors(True)
        v.setWordWrap(False)
        v.setTextElideMode(Qt.TextElideMode.ElideLeft)  # long paths keep their file name visible
        v.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        v.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        v.customContextMenuRequested.connect(self._menu)
        hdr = v.horizontalHeader()
        hdr.setHighlightSections(False)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for col in range(self.model.columnCount()):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for col in (C_SRC, C_NEW, C_DST):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        return v

    def _status_row(self) -> QHBoxLayout:
        self.summary = label("", "summary")
        self.progress_text = label("", "hint")
        self.progress = QProgressBar()
        self.progress.setFixedWidth(240)
        self.cancel_btn = button("Cancel", "ghost", self.cancel_flag.set)
        self.undo_btn = button("Undo renames", "ghost", self.undo_renames)
        self.undo_btn.setToolTip("Put the files renamed/moved by the last sync back where they were")
        self.verify_btn = button("Verify destination", "ghost", self.verify)
        self.verify_btn.setToolTip("Re-read the copies in the destination and check their codecs match the source")
        return row(self.summary, self.progress_text, None, self.progress, self.cancel_btn,
                   self.undo_btn, self.verify_btn)

    # ================= small helpers =================
    def _load_options(self) -> None:
        s = self.settings
        self.options = {
            "method": s.value("cmp_method", Method.TIME_SIZE.value),
            "deletion": s.value("sync_deletion", DEL_FOLDER),
            "excludes": s.value("sync_excludes", ""),
            "require_audio": s.value("require_audio", False, type=bool),
            "strip_version": s.value("rename_strip", True, type=bool),
            "lowercase_ext": s.value("rename_lower_ext", False, type=bool),
        }

    def _insert_token(self, token: str) -> None:
        self.template.lineEdit().insert(token)
        self.template.setFocus()
        self.settings_changed("Rename template changed")

    def _edited(self, edit: QLineEdit, why: str) -> None:
        if edit.isModified():
            edit.setModified(False)
            self.settings_changed(why)

    def _update_mode_hint(self, *_):
        self.mode.setToolTip(MODE_HELP[Mode(self.mode.currentText())])

    def _update_rules_label(self) -> None:
        n = len(self.rules)
        first = self.rules[0].folder if self.rules else ""
        self.rules_label.setText(f"{n} rule{'s' if n != 1 else ''}" + (f"  ·  {first} …" if first else ""))

    def _update_columns(self) -> None:
        self.view.setColumnHidden(C_NEW, not (self.rename_chk.isChecked() or self.organize_chk.isChecked()))
        self.view.setColumnHidden(C_CODEC, not self.ffprobe)
        self.view.setColumnHidden(C_FORMAT, not self.ffprobe)

    def _toggle_bucket(self, key: str, on: bool) -> None:
        (self.model.shown.add if on else self.model.shown.discard)(key)
        self.model.refilter()

    def _apply_spans(self) -> None:
        self.view.clearSpans()
        for n in self.model.header_rows():
            self.view.setSpan(n, 0, 1, self.model.columnCount())

    def error(self, text: str) -> None:
        QMessageBox.warning(self, "pxlsync", text)

    def journal_dir(self) -> Path:
        return config_dir() / "journals"

    def fields(self) -> dict[str, str]:
        return {"project": self.project_edit.text().strip(), "client": self.client_edit.text().strip()}

    def pairs(self) -> list[tuple[Path | None, Path | None]]:
        return [(Path(left) if left else None, Path(right) if right else None)
                for left, right in self.pairs_panel.pairs() if left or right]

    def all_rows(self) -> list[Row]:
        return [r for c in self.comparisons for r in c.rows]

    def check_rule(self) -> CheckRule:
        v = next(p for p in self.video_presets if p.label == self.video_combo.currentText())
        a = next(p for p in self.audio_presets if p.label == self.audio_combo.currentText())
        return CheckRule(video=v, audio=a, require_audio=self.options["require_audio"])

    # ================= state changes =================
    def settings_changed(self, why: str) -> None:
        self._update_columns()
        self.save_settings()
        if self.comparisons:
            self.mark_stale(f"{why}: press COMPARE again.")

    def codec_changed(self) -> None:
        self.save_settings()
        if self.comparisons and not self.busy:
            self.run_compare()  # codec scans are cached, so this is quick

    def mark_stale(self, message: str) -> None:
        self.comparisons = []
        self.source_moves = []
        self.model.set_comparisons([])
        self.summary.setText(message.upper())
        self.summary.setStyleSheet(f"color: {theme.MUTED};")
        self.progress_text.setText("")
        self._update_chips()
        self._update_sync_button()

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        self.progress.setVisible(busy)
        self.cancel_btn.setVisible(busy)
        self.progress_text.setText(text)
        self.compare_btn.setEnabled(not busy)
        self.pairs_panel.setEnabled(not busy)
        for w in self.toolbar_widgets + [self.project_edit, self.client_edit, self.undo_btn, self.verify_btn]:
            w.setEnabled(not busy)
        self._update_sync_button()

    def _update_chips(self) -> None:
        counts = self.model.counts()
        for key, text, _ in BUCKETS:
            self.chips[key].setText(f"{text}  {counts[key]}")

    def _update_sync_button(self) -> None:
        rows = self.all_rows()
        renames = [m for m in self.source_moves if m.changes and not m.problem]
        ok = not self.busy and (any(r.action != Action.NONE for r in rows) or bool(renames))
        tip = "" if ok else "Compare first"
        if rows and self.policy.currentText() == POLICY_BLOCK:
            blocked = sum(r.held for r in rows)
            if blocked:
                ok = False
                tip = f"{blocked} files failed the codec check. Fix them or change what happens to failing files."
                self.summary.setText(f"BLOCKED  ·  {blocked} FILES FAILED THE CODEC CHECK")
                self.summary.setStyleSheet(f"color: {theme.FAIL};")
        self.sync_btn.setEnabled(ok)
        self.sync_btn.setToolTip(tip)

    # ================= dialogs =================
    def edit_rules(self) -> None:
        labels = [p.label for p in self.video_presets + self.audio_presets if not p.is_any]
        dlg = OrganizeDialog(self, self.rules, labels)
        if dlg.exec():
            self.rules = dlg.rules()
            self._update_rules_label()
            if self.rules and not self.organize_chk.isChecked():
                self.organize_chk.setChecked(True)  # editing rules means you want them
            self.settings_changed("Organize rules changed")

    def edit_options(self) -> None:
        dlg = OptionsDialog(self, self.options)
        if dlg.exec():
            self.options = dlg.values()
            self.settings_changed("Options changed")

    # ================= compare =================
    def problems(self) -> list[str]:
        pairs = self.pairs()
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
        dests = [(n, d.resolve()) for n, (_, d) in enumerate(pairs, start=1) if d]
        for i, (n1, d1) in enumerate(dests):
            for n2, d2 in dests[i + 1:]:
                if d1 == d2 or d1.is_relative_to(d2) or d2.is_relative_to(d1):
                    out.append(f"Pairs {n1} and {n2} write to the same destination.")
            for n2, (s2, _) in enumerate(pairs, start=1):
                if s2 and n2 != n1 and (d1 == s2.resolve() or d1.is_relative_to(s2.resolve())):
                    out.append(f"Pair {n1}'s destination is inside pair {n2}'s source.")
        return out

    def run_compare(self) -> None:
        if problems := self.problems():
            return self.error("\n".join(problems))
        self.save_settings()
        pairs = self.pairs()
        sources = list(dict.fromkeys(src for src, _ in pairs))
        dests = [d for _, d in pairs]
        ffprobe = self.ffprobe
        rule = self.check_rule()
        hold_failing = self.policy.currentText() != POLICY_WARN
        template = self.template.currentText().strip() if self.rename_chk.isChecked() else None
        rules = list(self.rules) if self.organize_chk.isChecked() else None
        fields = self.fields()
        presets = self.video_presets + self.audio_presets
        method = Method(self.options["method"])
        excludes = parse_excludes(self.options["excludes"])
        strip, lower = self.options["strip_version"], self.options["lowercase_ext"]
        cache = dict(self.probe_cache)
        cancelled = self.cancel_flag.is_set
        self.cancel_flag.clear()
        self.progress.setRange(0, 0)
        self._set_busy(True, "Scanning…")

        def root_of(p: Path) -> Path | None:
            best = None
            for r in sources:
                if p.is_relative_to(r) and (best is None or len(r.parts) > len(best.parts)):
                    best = r
            return best

        def work(progress):
            self._last = "Finding media files…"
            progress(0, 0)
            files: list[Path] = []
            seen: set[Path] = set()
            for root in sources:
                for f in scan_media(root):
                    if f not in seen:
                        seen.add(f)
                        files.append(f)
            infos: dict[Path, MediaInfo] = {}
            todo = []
            for f in files:
                try:
                    st = f.stat()
                except OSError:
                    continue
                hit = cache.get(f)
                if hit and hit[0] == st.st_size and hit[1] == st.st_mtime:
                    infos[f] = hit[2]
                else:
                    todo.append((f, st))
            if ffprobe and todo:
                def p(d, t):
                    self._last = f"Checking codecs… {d}/{t}"
                    progress(d, t)
                for (f, st), info in zip(todo, probe_many([f for f, _ in todo], ffprobe, p)):
                    infos[f] = info
                    cache[f] = (st.st_size, st.st_mtime, info)
            results = {f: check(i, rule) for f, i in infos.items()} if ffprobe else {}
            failing = {f for f, r in results.items() if not r.ok}

            moves: list[fileops.Move] = []
            if template or rules:
                self._last = "Planning names and folders…"
                progress(0, 0)
                planned = [f for f in files if not (hold_failing and f in failing)]  # failing files keep their names
                known = existing_names(*sources, *dests) if template else None
                moves = plan_source_moves(planned, root_of, infos, fields, template=template, strip_version=strip,
                                          lowercase_ext=lower, known_names=known, rules=rules, presets=presets)
            comparisons = []
            for i, (src, dst) in enumerate(pairs):
                self._last = f"Comparing pair {i + 1}…" if len(pairs) > 1 else "Comparing…"
                progress(0, 0)
                comparisons.append(compare(src, dst, method, excludes, None, cancelled, pair=i,
                                           left_names=names_for_root(moves, src)))
            return infos, results, cache, moves, comparisons

        def done(result):
            self.infos, self.results, self.probe_cache, self.source_moves, self.comparisons = result
            self._set_busy(False)
            for i, cmp in enumerate(self.comparisons, start=1):
                if cmp.right_missing:
                    self.log.appendPlainText(f"Pair {i}: destination doesn't exist yet, it will be created: "
                                             f"{cmp.right_root}")
            self.replan(reset_overrides=True)
            n = sum(len(c.rows) for c in self.comparisons)
            self.log.appendPlainText(f"Compared {n} files in {len(self.comparisons)} folder pair(s)")

        def failed(msg):
            self._set_busy(False)
            self.mark_stale("Compare cancelled." if msg == "Cancelled" else "Compare failed.")
            if msg != "Cancelled":
                self.error(f"Compare failed: {msg}")

        def progress(d, t):
            if t:
                self.progress.setRange(0, t)
                self.progress.setValue(d)
            else:
                self.progress.setRange(0, 0)
            self.progress_text.setText(self._last)

        run_task(work, done, failed, progress)

    def replan(self, reset_overrides: bool = False) -> None:
        """Default actions for the sync mode, codec info per row, and holds for failing files."""
        if not self.comparisons:
            return
        mode = Mode(self.mode.currentText())
        problems = {m.src: m.problem for m in self.source_moves if m.problem}
        for cmp in self.comparisons:
            failing = set()
            for r in cmp.rows:
                if reset_overrides:
                    r.overridden = False
                r.meta.clear()
                if r.left:
                    path = cmp.left_root / r.left.actual
                    res = self.results.get(path)
                    r.meta.update(codec=res, info=self.infos.get(path), problem=problems.get(path, ""))
                    if res is not None and not res.ok:
                        failing.add(r.left.actual)
            plan(cmp.rows, mode)
            if self.policy.currentText() != POLICY_WARN:
                hold(cmp.rows, failing, "failed the codec check")
        self.model.set_comparisons(self.comparisons)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        self._update_chips()
        rows = self.all_rows()
        acting = [r for r in rows if r.action != Action.NONE]
        copies = [r for r in acting if r.action in (Action.TO_RIGHT, Action.TO_LEFT)]
        deletes = [r for r in acting if r.action in (Action.DELETE_RIGHT, Action.DELETE_LEFT)]
        renames = [m for m in self.source_moves if m.changes and not m.problem]
        held = sum(r.held for r in rows)
        conflicts = sum(r.conflict for r in rows)
        parts = []
        if copies:
            parts.append(f"{len(copies)} TO COPY ({human(syncer.total_bytes(copies))})")
        if deletes:
            parts.append(f"{len(deletes)} TO REMOVE")
        if renames:
            parts.append(f"{len(renames)} TO RENAME")
        if held:
            parts.append(f"{held} HELD BACK")
        if conflicts:
            parts.append(f"{conflicts} CONFLICTS")
        if not rows:
            text, color = "NO FILES FOUND", theme.MUTED
        elif not (copies or deletes or renames):
            text = "  ·  ".join(["NOTHING TO DO"] + parts) if parts else "IN SYNC  ·  NOTHING TO DO"
            color = theme.WARN if parts else theme.PASS
        else:
            text, color = "  ·  ".join(parts), theme.TEXT
        self.summary.setText(text)
        self.summary.setStyleSheet(f"color: {color};")
        self._update_sync_button()

    # ================= grid context menu =================
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
        reset.triggered.connect(lambda: self._reset_rows(rows))
        menu.addAction(reset)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _set_action(self, rows: list[Row], act: Action) -> None:
        for r in rows:
            r.action, r.overridden, r.held, r.conflict = act, True, False, False
        self.model.refilter()
        self._refresh_summary()

    def _reset_rows(self, rows: list[Row]) -> None:
        for r in rows:
            r.overridden = False
        self.replan()

    # ================= synchronize =================
    def _remover(self):
        choice = self.options["deletion"]
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
            return self.error("\n".join(problems))
        rows = self.all_rows()
        acting = [r for r in rows if r.action != Action.NONE]
        to_r = [r for r in acting if r.action == Action.TO_RIGHT]
        to_l = [r for r in acting if r.action == Action.TO_LEFT]
        dels = [r for r in acting if r.action in (Action.DELETE_RIGHT, Action.DELETE_LEFT)]
        moves = [m for m in self.source_moves if m.changes and not m.problem]
        lines = [f"{len(comparisons)} folder pair(s)"]
        if moves:
            lines.append(f"Rename / move {len(moves)} files in the source (UNDO RENAMES puts them back)")
        lines.append(f"Copy {len(to_r)} files to the destination ({human(syncer.total_bytes(to_r))})")
        if to_l:
            lines.append(f"Copy {len(to_l)} files back to the source ({human(syncer.total_bytes(to_l))})")
        if dels:
            lines.append(f"Remove {len(dels)} files  →  {self.options['deletion'].lower()}")
        if held := sum(r.held for r in rows):
            lines.append(f"Hold back {held} files that failed the codec check")
        if QMessageBox.question(self, "pxlsync", "\n".join(lines) + "\n\nStart?") != QMessageBox.StandardButton.Yes:
            return
        self.save_settings()
        self.cancel_flag.clear()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self._set_busy(True, "Syncing…")
        remover = self._remover()
        prune = Mode(self.mode.currentText()) == Mode.MIRROR
        cancelled = self.cancel_flag.is_set
        journal_dir = self.journal_dir()
        grand_total = max(syncer.total_bytes(acting), 1)

        def work(progress):
            renamed = 0
            if moves:
                self._last = f"Renaming {len(moves)} files in the source…"
                progress(0, 1000)
                fileops.apply(moves, journal_dir)  # all or nothing, before anything is copied
                renamed = len(moves)
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
            return renamed, total

        def done(result):
            renamed, stats = result
            self._set_busy(False)
            status = "cancelled" if stats.cancelled else "done"
            self.log.appendPlainText(
                f"■ Sync {status}: {renamed} renamed, {stats.copied} copied ({human(stats.bytes)}), "
                f"{stats.deleted} removed, {len(stats.errors)} errors")
            for e in stats.errors[:50]:
                self.log.appendPlainText(f"  ✕ {e}")
            self.run_compare()  # show the result, like FreeFileSync does

        def failed(msg):
            self._set_busy(False)
            self.error(f"Sync stopped: {msg}")
            self.run_compare()

        def progress(d, _t):
            self.progress.setValue(d)
            self.progress_text.setText(self._last)

        run_task(work, done, failed, progress)

    def undo_renames(self) -> None:
        journal = fileops.latest_journal(self.journal_dir())
        if not journal:
            return self.error("No renames to undo.")
        n = len(json.loads(journal.read_text(encoding="utf-8")))
        if QMessageBox.question(
                self, "pxlsync", f"Put the {n} files renamed/moved by the last sync back where they were in the "
                "source?\n(Copies already in the destination are not touched.)") != QMessageBox.StandardButton.Yes:
            return
        try:
            done = fileops.undo(journal)
        except (OSError, RuntimeError) as exc:
            return self.error(str(exc))
        self.log.appendPlainText(f"Undid {done} renames in the source")
        self.mark_stale("Renames undone: press COMPARE.")

    # ================= verify =================
    def verify(self) -> None:
        """Probe the destination copies of media files and compare codecs with the source."""
        if not self.ffprobe:
            return self.error("ffprobe is not installed (brew install ffmpeg).")
        pairs = []
        for cmp in self.comparisons:
            for r in cmp.rows:
                if r.left and r.right and r.category == Category.EQUAL:
                    src = cmp.left_root / r.left.actual
                    if src in self.infos:
                        pairs.append((src, cmp.right_root / r.right.rel))
        if not pairs:
            return self.error("Nothing to verify yet: sync first, then verify the copies that are in place.")
        self.log.appendPlainText(f"Verifying {len(pairs)} media files in the destination…")
        self.verify_btn.setEnabled(False)
        keys = ("video_codec", "video_profile", "width", "height", "audio_codec")
        ffprobe = self.ffprobe

        def done(infos):
            self.verify_btn.setEnabled(True)
            bad = 0
            for (s, d), di in zip(pairs, infos):
                si = self.infos.get(s)
                if di.error or (si and any(getattr(si, k) != getattr(di, k) for k in keys)):
                    bad += 1
                    self.log.appendPlainText(f"  ✕ {d}: {di.error or 'codec differs from source'}")
            self.log.appendPlainText(f"■ Verify: {len(pairs) - bad} OK, {bad} problems" if bad
                                     else f"■ Verify: all {len(pairs)} media files match the source")

        def failed(msg):
            self.verify_btn.setEnabled(True)
            self.log.appendPlainText(f"Verify failed: {msg}")

        run_task(lambda p: probe_many([d for _, d in pairs], ffprobe, p), done, failed)

    # ================= settings =================
    def save_settings(self) -> None:
        s = self.settings
        s.setValue("pairs", json.dumps(self.pairs_panel.pairs()))
        s.setValue("project", self.project_edit.text())
        s.setValue("client", self.client_edit.text())
        s.setValue("video_preset", self.video_combo.currentText())
        s.setValue("audio_preset", self.audio_combo.currentText())
        s.setValue("sync_mode", self.mode.currentText())
        s.setValue("sync_policy", self.policy.currentText())
        s.setValue("rename_on", self.rename_chk.isChecked())
        s.setValue("rename_template", self.template.currentText())
        s.setValue("organize_on", self.organize_chk.isChecked())
        s.setValue("org_rules", json.dumps([r.to_dict() for r in self.rules]))
        s.setValue("cmp_method", self.options["method"])
        s.setValue("sync_deletion", self.options["deletion"])
        s.setValue("sync_excludes", self.options["excludes"])
        s.setValue("require_audio", self.options["require_audio"])
        s.setValue("rename_strip", self.options["strip_version"])
        s.setValue("rename_lower_ext", self.options["lowercase_ext"])

    def closeEvent(self, event) -> None:  # noqa: N802
        self.save_settings()
        super().closeEvent(event)
