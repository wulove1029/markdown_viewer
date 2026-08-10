"""Behaviour of the translation result window.

The risky part is the service/language pickers: setting them programmatically
must not look like the user asking for a re-run, or restoring a stored choice
would loop.
"""

import pytest

from app.theme import DARK, LIGHT
from app.translate_dialog import TranslationDialog


@pytest.fixture
def dialog(qapp):
    return TranslationDialog(None, theme=LIGHT)


# ── pending / result / error states ─────────────────────────────────────

def test_start_shows_source_and_pending_status(dialog):
    dialog.start("Hello world", "google", "zh-TW")

    assert dialog.source_text() == "Hello world"
    assert dialog._source.toPlainText() == "Hello world"
    assert dialog._result.toPlainText() == ""
    assert dialog._status.text().startswith("翻譯中…")
    assert "繁體中文" in dialog._status.text()


def test_start_disables_controls_while_pending(dialog):
    dialog.start("Hello world", "google", "zh-TW")

    assert not dialog._provider_combo.isEnabled()
    assert not dialog._target_combo.isEnabled()
    assert not dialog._retry_btn.isEnabled()
    assert not dialog._copy_btn.isEnabled()


def test_result_re_enables_controls(dialog):
    dialog.start("Hello world", "google", "zh-TW")
    dialog.show_result("你好世界", "google", "zh-TW")

    assert dialog._result.toPlainText() == "你好世界"
    assert dialog._provider_combo.isEnabled()
    assert dialog._retry_btn.isEnabled()
    assert dialog._copy_btn.isEnabled()
    assert "快取" not in dialog._status.text()


def test_cached_result_is_marked(dialog):
    dialog.start("Hello world", "google", "zh-TW")
    dialog.show_result("你好世界", "google", "zh-TW", from_cache=True)

    assert "快取" in dialog._status.text()


def test_error_clears_result_and_re_enables_retry(dialog):
    dialog.start("Hello world", "google", "zh-TW")
    dialog.show_error("翻譯服務逾時")

    assert dialog._result.toPlainText() == ""
    assert dialog._status.text().startswith("⚠")
    assert "逾時" in dialog._status.text()
    assert dialog._retry_btn.isEnabled(), "user must be able to retry after a failure"
    assert not dialog._copy_btn.isEnabled(), "nothing to copy"


# ── picker wiring ───────────────────────────────────────────────────────

def test_start_syncs_combos_without_requesting_a_rerun(dialog):
    """The guard that stops restoring a choice from looping."""
    emitted = []
    dialog.retranslate_requested.connect(
        lambda p, t, f: emitted.append((p, t, f))
    )

    dialog.start("Hello world", "mymemory", "ja")

    assert dialog.current_provider() == "mymemory"
    assert dialog.current_target() == "ja"
    assert emitted == [], "programmatic sync must not emit"


def test_changing_provider_requests_a_rerun_without_forcing(dialog):
    dialog.start("Hello world", "google", "zh-TW")
    dialog.show_result("你好世界", "google", "zh-TW")
    emitted = []
    dialog.retranslate_requested.connect(
        lambda p, t, f: emitted.append((p, t, f))
    )

    idx = dialog._provider_combo.findData("mymemory")
    dialog._provider_combo.setCurrentIndex(idx)

    assert emitted == [("mymemory", "zh-TW", False)]


def test_changing_target_requests_a_rerun(dialog):
    dialog.start("Hello world", "google", "zh-TW")
    dialog.show_result("你好世界", "google", "zh-TW")
    emitted = []
    dialog.retranslate_requested.connect(
        lambda p, t, f: emitted.append((p, t, f))
    )

    idx = dialog._target_combo.findData("ja")
    dialog._target_combo.setCurrentIndex(idx)

    assert emitted == [("google", "ja", False)]


def test_retry_button_forces_a_fresh_translation(dialog):
    dialog.start("Hello world", "google", "zh-TW")
    dialog.show_result("你好世界", "google", "zh-TW")
    emitted = []
    dialog.retranslate_requested.connect(
        lambda p, t, f: emitted.append((p, t, f))
    )

    dialog._retry_btn.click()

    assert emitted == [("google", "zh-TW", True)]


def test_no_rerun_before_anything_was_translated(dialog):
    emitted = []
    dialog.retranslate_requested.connect(
        lambda p, t, f: emitted.append((p, t, f))
    )

    idx = dialog._provider_combo.findData("mymemory")
    dialog._provider_combo.setCurrentIndex(idx)
    dialog._on_retry()

    assert emitted == [], "no source text yet, nothing to translate"


# ── misc ────────────────────────────────────────────────────────────────

def test_copy_puts_the_translation_on_the_clipboard(dialog, qapp):
    from PySide6.QtGui import QGuiApplication

    dialog.start("Hello world", "google", "zh-TW")
    dialog.show_result("你好世界", "google", "zh-TW")
    dialog._copy_btn.click()

    assert QGuiApplication.clipboard().text() == "你好世界"
    assert "複製" in dialog._status.text()


def test_apply_theme_does_not_lose_state(dialog):
    dialog.start("Hello world", "google", "zh-TW")
    dialog.show_result("你好世界", "google", "zh-TW")

    dialog.apply_theme(DARK)

    assert dialog._result.toPlainText() == "你好世界"
    assert dialog.current_provider() == "google"
    assert dialog.styleSheet() != ""
