import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .ui import theme
from .ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setOrganizationName("PXLBOX")
    app.setApplicationName("pxlsync")
    theme.load_fonts()
    app.setStyle("Fusion")  # same look on macOS and Windows
    app.setPalette(theme.palette())
    app.setFont(theme.app_font())
    app.setStyleSheet(theme.STYLESHEET)
    app.setWindowIcon(QIcon(str(theme.ASSETS / "icon.png")))
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
