"""First-run setup, and the re-auth prompt.

Implemented as a QDialog over a QStackedWidget rather than a QWizard: the flow is
driven by network results (is 2FA required? which vaults exist?) rather than by a
fixed page order, and that is awkward to express through QWizard's page ids.

Every network call runs on a worker thread. The dialog only ever reacts to signals.
"""
import os
import threading

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QProgressBar, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

import config
import sync_engine
from auth import (
    TwoFactorRequired, authenticate, discover_vaults, get_password, save_password,
)
from paths import default_vault_path

PAGE_CREDENTIALS, PAGE_TWOFA, PAGE_VAULT, PAGE_FOLDER = range(4)


class _Worker(QObject):
    """Runs the blocking parts of setup and reports back with signals."""

    authenticated = Signal(object)      # api
    twoFactorNeeded = Signal(object)    # api
    vaultsFound = Signal(list)
    failed = Signal(str)

    def sign_in(self, email, password):
        threading.Thread(
            target=self._sign_in, args=(email, password), daemon=True,
            name="setup-signin").start()

    def _sign_in(self, email, password):
        try:
            api = authenticate(email, password, interactive=False)
            self.authenticated.emit(api)
        except TwoFactorRequired:
            # Reaching here means credentials were accepted and only the code is
            # missing, so re-authenticate below once we have one.
            self.twoFactorNeeded.emit(None)
        except Exception as exc:
            self.failed.emit(str(exc))

    def submit_code(self, email, password, code):
        threading.Thread(
            target=self._submit_code, args=(email, password, code), daemon=True,
            name="setup-2fa").start()

    def _submit_code(self, email, password, code):
        try:
            api = authenticate(
                email, password, interactive=False, twofa_callback=lambda _api: code)
            self.authenticated.emit(api)
        except Exception as exc:
            self.failed.emit(str(exc))

    def find_vaults(self, api):
        threading.Thread(
            target=self._find_vaults, args=(api,), daemon=True,
            name="setup-vaults").start()

    def _find_vaults(self, api):
        try:
            self.vaultsFound.emit(list(discover_vaults(api)))
        except Exception as exc:
            self.failed.emit(str(exc))


