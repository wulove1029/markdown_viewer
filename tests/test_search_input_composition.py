"""Search waits for committed text and leaves IME navigation to Qt."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QInputMethodEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.global_search import GlobalSearchView


def test_preedit_pauses_pending_search_and_keeps_candidate_keys(qapp, monkeypatch):
    view = GlobalSearchView(lambda: [], lambda *_args: None)
    launched = []
    monkeypatch.setattr(view, "_launch_task", lambda task: launched.append(task.query))
    view._input.setText("文")
    QApplication.sendEvent(view._input, QInputMethodEvent("ㄗ", []))
    assert view._composing
    assert not view._debounce.isActive()
    key = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier)
    assert view.eventFilter(view._input, key) is False
    view._input.returnPressed.emit()
    QTest.qWait(180)
    assert launched == []
    commit = QInputMethodEvent("", [])
    commit.setCommitString("字")
    QApplication.sendEvent(view._input, commit)
    QTest.qWait(190)
    assert launched == ["文字"]
    view.close()
    view.deleteLater()


def test_cancelled_composition_resumes_waiting_committed_query(qapp, monkeypatch):
    view = GlobalSearchView(lambda: [], lambda *_args: None)
    launched = []
    monkeypatch.setattr(view, "_launch_task", lambda task: launched.append(task.query))
    view._input.setText("中文")
    QApplication.sendEvent(view._input, QInputMethodEvent("ㄧ", []))
    QApplication.sendEvent(view._input, QInputMethodEvent("", []))
    QTest.qWait(190)
    assert launched == ["中文"]
    view.close()
    view.deleteLater()


def test_latest_query_debounces_and_enter_dispatches_immediately(qapp, monkeypatch):
    view = GlobalSearchView(lambda: [], lambda *_args: None)
    launched = []
    monkeypatch.setattr(view, "_launch_task", lambda task: launched.append(task.query))
    for query in ("a", "ab", "abc"):
        view._input.setText(query)
    QTest.qWait(190)
    assert launched == ["abc"]
    view._input.setText("other")
    view._input.returnPressed.emit()
    assert launched == ["abc", "other"]
    view._input.clear()
    QTest.qWait(190)
    assert launched == ["abc", "other"]
    view.close()
    view.deleteLater()
