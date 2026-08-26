"""New-note dialog (Ctrl+N): pick a type, name the file, create it empty."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)

from . import edit_backend, file_ops
from .theme import Theme

NOTE_TYPES: tuple[tuple[str, str], ...] = (
    ("Markdown 筆記 (.md)", ".md"),
    ("純文字筆記 (.txt)", ".txt"),
)


def normalized_file_name(name: str, suffix: str) -> str:
    """Append *suffix* unless the user already typed it (case-insensitive).

    A different extension is kept as part of the stem ("a.txt" + ".md" ->
    "a.txt.md"), matching how create_document strips only the matching one.
    """
    name = name.strip()
    if name.lower().endswith(suffix.lower()):
        name = name[: -len(suffix)].strip()
    return f"{name}{suffix}"


def validate_new_note(folder: Path, name: str, suffix: str) -> str:
    """Return a user-facing error message, or "" when the name is creatable."""
    stem = name.strip()
    if stem.lower().endswith(suffix.lower()):
        stem = stem[: -len(suffix)].strip()
    if not stem:
        return "請輸入檔名。"
    if not file_ops.is_valid_name(stem):
        return f"檔名不可包含 {file_ops.INVALID_NAME_CHARS} 或為保留名稱。"
    if (Path(folder) / normalized_file_name(name, suffix)).exists():
        return "已存在同名檔案，請改用其他檔名。"
    return ""


class NewNoteDialog(QDialog):
    """Modal dialog that creates one empty .md / .txt file in *folder*."""

    def __init__(
        self,
        folder: str | Path,
        theme: Theme,
        parent=None,
        *,
        default_backend: str = edit_backend.DEFAULT_BACKEND,
    ):
        super().__init__(parent)
        self._folder = Path(folder)
        self._created_path: Path | None = None

        self.setWindowTitle("新增筆記")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        type_row = QHBoxLayout()
        type_row.setSpacing(12)
        type_row.addWidget(QLabel("筆記類型："))
        self._type_buttons: list[tuple[QRadioButton, str]] = []
        for index, (label, suffix) in enumerate(NOTE_TYPES):
            button = QRadioButton(label)
            button.setChecked(index == 0)
            button.toggled.connect(self._revalidate)
            type_row.addWidget(button)
            self._type_buttons.append((button, suffix))
        type_row.addStretch()
        layout.addLayout(type_row)

        editor_row = QHBoxLayout()
        editor_row.setSpacing(8)
        editor_row.addWidget(QLabel("Markdown 編輯方式："))
        self._editor_backend_combo = QComboBox()
        self._editor_backend_combo.setObjectName("newNoteEditorBackend")
        self._editor_backend_combo.addItem(
            "原始 Markdown（純文字＋即時預覽，建議）",
            edit_backend.SOURCE_BACKEND,
        )
        self._editor_backend_combo.addItem(
            "Office 視覺編輯器", edit_backend.WYSIWYG_BACKEND
        )
        selected_backend = edit_backend.normalize_backend(default_backend)
        self._editor_backend_combo.setCurrentIndex(
            1 if selected_backend == edit_backend.WYSIWYG_BACKEND else 0
        )
        editor_row.addWidget(self._editor_backend_combo, 1)
        layout.addLayout(editor_row)

        self._editor_hint = QLabel(
            "兩種方式都建立標準 .md 純文字檔；原始模式直接編輯 Markdown，"
            "不經視覺格式轉換，Office 模式則提供視覺化編輯。"
        )
        self._editor_hint.setObjectName("newNoteEditorHint")
        self._editor_hint.setWordWrap(True)
        layout.addWidget(self._editor_hint)

        self._name_input = QLineEdit()
        self._name_input.setPlaceholderText("輸入檔名（可省略副檔名）")
        self._name_input.textChanged.connect(self._revalidate)
        self._name_input.returnPressed.connect(self._attempt_create)
        layout.addWidget(self._name_input)

        folder_row = QHBoxLayout()
        folder_row.setSpacing(8)
        self._folder_label = QLabel(f"建立於：{self._folder}")
        self._folder_label.setObjectName("newNoteFolder")
        self._folder_label.setWordWrap(True)
        folder_row.addWidget(self._folder_label, 1)
        self._browse_btn = QPushButton("瀏覽…")
        self._browse_btn.setObjectName("newNoteBrowse")
        self._browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(self._browse_btn, 0)
        layout.addLayout(folder_row)

        self._error_label = QLabel("")
        self._error_label.setObjectName("newNoteError")
        self._error_label.setWordWrap(True)
        layout.addWidget(self._error_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self._cancel_btn = QPushButton("取消")
        self._cancel_btn.clicked.connect(self.reject)
        self._create_btn = QPushButton("建立")
        self._create_btn.setDefault(True)
        self._create_btn.clicked.connect(self._attempt_create)
        button_row.addWidget(self._cancel_btn)
        button_row.addWidget(self._create_btn)
        layout.addLayout(button_row)

        self._apply_theme(theme)
        self._revalidate()
        self._name_input.setFocus()

    def _apply_theme(self, theme: Theme):
        self.setStyleSheet(
            f"QDialog {{ background: {theme.window}; }}"
            f"QLabel {{ color: {theme.text}; }}"
            f"QLabel#newNoteFolder {{ color: {theme.text_muted}; font-size: 12px; }}"
            f"QLabel#newNoteEditorHint {{ color: {theme.text_muted}; font-size: 12px; }}"
            f"QLabel#newNoteError {{ color: {theme.danger}; font-size: 12px; }}"
            f"QRadioButton {{ color: {theme.text}; }}"
            f"QLineEdit {{ background: {theme.surface}; border: 1px solid {theme.border};"
            f" border-radius: 6px; color: {theme.text}; padding: 6px 10px; font-size: 14px; }}"
            f"QLineEdit:focus {{ border-color: {theme.accent}; }}"
            f"QPushButton {{ background: {theme.surface}; border: 1px solid {theme.border};"
            f" border-radius: 6px; color: {theme.text}; padding: 6px 16px; }}"
            f"QPushButton:hover {{ background: {theme.surface_hover}; border-color: {theme.accent}; }}"
            f"QPushButton#newNoteBrowse {{ padding: 3px 10px; font-size: 12px; }}"
        )

    def _browse_folder(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "選擇建立位置", str(self._folder)
        )
        if chosen:
            self.set_folder(chosen)

    def set_folder(self, folder: str | Path):
        self._folder = Path(folder)
        self._folder_label.setText(f"建立於：{self._folder}")
        self._revalidate()

    def folder(self) -> Path:
        return self._folder

    def selected_suffix(self) -> str:
        for button, suffix in self._type_buttons:
            if button.isChecked():
                return suffix
        return NOTE_TYPES[0][1]

    def selected_editor_backend(self) -> str:
        """Return the requested editor; .txt always uses the source backend."""
        if self.selected_suffix().lower() != ".md":
            return edit_backend.SOURCE_BACKEND
        return edit_backend.normalize_backend(
            self._editor_backend_combo.currentData()
        )

    def target_path(self) -> Path:
        return self._folder / normalized_file_name(
            self._name_input.text(), self.selected_suffix()
        )

    def created_path(self) -> Path | None:
        return self._created_path

    def _revalidate(self, *_args) -> str:
        markdown_selected = self.selected_suffix().lower() == ".md"
        self._editor_backend_combo.setEnabled(markdown_selected)
        self._editor_hint.setEnabled(markdown_selected)
        error = validate_new_note(
            self._folder, self._name_input.text(), self.selected_suffix()
        )
        # An empty name shows no scary message while typing hasn't started;
        # the disabled 建立 button already conveys it.
        self._error_label.setText("" if error == "請輸入檔名。" else error)
        self._create_btn.setEnabled(not error)
        return error

    def _attempt_create(self):
        error = self._revalidate()
        if error:
            self._error_label.setText(error)
            return
        try:
            self._created_path = file_ops.create_document(
                self._folder, self._name_input.text(), self.selected_suffix()
            )
        except OSError as exc:
            # Keep the dialog open and the user's input intact.
            self._error_label.setText(f"無法建立檔案：{exc}")
            return
        self.accept()