class SetupDialog(QDialog):
    """Collects Apple ID, 2FA code, vault and local folder, then writes config."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set up obsisync")
        # Without an explicit size the stack adopts the tallest page's hint and
        # the dialog opens absurdly tall for a two-field form.
        self.setMinimumWidth(520)
        self.resize(560, 400)

        self.api = None
        self._email = ""
        self._password = ""

        self.worker = _Worker(self)
        self.worker.authenticated.connect(self._on_authenticated, Qt.QueuedConnection)
        self.worker.twoFactorNeeded.connect(self._on_twofa_needed, Qt.QueuedConnection)
        self.worker.vaultsFound.connect(self._on_vaults, Qt.QueuedConnection)
        self.worker.failed.connect(self._on_failed, Qt.QueuedConnection)

        layout = QVBoxLayout(self)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self._build_credentials()
        self._build_twofa()
        self._build_vault()
        self._build_folder()

        self.busy = QProgressBar()
        self.busy.setRange(0, 0)
        self.busy.hide()
        layout.addWidget(self.busy)

        self.message = QLabel("")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.next_btn = self.buttons.addButton("Continue", QDialogButtonBox.AcceptRole)
        self.buttons.rejected.connect(self.reject)
        self.next_btn.clicked.connect(self._advance)
        layout.addWidget(self.buttons)

    # ── pages ───────────────────────────────────────

    def _build_credentials(self):
        page = QWidget()
        form = QFormLayout(page)
        intro = QLabel(
            "Sign in with the Apple ID your Obsidian vault is stored under.\n"
            "Your password is kept in the system credential store, never in a file.")
        intro.setWordWrap(True)
        form.addRow(intro)
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.com")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("Apple ID", self.email)
        form.addRow("Password", self.password)
        form.addRow(QLabel(""))          # absorb slack instead of stretching fields
        self.stack.addWidget(page)

    def _build_twofa(self):
        page = QWidget()
        box = QVBoxLayout(page)
        label = QLabel("Enter the six-digit code Apple sent to your devices.")
        label.setWordWrap(True)
        self.code = QLineEdit()
        self.code.setPlaceholderText("123456")
        self.code.setMaxLength(6)
        box.addWidget(label)
        box.addWidget(self.code)
        box.addStretch()
        self.stack.addWidget(page)

    def _build_vault(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.addWidget(QLabel("Choose the vault to sync:"))
        self.vaults = QListWidget()
        self.vaults.setMaximumHeight(170)
        box.addWidget(self.vaults)
        self.manual_vault = QLineEdit()
        self.manual_vault.setPlaceholderText(
            "or type a path on iCloud Drive, e.g. Obsidian/Obsidian")
        box.addWidget(self.manual_vault)
        self.stack.addWidget(page)

    def _build_folder(self):
        page = QWidget()
        box = QVBoxLayout(page)
        label = QLabel(
            "Where should the vault live on this computer?\n"
            "If the folder is empty, everything will be downloaded from iCloud.")
        label.setWordWrap(True)
        box.addWidget(label)
        row = QHBoxLayout()
        self.folder = QLineEdit(default_vault_path())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.folder)
        row.addWidget(browse)
        box.addLayout(row)
        box.addStretch()
        self.stack.addWidget(page)

    def _browse(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose a folder for the vault", self.folder.text())
        if chosen:
            self.folder.setText(chosen)

    # ── flow ────────────────────────────────────────

    def _set_busy(self, busy, text=""):
        self.busy.setVisible(busy)
        self.next_btn.setEnabled(not busy)
        self.message.setText(text)

    def _advance(self):
        page = self.stack.currentIndex()

        if page == PAGE_CREDENTIALS:
            self._email = self.email.text().strip()
            self._password = self.password.text()
            if not self._email or not self._password:
                self.message.setText("Enter both an Apple ID and a password.")
                return
            self._set_busy(True, "Signing in…")
            self.worker.sign_in(self._email, self._password)

        elif page == PAGE_TWOFA:
            code = self.code.text().strip()
            if not code:
                self.message.setText("Enter the code Apple sent you.")
                return
            self._set_busy(True, "Verifying code…")
            self.worker.submit_code(self._email, self._password, code)

        elif page == PAGE_VAULT:
            vault = (self.manual_vault.text().strip()
                     or (self.vaults.currentItem().text()
                         if self.vaults.currentItem() else ""))
            if not vault:
                self.message.setText("Select a vault, or type its path.")
                return
            self._vault_name = vault
            self.stack.setCurrentIndex(PAGE_FOLDER)
            self.next_btn.setText("Finish")
            self.message.setText("")

        elif page == PAGE_FOLDER:
            folder = self.folder.text().strip()
            if not folder:
                self.message.setText("Choose a folder.")
                return
            self._finish(folder)

    def _on_authenticated(self, api):
        self.api = api
        # Only persist once Apple has actually accepted the credentials.
        save_password(self._email, self._password)
        self._set_busy(True, "Looking for Obsidian vaults…")
        self.worker.find_vaults(api)

    def _on_twofa_needed(self, _api):
        self._set_busy(False)
        self.stack.setCurrentIndex(PAGE_TWOFA)
        self.message.setText("")
        self.code.setFocus()

    def _on_vaults(self, vaults):
        self._set_busy(False)
        self.vaults.clear()
        self.vaults.addItems(vaults)
        if vaults:
            self.vaults.setCurrentRow(0)
            self.message.setText("")
        else:
            self.message.setText(
                "No vaults were found automatically — type the path instead.")
        self.stack.setCurrentIndex(PAGE_VAULT)

    def _on_failed(self, reason):
        self._set_busy(False)
        self.message.setText(reason)

    def _finish(self, folder):
        cfg = config.load()
        cfg["apple_id"] = self._email
        cfg["vault_name"] = self._vault_name
        cfg["local_path"] = folder
        config.save(cfg)
        os.makedirs(folder, exist_ok=True)
        sync_engine.log("INFO", f"Setup complete — vault '{self._vault_name}'")
        self.accept()

    def result_config(self):
        return config.load()


class TwoFactorDialog(QDialog):
    """Asks for a code when a session expires mid-run."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("iCloud needs a code")
        layout = QVBoxLayout(self)
        label = QLabel(
            "Your iCloud session expired. Enter the six-digit code Apple sent "
            "to your devices to carry on syncing.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.code = QLineEdit()
        self.code.setPlaceholderText("123456")
        self.code.setMaxLength(6)
        layout.addWidget(self.code)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def value(self):
        return self.code.text().strip()
