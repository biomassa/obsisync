"""GUI entry point: setup if needed, then connect, then run the daemon."""
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

import config
import state_db
import sync_engine
from gui.main_window import MainWindow
from gui.session import SessionManager
from gui.wizard import SetupDialog, TwoFactorDialog


def is_configured(cfg):
    return bool(cfg.get("apple_id")) and bool(cfg.get("vault_name"))


class Application:
    """Ties the window, the session and the daemon together."""

    def __init__(self, argv):
        self.app = QApplication(argv)
        self.app.setApplicationName("obsisync")
        self.app.setOrganizationName("obsisync")
        # A daemon with a window must survive that window being closed.
        self.app.setQuitOnLastWindowClosed(False)

        state_db.init()
        self.cfg = config.load()
        sync_engine.set_log_level(self.cfg.get("log_level", "INFO"))

        self.window = MainWindow()
        self.session = SessionManager()

        self.session.connected.connect(self._on_connected, Qt.QueuedConnection)
        self.session.failed.connect(self._on_failed, Qt.QueuedConnection)
        self.session.twoFactorNeeded.connect(self._on_twofa, Qt.QueuedConnection)
        self.app.aboutToQuit.connect(self._on_quit)

    def run(self):
        if not is_configured(self.cfg):
            dialog = SetupDialog()
            if dialog.exec() != SetupDialog.Accepted:
                return 0                      # user cancelled setup; nothing to run
            self.cfg = dialog.result_config()
            # Setup already authenticated, so reuse that session rather than
            # asking Apple (and possibly the user) all over again.
            if dialog.api is not None:
                self._adopt(dialog.api)

        self.window.show()

        if self.session.api is None:
            sync_engine.log("INFO", "Connecting to iCloud…")
            self.session.connect_async(self.cfg)

        return self.app.exec()

    def _adopt(self, api):
        from auth import find_vault_root
        vault_node = find_vault_root(api, self.cfg["vault_name"])
        if vault_node is None:
            self._on_failed(f"Vault '{self.cfg['vault_name']}' not found on iCloud Drive.")
            return
        self.session.api = api
        self.session.vault_node = vault_node
        self._on_connected(api, vault_node)

    def _on_connected(self, api, vault_node):
        self.window.controller.attach_session(api, vault_node, self.cfg)
        self.session.start_daemon(self.cfg)
        self.window.statusBar().showMessage("Connected to iCloud", 4000)

    def _on_failed(self, reason):
        sync_engine.log("ERROR", f"Not connected: {reason}")
        QMessageBox.warning(self.window, "Could not connect to iCloud", reason)

    def _on_twofa(self):
        dialog = TwoFactorDialog(self.window)
        code = dialog.value() if dialog.exec() == TwoFactorDialog.Accepted else None
        self.session.prompt.provide(code)

    def _on_quit(self):
        self.window.prepare_quit()
        self.session.shutdown()


def main(argv=None):
    return Application(argv if argv is not None else sys.argv).run()


if __name__ == "__main__":
    sys.exit(main())
