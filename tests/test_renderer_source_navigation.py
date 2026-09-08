"""Source-block navigation, stale-request guards, and real rendered behavior."""

import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from app.renderer import RendererView, _source_line_reveal_js


def renderer_stub():
    scripts = []
    find_callbacks = []
    page = SimpleNamespace(
        runJavaScript=lambda script: scripts.append(script),
        findText=lambda _text, _flags, callback: find_callbacks.append(callback),
    )
    stub = SimpleNamespace(
        _render_generation=4, _loaded_markdown_generation=4,
        _source_line_reveal=None, _pending_find=None,
        _pending_scroll=None, _pending_scroll_generation=None,
        _current_path=Path("note.md"),
        _spy_timer=SimpleNamespace(start=lambda: None, stop=lambda: None),
        page=lambda: page,
    )
    stub._apply_source_line_reveal = lambda request: RendererView._apply_source_line_reveal(stub, request)
    stub._cancel_source_line_reveal = lambda: RendererView._cancel_source_line_reveal(stub)
    return stub, scripts, find_callbacks


def test_reveal_restores_block_after_prior_find_completion_and_can_be_cancelled():
    stub, scripts, callbacks = renderer_stub()
    RendererView.find_text(stub, "needle")
    RendererView.reveal_source_line_after_load(stub, 99)
    assert len(scripts) == 0
    callbacks[0](None)
    callbacks[1](None)
    assert len(scripts) == 2
    assert "var line = 98" in scripts[-1]
    RendererView.cancel_pending_find(stub)
    callbacks[0](None)
    assert len(scripts) == 3
    assert scripts[-1] == "window.__mdSourceRevealRequest = null;"


def test_reveal_waits_for_matching_generation_and_new_render_cancels_it():
    stub, scripts, _callbacks = renderer_stub()
    stub._loaded_markdown_generation = None
    RendererView.reveal_source_line_after_load(stub, 30)
    assert not scripts
    RendererView._on_markdown_load_checked(stub, 4, stub._current_path, None)
    assert not scripts
    RendererView._on_markdown_load_checked(stub, 4, stub._current_path, 3)
    assert not scripts
    RendererView._on_markdown_load_checked(stub, 4, stub._current_path, 4)
    assert len(scripts) == 1
    request = stub._source_line_reveal
    RendererView._next_render_generation(stub)
    RendererView._apply_source_line_reveal(stub, request)
    assert len(scripts) == 1
    assert stub._source_line_reveal is None


_real_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="set RUN_WEBENGINE_TESTS=1 to execute real Chromium navigation",
)


def evaluate(view, script):
    result = {}
    loop = QEventLoop()

    def complete(value):
        result["value"] = value
        loop.quit()

    view.page().runJavaScript(script, complete)
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    assert "value" in result
    return result["value"]


def wait_until(qapp, condition):
    deadline = time.monotonic() + 8
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()
    assert condition()


@_real_webengine
def test_real_preview_reveals_requested_duplicate_match_before_and_after_load(qapp, tmp_path):
    note = tmp_path / "long.md"
    note.write_text("\n\n".join(f"needle paragraph {index}" for index in range(100)), encoding="utf-8")
    view = RendererView()
    view.resize(800, 600)
    view.show()
    view.load_file(note)
    view.reveal_source_line_after_load(161)
    wait_until(qapp, lambda: view._loaded_markdown_generation == view._render_generation)

    def target_visible():
        return evaluate(view, """(function(){
            var r=document.querySelector('[data-src-start="160"]').getBoundingClientRect();
            return r.top >= 0 && r.bottom <= window.innerHeight;
        })()""")

    wait_until(qapp, target_visible)
    evaluate(view, "window.scrollTo(0,0)")
    view.find_text("needle")
    view.reveal_source_line_after_load(161)
    wait_until(qapp, target_visible)
    # Flush a later event-loop turn to include the in-flight findText callback.
    loop = QEventLoop()
    QTimer.singleShot(150, loop.quit)
    loop.exec()
    assert target_visible(), evaluate(view, "JSON.stringify([window.scrollY,document.querySelector('[data-src-start=\"160\"]').getBoundingClientRect().top])")
    assert evaluate(view, "window.scrollY") > 1000
    view.close()
    view.deleteLater()
    qapp.processEvents()


@_real_webengine
def test_real_source_navigation_selects_smallest_block_and_rejects_wrong_generation(qapp, tmp_path):
    note = tmp_path / "source.md"
    note.write_text("# source", encoding="utf-8")
    view = RendererView()
    view.resize(800, 600)
    view.show()
    view.load_file(note)
    wait_until(qapp, lambda: view._loaded_markdown_generation == view._render_generation)
    generation = view._render_generation
    evaluate(view, """document.body.innerHTML = '<div style="height:1200px"></div>' +
      '<section id="outer" data-src-start="2" data-src-end="30" style="height:1000px">' +
      '<div style="height:700px"></div><p id="inner" data-src-start="10" data-src-end="12">target</p>' +
      '</section><div style="height:1200px"></div>';
      window.__revealed = [];
      Element.prototype.scrollIntoView = function(){ window.__revealed.push(this.id); };
    """)
    assert evaluate(view, _source_line_reveal_js(generation + 1, 12)) is False
    assert evaluate(view, _source_line_reveal_js(generation, 12)) is True
    wait_until(qapp, lambda: json.loads(evaluate(view, "JSON.stringify(window.__revealed)")) == ["inner"])
    assert evaluate(view, _source_line_reveal_js(generation, 99)) is False
    view.close()
    view.deleteLater()
    qapp.processEvents()


@_real_webengine
def test_pending_recovery_page_has_no_home_navigation(qapp, tmp_path):
    view = RendererView()
    view.show_pending_recovery(tmp_path / "draft.txt")
    wait_until(qapp, lambda: evaluate(view, "document.body.innerText").find("待復原草稿") >= 0)
    body = evaluate(view, "document.body.innerText")
    assert "draft.txt" in body
    assert "繼續編輯、另存副本或稍後處理" in body
    assert evaluate(view, "document.querySelectorAll('.home-actions').length") == 0
    assert view._current_path is None
    view.close()
    view.deleteLater()
    qapp.processEvents()
