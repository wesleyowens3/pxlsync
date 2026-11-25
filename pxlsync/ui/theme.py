"""PXLBOX look: black, hard edges, 1px white lines, monospace type."""

from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

ASSETS = Path(__file__).resolve().parent.parent / "assets"

BG = "#000000"
PANEL = "#0c0c10"
BOX = "#1d1d26"  # the fill of the PXLBOX cube
LINE = "#34343f"
TEXT = "#f2f2f2"
MUTED = "#8b8b99"
PASS = "#5ee08a"
FAIL = "#ff5c5c"
WARN = "#ffc857"
LEFT = "#7ab8ff"  # copies going back to the source

MONO = ["JetBrains Mono", "SF Mono", "Menlo", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "monospace"]


def load_fonts() -> None:
    """Register the bundled JetBrains Mono so every machine renders the same type."""
    for ttf in (ASSETS / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(ttf))


def app_font(size: float = 10) -> QFont:
    f = QFont()
    f.setFamilies(MONO)
    f.setPointSizeF(size)
    f.setStyleHint(QFont.StyleHint.Monospace)
    return f


def palette() -> QPalette:
    p = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: BG,
        QPalette.ColorRole.Base: PANEL,
        QPalette.ColorRole.AlternateBase: "#111117",
        QPalette.ColorRole.Text: TEXT,
        QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Button: BOX,
        QPalette.ColorRole.ButtonText: TEXT,
        QPalette.ColorRole.Highlight: TEXT,
        QPalette.ColorRole.HighlightedText: BG,
        QPalette.ColorRole.PlaceholderText: MUTED,
        QPalette.ColorRole.ToolTipBase: BOX,
        QPalette.ColorRole.ToolTipText: TEXT,
    }.items():
        p.setColor(role, QColor(color))
    return p


STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; }}
QToolTip {{ background: {BOX}; color: {TEXT}; border: 1px solid {TEXT}; padding: 4px; }}

QLabel#muted, QLabel#hint {{ color: {MUTED}; }}
QLabel#section {{ color: {MUTED}; letter-spacing: 2px; font-weight: bold; }}
QLabel#appname {{ font-size: 20pt; letter-spacing: 6px; }}
QLabel#tag {{ color: {BG}; background: {TEXT}; padding: 1px 6px; font-weight: bold; }}
QLabel#summary {{ font-weight: bold; letter-spacing: 1px; }}

QFrame#rule {{ background: {LINE}; max-height: 1px; min-height: 1px; border: none; }}
QFrame#vrule {{ background: {LINE}; max-width: 1px; min-width: 1px; border: none; }}
QFrame#card {{ background: {PANEL}; border: 1px solid {LINE}; }}
QFrame#card QLabel, QFrame#card QCheckBox {{ background: transparent; }}

QLineEdit, QComboBox, QPlainTextEdit {{
    background: {PANEL}; border: 1px solid {LINE}; padding: 6px 8px;
    selection-background-color: {TEXT}; selection-color: {BG};
}}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {TEXT}; }}
QLineEdit:read-only {{ color: {MUTED}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({ASSETS.joinpath("arrow.svg").as_posix()}); width: 10px; height: 6px; margin-right: 8px; }}
QComboBox QAbstractItemView {{ background: {BOX}; border: 1px solid {TEXT}; outline: none;
    selection-background-color: {TEXT}; selection-color: {BG}; }}

QPushButton {{
    background: {BG}; border: 1px solid {TEXT}; padding: 7px 14px;
    font-weight: bold; letter-spacing: 1px;
}}
QPushButton:hover {{ background: {BOX}; }}
QPushButton:pressed {{ background: {TEXT}; color: {BG}; }}
QPushButton:disabled {{ color: #4a4a55; border-color: #2a2a33; }}
QPushButton#primary {{ background: {TEXT}; color: {BG}; }}
QPushButton#primary:hover {{ background: #cfcfd6; }}
QPushButton#primary:disabled {{ background: #2a2a33; color: #55555f; }}
QPushButton#ghost {{ border: 1px solid {LINE}; color: {MUTED}; font-weight: normal; }}
QPushButton#ghost:hover {{ color: {TEXT}; border-color: {TEXT}; }}
QPushButton#chip {{ border: 1px solid {LINE}; padding: 2px 6px; font-weight: normal; letter-spacing: 0; }}
QPushButton#chip:hover {{ border-color: {TEXT}; }}

QPushButton#filter {{ border: 1px solid {LINE}; color: #55555f; padding: 4px 9px; font-weight: bold; letter-spacing: 0; }}
QPushButton#filter:hover {{ border-color: {MUTED}; }}

QTableView {{
    background: {PANEL}; alternate-background-color: #101016; border: 1px solid {LINE};
    selection-background-color: #2a2a36; selection-color: {TEXT};
}}
QMenu {{ background: {BOX}; border: 1px solid {TEXT}; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; }}
QMenu::item:selected {{ background: {TEXT}; color: {BG}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 4px 8px; }}

QPushButton#icon {{ border: 1px solid {LINE}; padding: 0; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; font-size: 13pt; }}
QPushButton#icon:hover {{ border-color: {TEXT}; }}
QLabel#pairnum {{ color: {MUTED}; border: 1px solid {LINE}; min-width: 22px; max-width: 22px; min-height: 28px; font-weight: bold; }}
QLineEdit[drop="true"] {{ border: 1px dashed {TEXT}; background: {BOX}; }}
QScrollArea {{ background: transparent; }}

QPushButton#nav {{
    text-align: left; border: none; border-left: 2px solid transparent;
    padding: 12px 18px; color: {MUTED}; letter-spacing: 2px;
}}
QPushButton#nav:hover {{ color: {TEXT}; background: {PANEL}; }}
QPushButton#nav:checked {{ color: {TEXT}; background: {BOX}; border-left: 2px solid {TEXT}; }}

QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid {TEXT}; background: {BG}; }}
QCheckBox::indicator:checked {{ background: {TEXT}; }}

QTableWidget {{
    background: {PANEL}; alternate-background-color: #101016; border: 1px solid {LINE};
    gridline-color: {LINE}; selection-background-color: {BOX}; selection-color: {TEXT};
}}
QTableWidget QLineEdit {{ padding: 0 4px; }}
QHeaderView::section {{
    background: {BG}; color: {MUTED}; border: none; border-bottom: 1px solid {TEXT};
    padding: 6px 8px; font-weight: bold; letter-spacing: 1px;
}}
QTableCornerButton::section {{ background: {BG}; border: none; }}

QProgressBar {{ border: 1px solid {LINE}; background: {PANEL}; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {TEXT}; }}

QScrollBar:vertical {{ background: {BG}; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {LINE}; min-height: 24px; }}
QScrollBar:horizontal {{ background: {BG}; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {LINE}; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}

QStatusBar {{ background: {BG}; color: {MUTED}; border-top: 1px solid {LINE}; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 0 10px; }}
QMessageBox, QDialog {{ background: {BOX}; }}
"""
