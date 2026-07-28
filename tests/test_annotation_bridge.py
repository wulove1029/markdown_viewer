import json

from app.annotation_bridge import AnnotationBridge


def test_add_emits_added(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.added.connect(got.append)
    bridge.add('{"id":"abc","exact":"x"}')
    assert got == ['{"id":"abc","exact":"x"}']


def test_remove_emits_removed(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.removed.connect(got.append)
    bridge.remove("abc")
    assert got == ["abc"]


def test_report_orphans_parses_json(qapp):
    bridge = AnnotationBridge()
    got = []
    bridge.orphansReported.connect(got.append)
    bridge.reportOrphans(json.dumps(["a", "b"]))
    assert got == [["a", "b"]]


def test_inline_edit_slots_report_unavailable_without_handlers(qapp):
    bridge = AnnotationBridge()

    assert json.loads(bridge.inlineEditFetch(0, 1)) == {
        "ok": False, "error": "unavailable"
    }
    assert json.loads(bridge.inlineEditCommit(0, 1, "a", "b"))["ok"] is False
    assert json.loads(bridge.inlineEditPasteImage())["ok"] is False


def test_inline_edit_slots_forward_arguments_and_serialize_the_reply(qapp):
    calls = []
    bridge = AnnotationBridge()
    bridge.set_inline_edit_handlers(
        fetch=lambda start, end: calls.append(("fetch", start, end))
        or {"ok": True, "text": "line"},
        commit=lambda start, end, original, new: calls.append(
            ("commit", start, end, original, new)
        )
        or {"ok": True},
        paste_image=lambda: {"ok": True, "link": "![](assets/a.png)"},
    )

    assert json.loads(bridge.inlineEditFetch(2, 5)) == {"ok": True, "text": "line"}
    assert json.loads(bridge.inlineEditCommit(2, 5, "old", "new")) == {"ok": True}
    assert json.loads(bridge.inlineEditPasteImage())["link"] == "![](assets/a.png)"
    assert calls == [("fetch", 2, 5), ("commit", 2, 5, "old", "new")]


def test_inline_edit_reply_keeps_non_ascii_text_readable(qapp):
    bridge = AnnotationBridge()
    bridge.set_inline_edit_handlers(fetch=lambda s, e: {"ok": True, "text": "中文"})

    assert json.loads(bridge.inlineEditFetch(0, 0))["text"] == "中文"


def test_inline_edit_handler_exception_answers_instead_of_hanging_the_page(qapp):
    def boom(start, end):
        raise RuntimeError("disk on fire")

    bridge = AnnotationBridge()
    bridge.set_inline_edit_handlers(fetch=boom)

    reply = json.loads(bridge.inlineEditFetch(0, 0))
    assert reply["ok"] is False
    assert "disk on fire" in reply["error"]


import os
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from app.annotations import Annotation
from app.renderer import RendererView

# These end-to-end tests construct a real QWebEngineView. Headless Chromium is
# flaky in CI/sandboxes (a render crash hard-aborts the whole pytest run), so
# they are opt-in. Run them on a machine with a working display via:
#   RUN_WEBENGINE_TESTS=1 python -m pytest tests/test_annotation_bridge.py
_skip_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="headless WebEngine is flaky; set RUN_WEBENGINE_TESTS=1 to run",
)


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _eval(view, js):
    box = {}
    loop = QEventLoop()
    def cb(v):
        box["v"] = v
        loop.quit()
    view.page().runJavaScript(js, cb)
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    return box.get("v")


@_skip_webengine
def test_stored_annotation_renders_mark(qapp, tmp_path):
    md = tmp_path / "d.md"
    md.write_text("The quick brown fox jumps over the lazy dog.", encoding="utf-8")
    ann = Annotation.new(exact="brown fox", prefix="quick ", suffix=" jumps",
                         textPosition=10)
    view = RendererView()
    view.resize(700, 500)
    view.set_annotations([ann.to_dict()])
    view.load_file(md)
    _wait(4000)
    count = _eval(view, "document.querySelectorAll('mark.annot').length")
    assert count == 1


@_skip_webengine
def test_orphan_annotation_not_rendered(qapp, tmp_path):
    md = tmp_path / "d.md"
    md.write_text("nothing matches here", encoding="utf-8")
    ann = Annotation.new(exact="absent phrase", textPosition=0)
    view = RendererView()
    view.resize(700, 500)
    view.set_annotations([ann.to_dict()])
    view.load_file(md)
    _wait(4000)
    count = _eval(view, "document.querySelectorAll('mark.annot').length")
    assert count == 0
