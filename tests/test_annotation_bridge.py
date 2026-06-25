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


import tempfile
from pathlib import Path

from PyQt6.QtCore import QEventLoop, QTimer

from app.annotations import Annotation
from app.renderer import RendererView


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
