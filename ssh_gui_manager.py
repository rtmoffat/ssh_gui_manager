#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

APP_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ssh-gui-manager"
APP_DIR.mkdir(parents=True, exist_ok=True)
DATA_FILE = APP_DIR / "profiles.json"


@dataclass
class SSHProfile:
    name: str
    host: str
    user: str = ""
    port: int = 22
    identity_file: str = ""
    jump_host: str = ""
    extra_args: str = ""  # e.g. "-A -o ServerAliveInterval=60"

    def display(self) -> str:
        who = f"{self.user}@" if self.user else ""
        port = f":{self.port}" if self.port and self.port != 22 else ""
        return f"{self.name}  —  {who}{self.host}{port}"


def load_profiles() -> List[SSHProfile]:
    if not DATA_FILE.exists():
        return []
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        out: List[SSHProfile] = []
        for item in data:
            out.append(
                SSHProfile(
                    name=item.get("name", "").strip(),
                    host=item.get("host", "").strip(),
                    user=item.get("user", "").strip(),
                    port=int(item.get("port", 22)),
                    identity_file=item.get("identity_file", "").strip(),
                    jump_host=item.get("jump_host", "").strip(),
                    extra_args=item.get("extra_args", "").strip(),
                )
            )
        # Drop empty/invalid names/hosts
        out = [p for p in out if p.name and p.host]
        return out
    except Exception:
        return []


