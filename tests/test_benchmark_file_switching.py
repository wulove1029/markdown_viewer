"""Prevent a wrong page/document from being reported as successful paint."""

from tools.benchmark_file_switching import matching_raster, summary


def test_full_markdown_probe_waits_for_completion_and_its_own_frames(qapp):
    import os
    import time

    import pytest

    if os.environ.get("RUN_WEBENGINE_TESTS") != "1":
        pytest.skip("set RUN_WEBENGINE_TESTS=1 for real Chromium")
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWebEngineWidgets import QWebEngineView

    from tests.test_reading_style_webengine import _evaluate
    from tools.benchmark_file_switching import markdown_ready_script

    view = QWebEngineView()
    loaded = []
    view.loadFinished.connect(loaded.append)
    view.show()
    try:
        view.setHtml('<meta name="markdown-viewer-render-generation" content="7">'
                     '<h2>A section</h2>')
        deadline = time.monotonic() + 10
        while not loaded and time.monotonic() < deadline:
            qapp.processEvents()
            QTest.qWait(10)
        assert loaded == [True]
        first = markdown_ready_script(7, "A section")
        full = markdown_ready_script(7, "A section", require_full=True)
        _evaluate(view, "Object.defineProperty(document,'readyState',"
                        "{configurable:true,value:'loading'});"
                        "window.__mdvBenchmarkFrame='7:false:false';")
        assert _evaluate(view, first) is True
        _evaluate(view, "Object.defineProperty(document.body,'innerText',"
                        "{configurable:true,get(){window.__earlyTextRead=true;"
                        "return 'A section';}}); window.__earlyTextRead=false;")
        assert _evaluate(view, full) is False
        assert _evaluate(view, "window.__earlyTextRead") is False
        _evaluate(view, "delete document.body.innerText")
        _evaluate(view, "delete document.readyState")
        assert _evaluate(view, full) is False
        deadline = time.monotonic() + 5
        while not _evaluate(view, full) and time.monotonic() < deadline:
            QTest.qWait(20)
        assert _evaluate(view, full) is True
        _evaluate(view, "document.body.insertAdjacentHTML('beforeend',"
                        "'<div class=partial-preview-notice>Partial</div>')")
        assert _evaluate(view, full) is False
        _evaluate(view, "document.querySelector('.partial-preview-notice').remove()")
        assert _evaluate(view, markdown_ready_script(8, "A section", require_full=True)) is False
        assert _evaluate(view, markdown_ready_script(7, "B section", require_full=True)) is False
    finally:
        view.close()
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_raster_evidence_requires_path_generation_target_page_and_request_time():
    paints = [("A.pdf", 7, 5, 1.1), ("B.pdf", 8, 5, 1.2),
              ("A.pdf", 8, 0, 1.3), ("A.pdf", 8, 5, 0.9)]
    assert not matching_raster(paints, "A.pdf", 8, 5, 1.0)
    paints.append(("A.pdf", 8, 5, 1.4))
    assert matching_raster(paints, "A.pdf", 8, 5, 1.0)


def test_summary_uses_nearest_rank_p95_and_keeps_empty_samples_explicit():
    assert summary([]) == {"n": 0}
    stats = summary(list(range(1, 31)))
    assert stats == {"n": 30, "p50_ms": 15.5, "p95_ms": 29, "max_ms": 30}
