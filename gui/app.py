"""GUI entry point."""
import sys

from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow


def main(argv=None):
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("obsisync")
    app.setOrganizationName("obsisync")
    # A tray-resident app must not exit when its last window is closed.
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