def save_profiles(profiles: List[SSHProfile]) -> None:
    DATA_FILE.write_text(
        json.dumps([asdict(p) for p in profiles], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def pick_terminal_command(command_str: str) -> Optional[List[str]]:
    """
    Return argv for a terminal that runs command_str via a shell, or None if no terminal found.
    """
    candidates = [
        ("gnome-terminal", ["gnome-terminal", "--", "bash", "-lc", command_str]),
        ("konsole", ["konsole", "-e", "bash", "-lc", command_str]),
        ("xfce4-terminal", ["xfce4-terminal", "-e", f"bash -lc {shlex_quote(command_str)}"]),
        ("xterm", ["xterm", "-e", "bash", "-lc", command_str]),
        ("kitty", ["kitty", "bash", "-lc", command_str]),
        ("alacritty", ["alacritty", "-e", "bash", "-lc", command_str]),
        ("wezterm", ["wezterm", "start", "--", "bash", "-lc", command_str]),
    ]
    for exe, argv in candidates:
        if shutil.which(exe):
            return argv
    return None


def shlex_quote(s: str) -> str:
    # Minimal quoting to avoid extra dependency; good enough for this use.
    return "'" + s.replace("'", "'\"'\"'") + "'"


def build_ssh_command(p: SSHProfile) -> str:
    # Assemble: ssh [-p] [-i] [-J] [extra] user@host
    parts: List[str] = ["ssh"]

    if p.port:
        parts += ["-p", str(p.port)]

    if p.identity_file:
        parts += ["-i", p.identity_file]

    if p.jump_host:
        parts += ["-J", p.jump_host]

    if p.extra_args:
        # We intentionally pass extra args as raw text into the shell command
        # so users can use multiple -o options, -A, etc.
        parts += [p.extra_args]

    target = f"{p.user}@{p.host}" if p.user else p.host
    parts += [target]

    # Join for shell execution
    return " ".join(parts)


class ProfileDialog(QDialog):
    def __init__(self, parent=None, profile: Optional[SSHProfile] = None):
        super().__init__(parent)
        self.setWindowTitle("SSH Profile")
        self.profile = profile

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name = QLineEdit(profile.name if profile else "")
        self.host = QLineEdit(profile.host if profile else "")
        self.user = QLineEdit(profile.user if profile else "")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(profile.port if profile else 22)

        self.identity = QLineEdit(profile.identity_file if profile else "")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self.browse_identity)

        id_row = QHBoxLayout()
        id_row.addWidget(self.identity, 1)
        id_row.addWidget(browse_btn)

        self.jump = QLineEdit(profile.jump_host if profile else "")
        self.extra = QLineEdit(profile.extra_args if profile else "")

        form.addRow("Name", self.name)
        form.addRow("Host", self.host)
        form.addRow("User", self.user)
        form.addRow("Port", self.port)
        form.addRow("Identity file", id_row)
        form.addRow("Jump host (-J)", self.jump)
        form.addRow("Extra SSH args", self.extra)

        layout.addLayout(form)

        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("Save")
        cancel = QPushButton("Cancel")
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

    def browse_identity(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Identity File", str(Path.home()))
        if path:
            self.identity.setText(path)

    def get_profile(self) -> Optional[SSHProfile]:
        name = self.name.text().strip()
        host = self.host.text().strip()
        if not name or not host:
            QMessageBox.warning(self, "Missing fields", "Name and Host are required.")
            return None
        return SSHProfile(
            name=name,
            host=host,
            user=self.user.text().strip(),
            port=int(self.port.value()),
            identity_file=self.identity.text().strip(),
            jump_host=self.jump.text().strip(),
            extra_args=self.extra.text().strip(),
        )


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SSH GUI Manager")
        self.resize(900, 520)

        self.profiles: List[SSHProfile] = load_profiles()

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        # Top: search
        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to filter profiles…")
        self.search.textChanged.connect(self.refresh_list)
        search_row.addWidget(self.search, 1)
        outer.addLayout(search_row)

        splitter = QSplitter()
        outer.addWidget(splitter, 1)

        # Left: list
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.on_select)
        left_layout.addWidget(self.list, 1)

        list_btns = QHBoxLayout()
        self.add_btn = QPushButton("Add")
        self.edit_btn = QPushButton("Edit")
        self.del_btn = QPushButton("Delete")
        self.dup_btn = QPushButton("Duplicate")
        self.add_btn.clicked.connect(self.add_profile)
        self.edit_btn.clicked.connect(self.edit_profile)
        self.del_btn.clicked.connect(self.delete_profile)
        self.dup_btn.clicked.connect(self.duplicate_profile)
        list_btns.addWidget(self.add_btn)
        list_btns.addWidget(self.edit_btn)
        list_btns.addWidget(self.del_btn)
        list_btns.addWidget(self.dup_btn)
        left_layout.addLayout(list_btns)

        splitter.addWidget(left)

        # Right: details + connect
        right = QWidget()
        right_layout = QVBoxLayout(right)

        self.details = QLabel("Select a profile.")
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.details.setStyleSheet("QLabel { padding: 8px; }")
        right_layout.addWidget(self.details, 1)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.clicked.connect(self.connect_selected)
        bottom.addWidget(self.connect_btn)
        right_layout.addLayout(bottom)

        splitter.addWidget(right)
        splitter.setSizes([340, 560])

        self.refresh_list()
        self.on_select()

    def refresh_list(self):
        term = self.search.text().strip().lower()
        self.list.clear()

        for idx, p in enumerate(self.profiles):
            hay = " ".join([p.name, p.host, p.user, p.jump_host, p.extra_args]).lower()
            if term and term not in hay:
                continue
            item = QListWidgetItem(p.display())
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.list.addItem(item)

        if self.list.count() > 0 and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)

    def selected_profile(self) -> Optional[SSHProfile]:
        item = self.list.currentItem()
        if not item:
            return None
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None:
            return None
        try:
            return self.profiles[int(idx)]
        except Exception:
            return None

    def on_select(self, *_):
        p = self.selected_profile()
        if not p:
            self.details.setText("Select a profile.")
            self.connect_btn.setEnabled(False)
            self.edit_btn.setEnabled(False)
            self.del_btn.setEnabled(False)
            self.dup_btn.setEnabled(False)
            return

        self.connect_btn.setEnabled(True)
        self.edit_btn.setEnabled(True)
        self.del_btn.setEnabled(True)
        self.dup_btn.setEnabled(True)

        cmd = build_ssh_command(p)
        self.details.setText(
            f"<b>{p.name}</b><br><br>"
            f"<b>Host:</b> {p.host}<br>"
            f"<b>User:</b> {p.user or '(none)'}<br>"
            f"<b>Port:</b> {p.port}<br>"
            f"<b>Identity:</b> {p.identity_file or '(none)'}<br>"
            f"<b>Jump:</b> {p.jump_host or '(none)'}<br>"
            f"<b>Extra args:</b> {p.extra_args or '(none)'}<br><br>"
            f"<b>Command:</b><br><code>{cmd}</code>"
        )

    def add_profile(self):
        dlg = ProfileDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            p = dlg.get_profile()
            if not p:
                return
            self.profiles.append(p)
            save_profiles(self.profiles)
            self.refresh_list()

    def edit_profile(self):
        p = self.selected_profile()
        if not p:
            return
        dlg = ProfileDialog(self, profile=p)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_profile()
            if not updated:
                return
            # Replace by matching name+host+user? No: replace the selected object in list
            # Find index by identity
            idx = self.profiles.index(p)
            self.profiles[idx] = updated
            save_profiles(self.profiles)
            self.refresh_list()

    def delete_profile(self):
        p = self.selected_profile()
        if not p:
            return
        res = QMessageBox.question(self, "Delete profile", f"Delete '{p.name}'?")
        if res == QMessageBox.StandardButton.Yes:
            self.profiles.remove(p)
            save_profiles(self.profiles)
            self.refresh_list()
            self.on_select()

    def duplicate_profile(self):
        p = self.selected_profile()
        if not p:
            return
        copy = SSHProfile(**asdict(p))
        copy.name = f"{copy.name} (copy)"
        self.profiles.append(copy)
        save_profiles(self.profiles)
        self.refresh_list()

    def connect_selected(self):
        p = self.selected_profile()
        if not p:
            return

        ssh_cmd = build_ssh_command(p) + "; echo; echo 'Session ended. Press Enter to close...'; read"
        term_argv = pick_terminal_command(ssh_cmd)
        if not term_argv:
            QMessageBox.critical(
                self,
                "No terminal found",
                "Could not find a supported terminal emulator.\n"
                "Install one of: gnome-terminal, konsole, xfce4-terminal, xterm, kitty, alacritty, wezterm.",
            )
            return

        try:
            subprocess.Popen(term_argv)
        except Exception as e:
            QMessageBox.critical(self, "Failed to launch", str(e))


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

