"""Dialogs: organize rules and the less-used options."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QLineEdit, QVBoxLayout,
)

from ..codecs import ANY_LABEL
from ..compare import Method
from ..organize import OrganizeRule
from .widgets import button, cell, label, row, section, table

DEL_FOLDER = "Keep in .pxlsync-trash folder"
DEL_SYSTEM = "Move to Trash / Recycle Bin"
DEL_PERMANENT = "Delete permanently"


def _buttons(dialog: QDialog) -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class OrganizeDialog(QDialog):
    """Edit the folder rules. First matching rule decides where a file goes."""

    def __init__(self, parent, rules: list[OrganizeRule], codec_labels: list[str]):
        super().__init__(parent)
        self.setWindowTitle("Organize rules")
        self.resize(820, 420)
        self.codec_labels = codec_labels
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)
        lay.addWidget(label("ORGANIZE RULES", "appname"))
        hint = label("Files are sorted into folders inside their source folder before syncing. Rules run top "
                     "to bottom; the first match wins. Folder templates use the rename tokens, e.g. "
                     "{mdate:%Y-%m-%d}/VIDEO/{codec:upper} ({mdate} = the file's own date, so folders stay "
                     "put; {date} = today). / makes sub-folders.", "hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.table = table(["Match files", "Only codec", "Move to folder"], 2)
        self.table.setEditTriggers(self.table.EditTrigger.AllEditTriggers)
        lay.addLayout(row(None, button("+ Add rule", "ghost", self.add_rule),
                          button("Remove", "ghost", self.remove_rule),
                          button("▲", "ghost", lambda: self.move(-1)),
                          button("▼", "ghost", lambda: self.move(1))))
        lay.addWidget(self.table, 1)
        for r in rules:
            self.add_rule(r)
        lay.addWidget(_buttons(self))

    def _combo(self, value: str) -> QComboBox:
        c = QComboBox()
        c.addItem(ANY_LABEL)
        c.addItems(self.codec_labels)
        c.setCurrentText(value or ANY_LABEL)
        return c

    def add_rule(self, rule: OrganizeRule | None = None) -> None:
        rule = rule if isinstance(rule, OrganizeRule) else OrganizeRule("*.mov", "{kind}")
        t = self.table
        r = t.rowCount()
        t.insertRow(r)
        t.setItem(r, 0, cell(rule.pattern))
        t.setCellWidget(r, 1, self._combo(rule.codec))
        t.setItem(r, 2, cell(rule.folder))

    def remove_rule(self) -> None:
        if (r := self.table.currentRow()) >= 0:
            self.table.removeRow(r)

    def move(self, delta: int) -> None:
        r = self.table.currentRow()
        rules = self.rules()
        if 0 <= r < len(rules) and 0 <= r + delta < len(rules):
            rules[r], rules[r + delta] = rules[r + delta], rules[r]
            self.table.setRowCount(0)
            for rule in rules:
                self.add_rule(rule)
            self.table.selectRow(r + delta)

    def rules(self) -> list[OrganizeRule]:
        t, out = self.table, []
        for r in range(t.rowCount()):
            pat = t.item(r, 0).text().strip() if t.item(r, 0) else ""
            folder = t.item(r, 2).text().strip() if t.item(r, 2) else ""
            codec = t.cellWidget(r, 1).currentText()
            if pat and folder:
                out.append(OrganizeRule(pat, folder, "" if codec == ANY_LABEL else codec))
        return out


class OptionsDialog(QDialog):
    """Settings most people set once."""

    def __init__(self, parent, values: dict):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.resize(640, 0)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(12)
        lay.addWidget(label("OPTIONS", "appname"))
        g = QGridLayout()
        g.setHorizontalSpacing(14)
        g.setVerticalSpacing(10)

        self.method = QComboBox()
        self.method.addItems([m.value for m in Method])
        self.method.setCurrentText(values["method"])
        self.deletion = QComboBox()
        self.deletion.addItems([DEL_FOLDER, DEL_SYSTEM, DEL_PERMANENT])
        self.deletion.setCurrentText(values["deletion"])
        self.excludes = QLineEdit(values["excludes"])
        self.excludes.setPlaceholderText("e.g.  *.tmp; *.lrf; Proxies/")
        self.require_audio = QCheckBox("Media files must have audio")
        self.require_audio.setChecked(values["require_audio"])
        self.strip = QCheckBox("Drop old version tags when renaming (shot_v002 → shot)")
        self.strip.setChecked(values["strip_version"])
        self.lower_ext = QCheckBox("Lowercase file extensions when renaming")
        self.lower_ext.setChecked(values["lowercase_ext"])

        rows = [
            ("Compare by", self.method, "Time and size is fast and right for almost everything."),
            ("Removed files", self.deletion, "Where deleted and overwritten files go. The trash folder "
                                             "works on NAS and server shares."),
            ("Ignore", self.excludes, ".DS_Store, ._ files, Thumbs.db and system folders are always ignored. "
                                      "A trailing / means a folder."),
        ]
        for i, (name, widget, help_text) in enumerate(rows):
            g.addWidget(section(name), i * 2, 0)
            g.addWidget(widget, i * 2, 1)
            h = label(help_text, "hint")
            h.setWordWrap(True)
            g.addWidget(h, i * 2 + 1, 1)
        g.setColumnStretch(1, 1)
        lay.addLayout(g)
        for c in (self.require_audio, self.strip, self.lower_ext):
            lay.addWidget(c)
        lay.addWidget(_buttons(self))

    def values(self) -> dict:
        return {
            "method": self.method.currentText(),
            "deletion": self.deletion.currentText(),
            "excludes": self.excludes.text(),
            "require_audio": self.require_audio.isChecked(),
            "strip_version": self.strip.isChecked(),
            "lowercase_ext": self.lower_ext.isChecked(),
        }
