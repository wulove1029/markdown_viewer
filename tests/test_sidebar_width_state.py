"""Remember explicit sidebar drags without persisting temporary clamping."""

import pytest
from PySide6.QtTest import QTest

from app import session_state
from app.theme import PANEL_WIDTH
from tests.test_window_integration import (  # noqa: F401
    _clean_settings,
    _isolated_recovery_store,
    _window_fakes,
    make_window,
)


def settings():
    return session_state.QSettings("markdown-viewer", "MarkdownViewer")


def show(window):
    # Match the real LeftPanel constraints while retaining unrelated test fakes.
    window._panel.setMinimumWidth(180)
    window._panel.setMaximumWidth(420)
    window.resize(1200, 750)
    window.show()
    QTest.qWait(5)


@pytest.mark.parametrize("raw", [None, "invalid", 0, -100, "nan", True, 100_000])
def test_legacy_or_invalid_sidebar_width_uses_default(make_window, raw):  # noqa: F811
    if raw is not None:
        settings().setValue(session_state.SIDEBAR_WIDTH_KEY, raw)
    window = make_window()
    show(window)
    assert window._splitter.sizes()[0] == PANEL_WIDTH


def test_user_drag_survives_collapse_focus_and_restart(make_window):  # noqa: F811
    window = make_window()
    show(window)
    window._splitter.moveSplitter(370, 1)
    assert window._splitter.sizes()[0] == 370
    assert int(settings().value(session_state.SIDEBAR_WIDTH_KEY)) == 370
    window._do_toggle(0)
    assert window._panel.isHidden()
    window._do_toggle(0)
    assert window._splitter.sizes()[0] == 370
    window._workspace_controls.toggle_focus()
    assert window._panel.isHidden()
    window._workspace_controls.toggle_focus()
    QTest.qWait(5)
    assert window._splitter.sizes()[0] == 370
    window.close()
    restored = make_window()
    show(restored)
    assert restored._splitter.sizes()[0] == 370


def test_narrow_layout_does_not_overwrite_preferred_width(make_window):  # noqa: F811
    settings().setValue(session_state.SIDEBAR_WIDTH_KEY, 410)
    window = make_window()
    show(window)
    window._splitter.setFixedWidth(600)
    QTest.qWait(5)
    assert 180 <= window._splitter.sizes()[0] <= 276
    assert window._sidebar_width_state.preferred == 410
    window._do_toggle(0)
    window._do_toggle(0)
    assert window._splitter.sizes()[0] <= 276
    window.close()
    assert int(settings().value(session_state.SIDEBAR_WIDTH_KEY)) == 410
    restored = make_window()
    show(restored)
    assert restored._splitter.sizes()[0] == 410


def test_drag_collapsed_to_zero_keeps_last_expanded_width(make_window):  # noqa: F811
    window = make_window()
    show(window)
    window._splitter.moveSplitter(350, 1)
    window._splitter.moveSplitter(0, 1)
    assert window._splitter.sizes()[0] == 0
    assert int(settings().value(session_state.SIDEBAR_WIDTH_KEY)) == 350
    window._do_toggle(0)
    window._do_toggle(0)
    assert window._splitter.sizes()[0] == 350


def test_detached_window_does_not_replace_primary_preference(make_window):  # noqa: F811
    settings().setValue(session_state.SIDEBAR_WIDTH_KEY, 300)
    window = make_window()
    window._is_detached = True
    show(window)
    window._splitter.moveSplitter(400, 1)
    assert window._sidebar_width_state.preferred == 400
    window.close()
    assert int(settings().value(session_state.SIDEBAR_WIDTH_KEY)) == 300
