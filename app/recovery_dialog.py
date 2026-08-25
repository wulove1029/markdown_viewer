"""Side-by-side review dialog for an unsaved recovery snapshot."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class RecoveryDialog(QDialog):
    """Let the user compare disk and draft text before choosing either one."""

    RESTORE = "restore"
    DISCARD = "discard"
    LATER = "later"

    def __init__(
        self,
        source_path: str | Path,
        disk_text: str,
        draft_text: str,
        updated_at: str,
        parent=None,
    ):
        super().__init__(parent)
        self.choice = self.LATER
        self.setWindowTitle("復原未儲存草稿")
        self.resize(980, 620)

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                f"{Path(source_path).name} 有未儲存的復原草稿（{updated_at}）。\n"
                "左側是磁碟版本，右側是復原草稿；原始檔不會在這一步被覆寫。"
            )
        )

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.disk_editor = self._pane("磁碟版本", disk_text)
        self.draft_editor = self._pane("復原草稿", draft_text)
        splitter.addWidget(self.disk_editor.parentWidget())
        splitter.addWidget(self.draft_editor.parentWidget())
        splitter.setSizes([480, 480])
        layout.addWidget(splitter, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        later = QPushButton("稍後")
        discard = QPushButton("使用磁碟版本")
        restore = QPushButton("復原草稿")
        restore.setDefault(True)
        later.clicked.connect(self._choose_later)
        discard.clicked.connect(self._choose_discard)
        restore.clicked.connect(self._choose_restore)
        buttons.addWidget(later)
        buttons.addWidget(discard)
        buttons.addWidget(restore)
        layout.addLayout(buttons)

    def _pane(self, title: str, text: str) -> QPlainTextEdit:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(title)
        editor = QPlainTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText(text)
        editor.setAccessibleName(title)
        layout.addWidget(label)
        layout.addWidget(editor, 1)
        return editor

    def _choose_restore(self):
        self.choice = self.RESTORE
        self.accept()

    def _choose_discard(self):
        self.choice = self.DISCARD
        self.accept()

    def _choose_later(self):
        self.choice = self.LATER
        self.reject()
