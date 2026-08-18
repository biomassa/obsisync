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
    QButtonGroup, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QMessageBox, QProgressBar,
    QPushButton, QRadioButton, QStackedWidget, QVBoxLayout, QWidget,
)

import config
import sync_engine
from auth import (
    TwoFactorRequired, authenticate, discover_vaults, get_password, save_password,
)
from paths import default_vault_path

PAGE_CREDENTIALS, PAGE_TWOFA, PAGE_VAULT, PAGE_FOLDER, PAGE_FIRST_SYNC = range(5)


class _Worker(QObject):
    """Runs the blocking parts of setup and reports back with signals."""

    authenticated = Signal(object)      # api
    twoFactorNeeded = Signal(object)    # a delivery notice, or None
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
        except TwoFactorRequired as exc:
            # Credentials were accepted and only the code is missing. The
            # message carries where Apple sent it, or why it could not.
            self.twoFactorNeeded.emit(str(exc) if "sent" in str(exc).lower() else None)
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
        self._build_first_sync()

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

        # An existing iObsi install already holds everything needed, including a
        # populated sync database — importing it avoids signing in again AND
        # avoids the first-run reconciliation entirely.
        import migrate
        self._migrate_info = migrate.describe() if migrate.available() else None
        if self._migrate_info:
            tracked = self._migrate_info.get("tracked")
            summary = QLabel(
                "An existing <b>iObsi</b> installation was found"
                + (f" tracking {tracked} files" if tracked else "")
                + ". Importing it carries over your account, vault and sync "
                  "history, so there is nothing to sign in to or reconcile.")
            summary.setWordWrap(True)
            self.import_btn = QPushButton("Import from iObsi")
            self.import_btn.clicked.connect(self._do_import)
            form.addRow(summary)
            form.addRow("", self.import_btn)
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@example.com")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        form.addRow("Apple ID", self.email)
        form.addRow("Password", self.password)
        form.addRow(QLabel(""))          # absorb slack instead of stretching fields
        self.stack.addWidget(page)

    def _do_import(self):
        import migrate
        self._set_busy(True, "Importing from iObsi…")
        try:
            # force: launching the app creates an empty database at the
            # destination, and keeping that would defeat the whole point.
            result = migrate.import_from_iobsi(force=True)
        except Exception as exc:
            self._set_busy(False)
            self.message.setText(f"Import failed: {exc}")
            return
        self._set_busy(False)
        sync_engine.log("INFO", f"Imported from iObsi: {', '.join(result['copied'])}")
        QMessageBox.information(
            self, "Imported",
            "Settings and sync history were copied from iObsi.\n\n"
            "iObsi's own files were left untouched — do not run both against the "
            "same vault at the same time.")
        self.accept()

    def _build_twofa(self):
        page = QWidget()
        box = QVBoxLayout(page)
        label = QLabel("Enter the six-digit code Apple sent to your devices.")
        label.setWordWrap(True)
        self.twofa_label = label
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

    def _build_first_sync(self):
        page = QWidget()
        box = QVBoxLayout(page)
        self.first_sync_summary = QLabel()
        self.first_sync_summary.setWordWrap(True)
        box.addWidget(self.first_sync_summary)

        # There is no safe default here: the app cannot tell whether the local
        # copy or the iCloud copy is the one you want to keep.
        self.first_sync_group = QButtonGroup(page)
        self.mode_adopt = QRadioButton(
            "They already match \u2014 just start tracking them")
        self.mode_remote = QRadioButton(
            "Trust iCloud \u2014 replace differing local files")
        self.mode_local = QRadioButton(
            "Trust this computer \u2014 replace differing files on iCloud")
        self.mode_adopt.setChecked(True)
        for i, btn in enumerate((self.mode_adopt, self.mode_remote, self.mode_local)):
            self.first_sync_group.addButton(btn, i)
            box.addWidget(btn)

        note = QLabel(
            "The first option never transfers or overwrites anything. Files that "
            "differ are listed as conflicts for you to resolve afterwards.")
        note.setWordWrap(True)
        from gui.pages import make_secondary
        make_secondary(note)
        box.addWidget(note)
        box.addStretch()
        self.stack.addWidget(page)

    def first_sync_mode(self):
        if self.mode_remote.isChecked():
            return "prefer-remote"
        if self.mode_local.isChecked():
            return "prefer-local"
        return "adopt"

    @staticmethod
    def folder_has_vault(folder):
        """True if the folder already holds files we would have to reconcile."""
        if not os.path.isdir(folder):
            return False
        for _root, _dirs, files in os.walk(folder):
            if any(not f.startswith(".") for f in files):
                return True
        return False

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
            if self.folder_has_vault(folder):
                self._folder = folder
                self.first_sync_summary.setText(
                    f"<b>{folder}</b> already contains files, and your iCloud vault "
                    "does too. Nothing is tracked yet, so obsisync cannot tell which "
                    "copy you want to keep. Choose how to start:")
                self.stack.setCurrentIndex(PAGE_FIRST_SYNC)
                self.next_btn.setText("Finish")
                self.message.setText("")
                return
            self._finish(folder)

        elif page == PAGE_FIRST_SYNC:
            self._finish(self._folder)

    def _on_authenticated(self, api):
        self.api = api
        # Only persist once Apple has actually accepted the credentials.
        save_password(self._email, self._password)
        self._set_busy(True, "Looking for Obsidian vaults…")
        self.worker.find_vaults(api)

    def _on_twofa_needed(self, notice):
        self._set_busy(False)
        self.stack.setCurrentIndex(PAGE_TWOFA)
        # Saying where the code went saves people hunting for it, and makes an
        # SMS fallback obvious when the trusted-device prompt did not arrive.
        if notice:
            self.twofa_label.setText(notice)
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
        # Consumed once by the first sync, then cleared.
        cfg["first_run_mode"] = (
            self.first_sync_mode() if self.folder_has_vault(folder) else "adopt")
        config.save(cfg)
        os.makedirs(folder, exist_ok=True)
        sync_engine.log("INFO", f"Setup complete — vault '{self._vault_name}'")
        self.accept()

    def result_config(self):
        return config.load()


class TwoFactorDialog(QDialog):
    """Asks for a code when a session expires mid-run."""

    def __init__(self, parent=None, notice=None):
        super().__init__(parent)
        self.setWindowTitle("iCloud needs a code")
        layout = QVBoxLayout(self)
        label = QLabel(
            (notice + "\n\n" if notice else "")
            + "Enter the six-digit code to carry on syncing.")
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
