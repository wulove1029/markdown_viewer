from app import translation_flow
from app import window as window_module
from tests import test_editor_data_safety as safety

_isolated_editor_dependencies = safety._isolated_editor_dependencies
make_data_safety_window = safety.make_data_safety_window


def test_cached_translation_updates_real_dialog_without_network(
    make_data_safety_window, monkeypatch,
):
    window = make_data_safety_window()
    monkeypatch.setattr(translation_flow, "QSettings", window_module.QSettings)
    monkeypatch.setattr(translation_flow, "cached_translation", lambda *args: "cached result")
    calls = []
    monkeypatch.setattr(translation_flow, "start_translation", lambda *a, **k: calls.append(a))
    window._translate_selection("  selected text  ")
    assert window._translate_dialog.source_text() == "selected text"
    assert window._translate_dialog._result.toPlainText() == "cached result"
    assert window._translate_dialog.isVisible()
    assert calls == []


def test_old_network_reply_cannot_replace_new_selection(make_data_safety_window, monkeypatch):
    window = make_data_safety_window()
    monkeypatch.setattr(translation_flow, "QSettings", window_module.QSettings)
    monkeypatch.setattr(translation_flow, "cached_translation", lambda *args: None)
    requests = []
    monkeypatch.setattr(translation_flow, "start_translation",
                        lambda request, text, **kwargs: requests.append((request, kwargs)))
    window._run_translation("first", "google", "zh-TW")
    window._run_translation("second", "google", "zh-TW")
    first, second = requests
    first[1]["on_finished"](first[0], "stale")
    assert window._translate_dialog._result.toPlainText() == ""
    second[1]["on_finished"](second[0], "current")
    assert window._translate_dialog._result.toPlainText() == "current"
