"""Selection translation orchestration delegated from MainWindow."""

from PySide6.QtCore import QSettings

from .settings_store import APP as _APP
from .settings_store import ORG as _ORG
from .translate import (
    DEEPL_KEY,
    PROVIDER_KEY,
    TARGET_KEY,
    cached_translation,
    normalize_provider,
    normalize_target,
    start_translation,
)
from .translate_dialog import TranslationDialog


def translate_selection(self, text: str):
    """Translate a right-click selection from any of the content views."""
    selection = (text or "").strip()
    if not selection:
        self.statusBar().showMessage("沒有選取任何文字", 3000)
        return
    settings = QSettings(_ORG, _APP)
    self._run_translation(
        selection,
        normalize_provider(settings.value(PROVIDER_KEY)),
        normalize_target(settings.value(TARGET_KEY)),
    )

def ensure_translate_dialog(self) -> TranslationDialog:
    if self._translate_dialog is None:
        dialog = TranslationDialog(self, theme=self._theme)
        dialog.retranslate_requested.connect(self._on_retranslate_requested)
        self._translate_dialog = dialog
    return self._translate_dialog

def run_translation(
    self, selection: str, provider: str, target: str, force: bool = False
):
    dialog = self._ensure_translate_dialog()
    dialog.start(selection, provider, target)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()

    # A repeat of the same request costs nothing and returns instantly.
    if not force:
        cached = cached_translation(selection, provider, target)
        if cached is not None:
            self._translate_request_id += 1  # invalidate anything in flight
            dialog.show_result(cached, provider, target, from_cache=True)
            return

    self._translate_request_id += 1
    request_id = self._translate_request_id
    start_translation(
        request_id,
        selection,
        provider=provider,
        target=target,
        api_key=str(QSettings(_ORG, _APP).value(DEEPL_KEY, "") or ""),
        on_finished=lambda rid, result: self._on_translation_done(
            rid, result, provider, target
        ),
        on_failed=self._on_translation_failed,
    )

def on_retranslate_requested(self, provider: str, target: str, force: bool):
    """The translation window's own service / language pickers."""
    dialog = self._translate_dialog
    if dialog is None or not dialog.source_text():
        return
    # Remember the choice so the next right-click uses it too.
    settings = QSettings(_ORG, _APP)
    settings.setValue(PROVIDER_KEY, provider)
    settings.setValue(TARGET_KEY, target)
    self._run_translation(dialog.source_text(), provider, target, force=force)

def on_translation_done(
    self, request_id: int, result: str, provider: str, target: str
):
    if request_id != self._translate_request_id:
        return  # superseded by a newer selection
    if self._translate_dialog is not None:
        self._translate_dialog.show_result(result, provider, target)

def on_translation_failed(self, request_id: int, message: str):
    if request_id != self._translate_request_id:
        return
    if self._translate_dialog is not None:
        self._translate_dialog.show_error(message)
