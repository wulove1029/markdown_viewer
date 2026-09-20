from html import escape

import pytest
from PySide6.QtCore import QTimer

from app import fragment_render as module


@pytest.fixture
def renderer():
    # Exercise orchestration without starting Chromium in unit tests.
    instance = object.__new__(module.FragmentRenderer)
    instance._cache, instance._fails = {}, {}
    instance._busy = False
    return instance


def test_transient_fragment_failure_retries_then_caches_success(renderer, monkeypatch):
    attempts = []
    monkeypatch.setattr(module, "pymupdf", object())

    def render(kind, source):
        attempts.append(source)
        if len(attempts) == 1:
            raise RuntimeError("cold renderer")
        return "fragment.png"

    monkeypatch.setattr(renderer, "_render_one", render)
    assert renderer.provide("math", "x") is None
    assert not renderer._busy
    assert renderer.provide("math", "x") == "fragment.png"
    assert renderer.provide("math", "x") == "fragment.png"
    assert len(attempts) == 2


def test_permanent_failure_is_bounded_and_missing_backend_degrades(renderer, monkeypatch):
    attempts = []
    monkeypatch.setattr(module, "pymupdf", object())
    monkeypatch.setattr(renderer, "_render_one", lambda *args: attempts.append(args))
    for _ in range(4):
        assert renderer.provide("mermaid", "bad") is None
    assert len(attempts) == 2
    monkeypatch.setattr(module, "pymupdf", None)
    assert renderer.provide("math", "new") is None
    assert len(attempts) == 2


@pytest.mark.parametrize("kind,selector", [("mermaid", ".mermaid svg"), ("math", ".katex")])
def test_fragment_source_is_escaped(renderer, kind, selector):
    source = '<img src=x onerror="alert(1)">&'
    html, actual = renderer._build_html(kind, source)
    assert actual == selector
    assert source not in html
    assert escape(source, quote=True) in html


def test_watchdog_runs_teardown_and_ignores_late_completion(qapp, renderer):
    calls = []
    callbacks = []

    def arm(finish, teardown):
        callbacks.append(finish)
        teardown(lambda: calls.append("stopped"))

    assert renderer._run(arm, 5) is None
    callbacks[0]("late")
    assert calls == ["stopped"]
    assert renderer._run(lambda finish, _: QTimer.singleShot(0, lambda: finish("ok")), 50) == "ok"
