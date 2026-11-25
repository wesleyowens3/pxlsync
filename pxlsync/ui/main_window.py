"""pxlsync main window: 01 CHECK, 02 RENAME, 03 ORGANIZE, 04 SYNC."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QProcess, QSettings, QStandardPaths, Qt, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar,
    QStackedWidget, QVBoxLayout, QWidget,
)

from .. import __version__, ffs, fileops
from ..check import CheckResult, CheckRule, check
from ..codecs import ANY_LABEL, load_presets
from ..organize import DEFAULT_RULES, OrganizeRule, plan_organize
from ..probe import MediaInfo, find_ffprobe, probe_many, scan_media
from ..rename import existing_names, plan_renames
from ..tokens import TemplateError
from . import theme
from .widgets import button, cell, fill, hrule, label, row, run_task, section, table, vrule

POLICY_BLOCK = "Block the sync"
POLICY_SKIP = "Skip failing files"
POLICY_WARN = "Sync everything (warn only)"

RENAME_PRESETS = [
    "{project}_{name}_v{v:3}",
    "{client}_{project}_{name}_{date}_v{v:3}",
    "{project}_{name}_{codec:upper}_{res}_v{v:3}",
    "{project}_{seq:4}_v{v:2}",
]
TOKENS = ["{project}", "{client}", "{name}", "{v:3}", "{seq:3}", "{date}", "{mdate}",
          "{codec}", "{profile}", "{res}", "{fps}", "{kind}", "{folder}"]


def config_dir() -> Path:
    d = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
    d.mkdir(parents=True, exist_ok=True)
    return d


class Session:
    """What has been scanned: shared by every page."""

    def __init__(self) -> None:
        self.files: list[Path] = []
        self.infos: dict[Path, MediaInfo] = {}
        self.results: dict[Path, CheckResult] = {}

    def failing(self) -> list[Path]:
        return [p for p, r in self.results.items() if not r.ok]

    def apply_moves(self, moves: list[fileops.Move]) -> None:
        """Keep scan results valid after files were renamed or moved."""
        mapping = {m.src: m.dst for m in moves if m.changes and not m.problem}
        self.files = [mapping.get(p, p) for p in self.files]
        for store in (self.infos, self.results):
            for old, new in mapping.items():
                if old in store:
                    store[new] = store.pop(old)
        for path, info in self.infos.items():
            info.path = path


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings("PXLBOX", "pxlsync")
        self.session = Session()
        self.video_presets, self.audio_presets = load_presets(config_dir() / "codec_presets.json")
        self.ffprobe = find_ffprobe()

        self.setWindowTitle("pxlsync")
        self.setWindowIcon(QIcon(str(theme.ASSETS / "icon.png")))
        self.resize(1240, 800)
        self.setMinimumSize(980, 640)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._header())
        outer.addWidget(hrule())
        outer.addWidget(self._folder_bar())
        outer.addWidget(hrule())

        body = QHBoxLayout()
        body.setSpacing(0)
        self.stack = QStackedWidget()
        self.check_page = CheckPage(self)
        self.rename_page = RenamePage(self)
        self.organize_page = OrganizePage(self)
        self.sync_page = SyncPage(self)
        pages = [("01  CHECK", self.check_page), ("02  RENAME", self.rename_page),
                 ("03  ORGANIZE", self.organize_page), ("04  SYNC", self.sync_page)]
        body.addWidget(self._nav([p[0] for p in pages]))
        body.addWidget(vrule())
        for _, page in pages:
            self.stack.addWidget(page)
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.setCentralWidget(central)

        self.tool_status = QLabel()
        self.statusBar().addPermanentWidget(self.tool_status)
        self.statusBar().setSizeGripEnabled(False)
        self.refresh_tool_status()
        self.stack.currentChanged.connect(self._page_changed)

    # ---------- layout pieces ----------
    def _header(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(22, 14, 22, 14)
        logo = QLabel()
        pix = QPixmap(str(theme.ASSETS / "logo.png"))
        logo.setPixmap(pix.scaledToHeight(34, Qt.TransformationMode.SmoothTransformation))
        lay.addWidget(logo)
        lay.addSpacing(16)
        lay.addWidget(vrule())
        lay.addSpacing(16)
        lay.addWidget(label("PXLSYNC", "appname"))
        lay.addSpacing(10)
        lay.addWidget(label(f"v{__version__}", "tag"), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addStretch(1)
        lay.addWidget(label("check · rename · organize · sync", "muted"))
        return w

    def _folder_bar(self) -> QWidget:
        w = QWidget()
        grid = QGridLayout(w)
        grid.setContentsMargins(22, 14, 22, 14)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.source_edit = QLineEdit(self.settings.value("source", ""))
        self.source_edit.setPlaceholderText("Folder with the media to send")
        self.dest_edit = QLineEdit(self.settings.value("dest", ""))
        self.dest_edit.setPlaceholderText("Where it should end up (NAS, drive, server share)")
        self.project_edit = QLineEdit(self.settings.value("project", ""))
        self.project_edit.setPlaceholderText("e.g. PXL0142")
        self.client_edit = QLineEdit(self.settings.value("client", ""))
        self.client_edit.setPlaceholderText("e.g. ACME")
        for e in (self.project_edit, self.client_edit):
            e.setFixedWidth(170)
            e.textChanged.connect(lambda _=None: self.fields_changed())
        self.source_edit.editingFinished.connect(self._source_changed)

        grid.addWidget(section("Source"), 0, 0)
        grid.addWidget(self.source_edit, 0, 1)
        grid.addWidget(button("Browse", "ghost", lambda: self._pick(self.source_edit)), 0, 2)
        grid.addWidget(section("Project"), 0, 3)
        grid.addWidget(self.project_edit, 0, 4)
        grid.addWidget(section("Destination"), 1, 0)
        grid.addWidget(self.dest_edit, 1, 1)
        grid.addWidget(button("Browse", "ghost", lambda: self._pick(self.dest_edit)), 1, 2)
        grid.addWidget(section("Client"), 1, 3)
        grid.addWidget(self.client_edit, 1, 4)
        grid.setColumnStretch(1, 1)
        grid.setColumnMinimumWidth(3, 90)
        return w

    def _nav(self, names: list[str]) -> QWidget:
        w = QWidget()
        w.setFixedWidth(190)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 14, 0, 14)
        lay.setSpacing(2)
        self.nav_group = QButtonGroup(self)
        for i, name in enumerate(names):
            b = button(name, "nav")
            b.setCheckable(True)
            self.nav_group.addButton(b, i)
            lay.addWidget(b)
        self.nav_group.button(0).setChecked(True)
        self.nav_group.idClicked.connect(self.stack.setCurrentIndex)
        lay.addStretch(1)
        self.scan_summary = label("Nothing scanned yet", "hint")
        self.scan_summary.setWordWrap(True)
        self.scan_summary.setContentsMargins(18, 0, 12, 0)
        lay.addWidget(self.scan_summary)
        return w

    # ---------- shared helpers ----------
    def _pick(self, edit: QLineEdit) -> None:
        d = QFileDialog.getExistingDirectory(self, "Choose folder", edit.text() or str(Path.home()))
        if d:
            edit.setText(d)
            if edit is self.source_edit:
                self._source_changed()
            self.save_settings()

    def _source_changed(self) -> None:
        if self.session.files and Path(self.source_edit.text()) != self.scanned_root:
            self.session = Session()
            self.check_page.render()
            self.update_scan_summary()
        self.save_settings()

    scanned_root: Path | None = None

    def source(self) -> Path | None:
        t = self.source_edit.text().strip()
        return Path(t) if t else None

    def dest(self) -> Path | None:
        t = self.dest_edit.text().strip()
        return Path(t) if t else None

    def fields(self) -> dict[str, str]:
        return {"project": self.project_edit.text().strip(), "client": self.client_edit.text().strip()}

    def fields_changed(self) -> None:
        self.rename_page.schedule_preview()
        self.organize_page.schedule_preview()

    def journal_dir(self) -> Path:
        return config_dir() / "journals"

    def need_scan(self) -> bool:
        if self.session.files:
            return False
        QMessageBox.information(self, "pxlsync", "Scan the source folder first (01 CHECK).")
        self.nav_group.button(0).click()
        return True

    def error(self, text: str) -> None:
        QMessageBox.warning(self, "pxlsync", text)

    def refresh_tool_status(self) -> None:
        ffs_path = self.sync_page.ffs_path()
        parts = [
            f"ffprobe: {'OK' if self.ffprobe else 'NOT FOUND'}",
            f"FreeFileSync: {'OK' if ffs_path else 'NOT FOUND'}",
        ]
        self.tool_status.setText("   ·   ".join(parts))

    def update_scan_summary(self) -> None:
        s = self.session
        if not s.files:
            self.scan_summary.setText("Nothing scanned yet")
            return
        bad = len(s.failing())
        self.scan_summary.setText(f"{len(s.files)} files scanned\n{len(s.files) - bad} pass · {bad} fail")

    def after_moves(self, moves: list[fileops.Move]) -> None:
        self.session.apply_moves(moves)
        self.check_page.render()
        self.rename_page.schedule_preview()
        self.organize_page.schedule_preview()

    def _page_changed(self, index: int) -> None:
        page = self.stack.widget(index)
        if hasattr(page, "on_show"):
            page.on_show()

    def save_settings(self) -> None:
        s = self.settings
        s.setValue("source", self.source_edit.text())
        s.setValue("dest", self.dest_edit.text())
        s.setValue("project", self.project_edit.text())
        s.setValue("client", self.client_edit.text())
        for page in (self.check_page, self.rename_page, self.organize_page, self.sync_page):
            page.save(s)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        self.save_settings()
        super().closeEvent(event)


class Page(QWidget):
    def __init__(self, app: MainWindow, title: str, blurb: str):
        super().__init__()
        self.app = app
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(26, 20, 26, 20)
        self.lay.setSpacing(12)
        self.lay.addWidget(label(title.upper(), "appname"))
        hint = label(blurb, "hint")
        hint.setWordWrap(True)
        self.lay.addWidget(hint)
        self.lay.addSpacing(4)

    def save(self, settings: QSettings) -> None:
        pass


# ============================== 01 CHECK ==============================
class CheckPage(Page):
    def __init__(self, app: MainWindow):
        super().__init__(app, "Codec check", "Scan the source and flag every file that isn't in the codec this job needs.")
        s = app.settings
        self.video_combo = QComboBox()
        self.video_combo.addItems([p.label for p in app.video_presets])
        self.video_combo.setCurrentText(s.value("video_preset", ANY_LABEL))
        self.audio_combo = QComboBox()
        self.audio_combo.addItems([p.label for p in app.audio_presets])
        self.audio_combo.setCurrentText(s.value("audio_preset", ANY_LABEL))
        self.require_audio = QCheckBox("Audio required")
        self.require_audio.setChecked(s.value("require_audio", False, type=bool))
        for w in (self.video_combo, self.audio_combo):
            w.setMinimumWidth(240)
            w.currentTextChanged.connect(lambda _=None: self.recheck())
        self.require_audio.toggled.connect(lambda _=None: self.recheck())
        self.scan_btn = button("Scan source", "primary", self.scan)

        self.lay.addLayout(row(section("Video codec"), self.video_combo, section("Audio codec"),
                               self.audio_combo, self.require_audio, None, self.scan_btn, spacing=12))
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.lay.addWidget(self.progress)
        self.summary = label("", "summary")
        self.lay.addWidget(self.summary)
        self.table = table(["Status", "File", "Video", "Profile", "Resolution", "FPS", "Audio", "Issue"], 7)
        self.lay.addWidget(self.table, 1)
        self.render()

    def rule(self) -> CheckRule:
        v = next(p for p in self.app.video_presets if p.label == self.video_combo.currentText())
        a = next(p for p in self.app.audio_presets if p.label == self.audio_combo.currentText())
        return CheckRule(video=v, audio=a, require_audio=self.require_audio.isChecked())

    def scan(self) -> None:
        src = self.app.source()
        if not src or not src.is_dir():
            return self.app.error("Choose a source folder that exists.")
        if not self.app.ffprobe:
            return self.app.error(
                "ffprobe was not found. Install ffmpeg (macOS: brew install ffmpeg, "
                "Windows: winget install ffmpeg) or set PXLSYNC_FFPROBE to its path."
            )
        self.app.save_settings()
        self.scan_btn.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.summary.setText("SCANNING…")
        ffprobe = self.app.ffprobe

        def work(progress):
            files = scan_media(src)
            return files, probe_many(files, ffprobe, progress)

        def done(result):
            files, infos = result
            self.app.session = Session()
            self.app.session.files = files
            self.app.session.infos = {i.path: i for i in infos}
            self.app.scanned_root = src
            self._finish()
            self.recheck()

        def failed(msg):
            self._finish()
            self.app.error(f"Scan failed: {msg}")

        def progress(d, t):
            self.progress.setMaximum(t)
            self.progress.setValue(d)

        run_task(work, done, failed, progress)

    def _finish(self) -> None:
        self.scan_btn.setEnabled(True)
        self.progress.setVisible(False)

    def recheck(self) -> None:
        sess = self.app.session
        rule = self.rule()
        sess.results = {p: check(sess.infos[p], rule) for p in sess.files if p in sess.infos}
        self.render()
        self.app.save_settings()

    def render(self) -> None:
        sess = self.app.session
        root = self.app.scanned_root
        rows = []
        # failing files first, then by path
        for path in sorted(sess.files, key=lambda p: (sess.results.get(p) is None or sess.results[p].ok, str(p))):
            info, res = sess.infos.get(path), sess.results.get(path)
            ok = res.ok if res else True
            rel = path.relative_to(root).as_posix() if root and path.is_relative_to(root) else str(path)
            issue = "; ".join(res.reasons) if res else ""
            rows.append([
                cell("PASS" if ok else "FAIL", theme.PASS if ok else theme.FAIL, bold=True),
                cell(rel, tip=str(path)),
                cell(info.video_codec if info else ""),
                cell(info.video_profile if info else ""),
                cell(info.resolution if info else ""),
                cell(info.fps_text if info else ""),
                cell(info.audio_codec if info else ""),
                cell(issue, theme.FAIL if issue else None, tip=issue or None),
            ])
        fill(self.table, rows)
        bad = len(sess.failing())
        if sess.files:
            text = f"{len(sess.files)} FILES  ·  {len(sess.files) - bad} PASS  ·  {bad} FAIL"
            self.summary.setStyleSheet(f"color: {theme.FAIL if bad else theme.PASS};")
        else:
            text = "NO FILES SCANNED"
            self.summary.setStyleSheet(f"color: {theme.MUTED};")
        self.summary.setText(text)
        self.app.update_scan_summary() if hasattr(self.app, "scan_summary") else None

    def save(self, s: QSettings) -> None:
        s.setValue("video_preset", self.video_combo.currentText())
        s.setValue("audio_preset", self.audio_combo.currentText())
        s.setValue("require_audio", self.require_audio.isChecked())


# ============================== 02 RENAME ==============================
class MovePage(Page):
    """Shared preview / apply / undo for RENAME and ORGANIZE."""

    noun = "files"

    def __init__(self, app, title, blurb):
        super().__init__(app, title, blurb)
        self.moves: list[fileops.Move] = []
        self.timer = QTimer(self, singleShot=True, interval=250)
        self.timer.timeout.connect(self.preview)

    def schedule_preview(self) -> None:
        self.timer.start()

    def on_show(self) -> None:
        self.preview()

    def plan(self) -> list[fileops.Move]:
        raise NotImplementedError

    def preview(self) -> None:
        if not self.app.session.files:
            fill(self.preview_table, [])
            self.status.setText("Scan the source in 01 CHECK to see a preview.")
            self.apply_btn.setEnabled(False)
            return
        try:
            self.moves = self.plan()
        except TemplateError as exc:
            self.moves = []
            fill(self.preview_table, [])
            self.status.setText(f"Template problem: {exc}")
            self.status.setStyleSheet(f"color: {theme.FAIL};")
            self.apply_btn.setEnabled(False)
            return
        root = self.app.scanned_root
        rel = lambda p: p.relative_to(root).as_posix() if root and p.is_relative_to(root) else str(p)  # noqa: E731
        rows = []
        for m in sorted(self.moves, key=lambda m: (not m.problem, not m.changes, str(m.src))):
            note, color = ("", theme.MUTED)
            if m.problem:
                note, color = m.problem, theme.FAIL
            elif m.changes:
                note, color = "will change", theme.PASS
            else:
                note = "no change"
            rows.append([cell(rel(m.src)), cell(rel(m.dst), theme.TEXT if m.changes else theme.MUTED),
                         cell(note, color)])
        fill(self.preview_table, rows)
        changing = [m for m in self.moves if m.changes and not m.problem]
        problems = [m for m in self.moves if m.problem]
        self.status.setText(f"{len(changing)} {self.noun} will change  ·  {len(problems)} blocked")
        self.status.setStyleSheet(f"color: {theme.FAIL if problems else theme.TEXT};")
        self.apply_btn.setEnabled(bool(changing))

    def apply(self) -> None:
        changing = [m for m in self.moves if m.changes and not m.problem]
        if not changing:
            return
        if QMessageBox.question(self, "pxlsync", f"Change {len(changing)} {self.noun} in the source folder?\n"
                                "You can undo this with UNDO LAST.") != QMessageBox.StandardButton.Yes:
            return
        try:
            fileops.apply(self.moves, self.app.journal_dir())
        except OSError as exc:
            return self.app.error(f"Nothing was changed: {exc}")
        self.app.after_moves(self.moves)
        self.app.statusBar().showMessage(f"{len(changing)} {self.noun} changed", 6000)

    def undo(self) -> None:
        journal = fileops.latest_journal(self.app.journal_dir())
        if not journal:
            return self.app.error("Nothing to undo.")
        entries = json.loads(journal.read_text(encoding="utf-8"))
        if QMessageBox.question(self, "pxlsync", f"Undo the last change ({len(entries)} files)?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            n = fileops.undo(journal)
        except (OSError, RuntimeError) as exc:
            return self.app.error(str(exc))
        back = [fileops.Move(Path(e["dst"]), Path(e["src"])) for e in entries]
        self.app.after_moves(back)
        self.app.statusBar().showMessage(f"Undid {n} changes", 6000)

    def add_preview_area(self, headers: list[str]) -> None:
        self.status = label("", "summary")
        self.apply_btn = button("Apply", "primary", self.apply)
        self.lay.addLayout(row(self.status, None, button("Undo last", "ghost", self.undo), self.apply_btn))
        self.preview_table = table(headers, 1)
        self.lay.addWidget(self.preview_table, 1)

    def target_files(self) -> list[Path]:
        sess = self.app.session
        if self.only_passing.isChecked():
            return [p for p in sess.files if sess.results.get(p) is None or sess.results[p].ok]
        return list(sess.files)


class RenamePage(MovePage):
    noun = "names"

    def __init__(self, app: MainWindow):
        super().__init__(app, "Version naming",
                         "Rename files to the studio naming scheme. Version numbers continue from what "
                         "already exists in the source and destination, so nothing gets overwritten.")
        s = app.settings
        self.template = QComboBox()
        self.template.setEditable(True)
        self.template.addItems(RENAME_PRESETS)
        self.template.setCurrentText(s.value("rename_template", RENAME_PRESETS[0]))
        self.template.currentTextChanged.connect(lambda _=None: self.schedule_preview())
        self.lay.addLayout(row(section("Template"), self.template, stretch_at=1, spacing=12))

        chips = QHBoxLayout()
        chips.setSpacing(4)
        chips.addWidget(label("insert:", "hint"))
        for t in TOKENS:
            chips.addWidget(button(t, "chip", lambda _=False, t=t: self._insert(t)))
        chips.addStretch(1)
        self.lay.addLayout(chips)

        self.strip = QCheckBox("Drop old version tags from {name} (shot_v002 → shot)")
        self.strip.setChecked(s.value("rename_strip", True, type=bool))
        self.lower_ext = QCheckBox("Lowercase extensions")
        self.lower_ext.setChecked(s.value("rename_lower_ext", False, type=bool))
        self.only_passing = QCheckBox("Only files that passed the codec check")
        self.only_passing.setChecked(s.value("rename_only_passing", False, type=bool))
        for c in (self.strip, self.lower_ext, self.only_passing):
            c.toggled.connect(lambda _=None: self.schedule_preview())
        self.lay.addLayout(row(self.strip, self.lower_ext, self.only_passing, None, spacing=20))
        self.lay.addWidget(hrule())
        self.add_preview_area(["Current name", "New name", "Note"])
        self.apply_btn.setText("APPLY RENAMES")

    def _insert(self, token: str) -> None:
        self.template.lineEdit().insert(token)
        self.template.setFocus()

    def plan(self) -> list[fileops.Move]:
        known = existing_names(*(p for p in (self.app.scanned_root, self.app.dest()) if p))
        return plan_renames(
            self.target_files(), self.template.currentText(), self.app.fields(),
            infos=self.app.session.infos, strip_version=self.strip.isChecked(),
            known_names=known, lowercase_ext=self.lower_ext.isChecked(),
        )

    def save(self, s: QSettings) -> None:
        s.setValue("rename_template", self.template.currentText())
        s.setValue("rename_strip", self.strip.isChecked())
        s.setValue("rename_lower_ext", self.lower_ext.isChecked())
        s.setValue("rename_only_passing", self.only_passing.isChecked())


# ============================== 03 ORGANIZE ==============================
class OrganizePage(MovePage):
    def __init__(self, app: MainWindow):
        super().__init__(app, "Organize",
                         "Sort files into folders inside the source before syncing. Rules run top to "
                         "bottom; the first match wins. Folder templates use the same tokens as RENAME, "
                         "and / makes sub-folders.")
        self.rules_table = table(["Match files", "Only codec", "Move to folder"], 2)
        self.rules_table.setEditTriggers(self.rules_table.EditTrigger.AllEditTriggers)
        self.rules_table.setMaximumHeight(190)
        self.rules_table.itemChanged.connect(lambda _=None: self.schedule_preview())
        self.only_passing = QCheckBox("Only files that passed the codec check")
        self.only_passing.setChecked(app.settings.value("org_only_passing", False, type=bool))
        self.only_passing.toggled.connect(lambda _=None: self.schedule_preview())
        self.lay.addLayout(row(section("Rules"), None, self.only_passing,
                               button("+ Add rule", "ghost", self.add_rule),
                               button("Remove", "ghost", self.remove_rule),
                               button("▲", "ghost", lambda: self.move_rule(-1)),
                               button("▼", "ghost", lambda: self.move_rule(1))))
        self.lay.addWidget(self.rules_table)
        saved = app.settings.value("org_rules", "")
        try:
            rules = [OrganizeRule(**r) for r in json.loads(saved)] if saved else list(DEFAULT_RULES)
        except (ValueError, TypeError):
            rules = list(DEFAULT_RULES)
        for r in rules:
            self.add_rule(r)
        self.lay.addWidget(hrule())
        self.add_preview_area(["File", "New location", "Note"])
        self.noun = "files"
        self.apply_btn.setText("APPLY MOVES")

    def _codec_combo(self, value: str) -> QComboBox:
        c = QComboBox()
        c.addItem(ANY_LABEL)
        c.addItems([p.label for p in self.app.video_presets if not p.is_any])
        c.addItems([p.label for p in self.app.audio_presets if not p.is_any])
        c.setCurrentText(value or ANY_LABEL)
        c.currentTextChanged.connect(lambda _=None: self.schedule_preview())
        return c

    def add_rule(self, rule: OrganizeRule | None = None) -> None:
        rule = rule if isinstance(rule, OrganizeRule) else OrganizeRule("*.mov", "{kind}")
        t = self.rules_table
        r = t.rowCount()
        t.blockSignals(True)
        t.insertRow(r)
        t.setItem(r, 0, cell(rule.pattern))
        t.setCellWidget(r, 1, self._codec_combo(rule.codec))
        t.setItem(r, 2, cell(rule.folder))
        t.blockSignals(False)
        self.schedule_preview()

    def rules(self) -> list[OrganizeRule]:
        t = self.rules_table
        out = []
        for r in range(t.rowCount()):
            pat = t.item(r, 0).text().strip() if t.item(r, 0) else ""
            folder = t.item(r, 2).text().strip() if t.item(r, 2) else ""
            if pat and folder:
                codec = t.cellWidget(r, 1).currentText()
                out.append(OrganizeRule(pat, folder, "" if codec == ANY_LABEL else codec))
        return out

    def remove_rule(self) -> None:
        r = self.rules_table.currentRow()
        if r >= 0:
            self.rules_table.removeRow(r)
            self.schedule_preview()

    def move_rule(self, delta: int) -> None:
        t = self.rules_table
        r = t.currentRow()
        rules = self.rules()
        if not (0 <= r < len(rules)) or not (0 <= r + delta < len(rules)):
            return
        rules[r], rules[r + delta] = rules[r + delta], rules[r]
        t.setRowCount(0)
        for rule in rules:
            self.add_rule(rule)
        t.selectRow(r + delta)

    def plan(self) -> list[fileops.Move]:
        return plan_organize(self.target_files(), self.app.scanned_root, self.rules(), self.app.fields(),
                             self.app.session.infos, self.app.video_presets + self.app.audio_presets)

    def save(self, s: QSettings) -> None:
        s.setValue("org_rules", json.dumps([r.to_dict() for r in self.rules()]))
        s.setValue("org_only_passing", self.only_passing.isChecked())


# ============================== 04 SYNC ==============================
class SyncPage(Page):
    def __init__(self, app: MainWindow):
        super().__init__(app, "Sync",
                         "Copies the source to the destination with FreeFileSync. Failing files can "
                         "block the sync or be left out of it.")
        s = app.settings
        self.mode = QComboBox()
        self.mode.addItems(list(ffs.MODES))
        self.mode.setCurrentText(s.value("sync_mode", list(ffs.MODES)[1]))
        self.policy = QComboBox()
        self.policy.addItems([POLICY_BLOCK, POLICY_SKIP, POLICY_WARN])
        self.policy.setCurrentText(s.value("sync_policy", POLICY_BLOCK))
        for c in (self.mode, self.policy):
            c.setMinimumWidth(330)
            c.currentTextChanged.connect(lambda _=None: self.on_show())

        self.ffs_edit = QLineEdit(s.value("ffs_path", ""))
        self.ffs_edit.setPlaceholderText(ffs.find_freefilesync() or "FreeFileSync not found: click LOCATE")
        self.ffs_edit.textChanged.connect(lambda _=None: (self.app.refresh_tool_status(), self.on_show()))
        self.template_edit = QLineEdit(s.value("ffs_template", ""))
        self.template_edit.setPlaceholderText("Optional: a .ffs_batch saved from FreeFileSync with your preferred settings")

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.addWidget(section("Mode"), 0, 0)
        grid.addWidget(self.mode, 0, 1)
        grid.addWidget(section("If files fail"), 1, 0)
        grid.addWidget(self.policy, 1, 1)
        grid.addWidget(section("FreeFileSync"), 2, 0)
        grid.addWidget(self.ffs_edit, 2, 1, 1, 2)
        grid.addWidget(button("Locate", "ghost", self.locate_ffs), 2, 3)
        grid.addWidget(section("Settings template"), 3, 0)
        grid.addWidget(self.template_edit, 3, 1, 1, 2)
        grid.addWidget(button("Choose", "ghost", self.choose_template), 3, 3)
        grid.setColumnStretch(2, 1)
        self.lay.addLayout(grid)

        self.checklist = QFrame()
        self.checklist.setObjectName("card")
        cl = QVBoxLayout(self.checklist)
        cl.setContentsMargins(16, 12, 16, 12)
        self.check_label = QLabel()
        self.check_label.setTextFormat(Qt.TextFormat.RichText)
        cl.addWidget(self.check_label)
        self.lay.addWidget(self.checklist)

        self.run_btn = button("Run sync", "primary", self.run_sync)
        self.verify_btn = button("Verify destination", None, self.verify)
        self.lay.addLayout(row(button("Save batch file…", "ghost", self.save_batch), None,
                               self.verify_btn, self.run_btn))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Sync log")
        self.lay.addWidget(self.log, 1)
        self.proc: QProcess | None = None

    def ffs_path(self) -> str | None:
        t = self.ffs_edit.text().strip() if hasattr(self, "ffs_edit") else ""
        return t if t and Path(t).exists() else ffs.find_freefilesync()

    def locate_ffs(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Locate FreeFileSync")
        if path:
            if path.endswith(".app"):
                path = str(Path(path) / "Contents/MacOS/FreeFileSync")
            self.ffs_edit.setText(path)

    def choose_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "FreeFileSync batch template", "",
                                              "FreeFileSync (*.ffs_batch *.ffs_gui)")
        if path:
            self.template_edit.setText(path)

    def excluded(self) -> list[Path]:
        return self.app.session.failing() if self.policy.currentText() == POLICY_SKIP else []

    def problems(self) -> list[str]:
        out = []
        src, dst = self.app.source(), self.app.dest()
        if not src or not src.is_dir():
            out.append("Source folder is missing")
        if not dst:
            out.append("Destination folder is not set")
        elif src and dst.resolve() == src.resolve():
            out.append("Source and destination are the same folder")
        if not self.ffs_path():
            out.append("FreeFileSync is not installed or not located")
        if not self.app.session.files:
            out.append("Source not scanned in 01 CHECK")
        elif self.app.scanned_root != src:
            out.append("Source changed since the last scan: scan again")
        bad = self.app.session.failing()
        if bad and self.policy.currentText() == POLICY_BLOCK:
            out.append(f"{len(bad)} files failed the codec check")
        return out

    def on_show(self) -> None:
        sess = self.app.session
        bad = sess.failing()
        lines = []
        for text, ok in [
            (f"Source: {self.app.source() or '-'}", bool(self.app.source() and self.app.source().is_dir())),
            (f"Destination: {self.app.dest() or '-'}", bool(self.app.dest())),
            (f"FreeFileSync: {self.ffs_path() or 'not found'}", bool(self.ffs_path())),
            (f"Codec check: {len(sess.files)} files, {len(bad)} failing",
             bool(sess.files) and (not bad or self.policy.currentText() != POLICY_BLOCK)),
        ]:
            color = theme.PASS if ok else theme.FAIL
            lines.append(f"<span style='color:{color}'>{'■' if ok else '□'}</span>&nbsp;&nbsp;{text}")
        if bad and self.policy.currentText() == POLICY_SKIP:
            lines.append(f"<span style='color:{theme.WARN}'>!</span>&nbsp;&nbsp;{len(bad)} failing files will be left out")
        elif bad and self.policy.currentText() == POLICY_WARN:
            lines.append(f"<span style='color:{theme.WARN}'>!</span>&nbsp;&nbsp;failing files will be synced anyway")
        self.check_label.setText("<br>".join(lines))
        problems = self.problems()
        self.run_btn.setEnabled(not problems and self.proc is None)
        self.run_btn.setToolTip("\n".join(problems))
        self.verify_btn.setEnabled(bool(sess.files) and bool(self.app.dest()))

    def _batch_xml(self) -> str:
        tpl = self.template_edit.text().strip()
        return ffs.build_batch(self.app.source(), self.app.dest(), self.mode.currentText(),
                               self.excluded(), Path(tpl) if tpl else None)

    def save_batch(self) -> None:
        if not self.app.source() or not self.app.dest():
            return self.app.error("Set the source and destination first.")
        path, _ = QFileDialog.getSaveFileName(self, "Save batch file", "pxlsync.ffs_batch", "*.ffs_batch")
        if path:
            try:
                Path(path).write_text(self._batch_xml(), encoding="utf-8")
            except (OSError, ValueError) as exc:
                return self.app.error(str(exc))
            self.log.appendPlainText(f"Saved {path}")

    def run_sync(self) -> None:
        if problems := self.problems():
            return self.app.error("\n".join(problems))
        self.app.save_settings()
        batch = config_dir() / "last_run.ffs_batch"
        try:
            batch.write_text(self._batch_xml(), encoding="utf-8")
        except (OSError, ValueError) as exc:
            return self.app.error(f"Could not write the batch file: {exc}")
        excluded = self.excluded()
        self.log.appendPlainText(f"▶ {self.mode.currentText().split('(')[0].strip()}  "
                                 f"{self.app.source()}  →  {self.app.dest()}")
        if excluded:
            self.log.appendPlainText(f"  leaving out {len(excluded)} failing files")
        self.proc = QProcess(self)
        self.proc.finished.connect(self._sync_finished)
        self.proc.errorOccurred.connect(lambda e: self.log.appendPlainText(f"Could not start FreeFileSync: {e}"))
        self.proc.start(self.ffs_path(), [str(batch)])
        self.on_show()

    def _sync_finished(self, code: int, _status) -> None:
        self.log.appendPlainText(f"■ {ffs.EXIT_CODES.get(code, f'FreeFileSync exit code {code}')}")
        self.proc = None
        self.on_show()

    def verify(self) -> None:
        """Probe the copies in the destination and compare them with the source."""
        sess, src, dst = self.app.session, self.app.scanned_root, self.app.dest()
        skip = set(self.excluded())
        pairs = [(p, dst / p.relative_to(src)) for p in sess.files if p not in skip]
        missing = [d for _, d in pairs if not d.exists()]
        present = [(s, d) for s, d in pairs if d.exists()]
        self.log.appendPlainText(f"Verifying {len(present)} files in destination…")
        self.verify_btn.setEnabled(False)
        ffprobe = self.app.ffprobe

        def work(progress):
            return probe_many([d for _, d in present], ffprobe, progress)

        def done(infos):
            bad = 0
            for (s, d), di in zip(present, infos):
                si = sess.infos.get(s)
                keys = ("video_codec", "video_profile", "width", "height", "audio_codec")
                if di.error or (si and any(getattr(si, k) != getattr(di, k) for k in keys)):
                    bad += 1
                    self.log.appendPlainText(f"  ✕ {d}: {di.error or 'codec differs from source'}")
            for d in missing:
                self.log.appendPlainText(f"  ✕ missing: {d}")
            total_bad = bad + len(missing)
            self.log.appendPlainText(
                f"■ Verify: {len(present) - bad} OK, {total_bad} problems" if total_bad
                else f"■ Verify: all {len(present)} files match the source")
            self.on_show()

        def failed(msg):
            self.log.appendPlainText(f"Verify failed: {msg}")
            self.on_show()

        run_task(work, done, failed)

    def save(self, s: QSettings) -> None:
        s.setValue("sync_mode", self.mode.currentText())
        s.setValue("sync_policy", self.policy.currentText())
        s.setValue("ffs_path", self.ffs_edit.text())
        s.setValue("ffs_template", self.template_edit.text())
