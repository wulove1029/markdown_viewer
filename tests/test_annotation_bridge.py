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
