"""Chromium checks for deferred graphs, failure recovery and complete exports."""

import json
import os
import time
from html import escape
from pathlib import Path
from types import SimpleNamespace

import fitz
import pytest
from PySide6.QtCore import QEventLoop, Qt, QTimer, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QPushButton, QWidget

from app import edit_backend, export_actions
from app.md_converter import convert_text
from app.renderer import RendererView

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="set RUN_WEBENGINE_TESTS=1 for real Chromium diagram checks",
)


def _evaluate(view, script):
    result = []
    loop = QEventLoop()
    view.page().runJavaScript(script, lambda value: (result.append(value), loop.quit()))
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    assert result, "JavaScript callback timed out"
    return result[0]


def _until(predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        QTest.qWait(30)
    pytest.fail("The diagram state did not become ready")


def _fake_page(view, sources):
    script = (Path(__file__).parents[1] / "assets" / "diagram_loading.js").read_text("utf-8")
    graphs = '<div style="height:2500px"></div>'.join(
        f'<pre class="mermaid" data-diagram="{escape(source, quote=True)}">'
        f'{escape(source)}</pre>' for source in sources
    )
    fake = """
      window.calls=[]; window.active=0; window.maximum=0; window.fail=true;
      window.mermaid={initialize:function(config){window.graphTheme=config.theme;},
        run:function(options){
          var node=options.nodes[0], source=node.textContent;
          window.calls.push(source); window.active++;
          window.maximum=Math.max(window.maximum,window.active);
          return new Promise(function(resolve,reject){setTimeout(function(){
            window.active--;
            if(source==='BROKEN' && window.fail) reject(new Error('invalid graph'));
            else {node.innerHTML='<svg width="200" height="100"></svg>';resolve();}
          },80);});
        }};
    """
    view.resize(800, 600)
    view.show()
    view.setHtml(f"<html><head></head><body>{graphs}<script>{fake}{script}</script></body></html>")
    _until(lambda: _evaluate(view, "!!window.mdDiagrams"))


def test_nearby_graphs_first_and_theme_change_keeps_offscreen_pending(qapp):
    view = QWebEngineView()
    _fake_page(view, ["FIRST", "SECOND", "LAST"])
    _until(lambda: _evaluate(view, "window.mdDiagrams.status().rendered") == 1)
    assert json.loads(_evaluate(view, "JSON.stringify(window.calls)")) == ["FIRST"]
    _evaluate(view, "window.mdDiagrams.setTheme('dark')")
    _until(lambda: _evaluate(view, "window.calls.length") == 2)
    _until(lambda: _evaluate(view, "window.mdDiagrams.status().rendered") == 1)
    assert _evaluate(view, "window.graphTheme") == "dark"
    assert _evaluate(view, "window.mdDiagrams.status().pending") == 2
    _evaluate(view, "document.querySelectorAll('.diagram-block')[1].scrollIntoView()")
    _until(lambda: _evaluate(view, "window.mdDiagrams.status().rendered") == 2)
    assert _evaluate(view, "window.mdDiagrams.status().pending") == 1
    _evaluate(view, "window.mdDiagrams.ensureAll().then(()=>window.allFinished=true)")
    _until(lambda: _evaluate(view, "window.allFinished===true"))
    assert _evaluate(view, "window.mdDiagrams.status().rendered") == 3
    assert _evaluate(view, "window.maximum") == 1
    view.close()


def test_large_graph_requires_activation_and_failure_can_retry(qapp):
    view = QWebEngineView()
    large = "graph TD\n" + "%% Complete source line\n" * 200
    _fake_page(view, [large, "BROKEN"])
    QTest.qWait(200)
    assert _evaluate(view, "window.calls.length") == 0
    assert _evaluate(view, "document.querySelector('.mermaid').textContent") == large
    _evaluate(view, "document.querySelector('.diagram-controls button').focus()")
    QTest.keyClick(view.focusProxy(), Qt.Key.Key_Return)
    _until(lambda: _evaluate(view, "window.mdDiagrams.status().rendered") == 1)
    assert _evaluate(view, "document.activeElement.classList.contains('mermaid')")
    _evaluate(view, "window.mdDiagrams.ensureAll().then(()=>window.allFinished=true)")
    _until(lambda: _evaluate(view, "window.allFinished===true"))
    assert _evaluate(view, "window.mdDiagrams.status().failed") == 1
    assert _evaluate(view, "document.querySelectorAll('.mermaid')[1].textContent") == "BROKEN"
    _evaluate(view, """window.fail=false;
        document.querySelectorAll('.diagram-controls button')[1].click()""")
    _until(lambda: _evaluate(view, "window.mdDiagrams.status().rendered") == 2)
    assert _evaluate(view, "window.maximum") == 1
    view.close()


def _document():
    return (
        "# Diagram document\n\n```mermaid\nflowchart TD\nA[FIRSTGRAPH]-->B[Start]\n```\n\n" +
        "Paragraph between diagrams.\n\n" * 55 +
        "```mermaid\nflowchart LR\nC[LASTGRAPH]-->D[Complete]\n```\n\n" +
        "```mermaid\nTHIS IS INVALID MERMAID SOURCE\n```\n"
    )


def test_real_reader_pdf_forces_every_graph_and_preserves_invalid_source(qapp, tmp_path):
    view = RendererView()
    view.resize(900, 600)
    view.show()
    source = tmp_path / "graphs.md"
    source.write_text(_document(), "utf-8")
    view.load_file(source)
    _until(lambda: _evaluate(view, "window.mdDiagrams && window.mdDiagrams.status().rendered") == 1)
    assert _evaluate(view, "window.mdDiagrams.status().pending") == 2
    output = tmp_path / "graphs.pdf"
    finished = []
    started = time.monotonic()
    view.export_pdf(output, lambda path, ok: finished.append((path, ok)))
    _until(lambda: bool(finished))
    assert finished == [(str(output), True)]
    assert time.monotonic() - started < 15
    with fitz.open(output) as pdf:
        text = "\n".join(page.get_text() for page in pdf)
    assert "FIRSTGRAPH" in text and "LASTGRAPH" in text
    assert "THIS IS INVALID MERMAID SOURCE" in text
    assert _evaluate(view, "window.mdDiagrams.status().complete") is True
    view.close()


@pytest.mark.parametrize("kind", ["html", "single"])
def test_source_exports_force_all_graphs_and_terminate_on_invalid_graph(
    qapp, tmp_path, monkeypatch, kind,
):
    source = tmp_path / "draft.md"
    source.write_text("Saved source", "utf-8")
    owner = QWidget()
    owner.current_file, owner.edit_mode, owner.exporting = source, True, False
    owner._active_edit_backend = edit_backend.SOURCE_BACKEND
    owner.wysiwyg_view = owner.pdf_progress = None
    owner._export_btn = QPushButton(owner)
    owner._editor = SimpleNamespace(toPlainText=_document)
    owner.renderer = SimpleNamespace(width=lambda: 900)
    owner.statusBar = lambda: SimpleNamespace(showMessage=lambda *args: None)
    owner._refresh_icons = lambda: None
    done = []
    monkeypatch.setattr(export_actions, "show_pdf_progress", lambda *args: None)
    monkeypatch.setattr(export_actions, "show_export_complete", lambda *args: done.append(True))
    monkeypatch.setattr(export_actions, "on_pdf_exported",
                        lambda _owner, _path, ok: done.append(ok))
    output = tmp_path / ("graphs.html" if kind == "html" else "graphs-single.pdf")
    setup = None if kind == "html" else {"size": "single", "orientation": "portrait"}
    started = time.monotonic()
    export_actions._start_source_snapshot_export(owner, str(output), setup)
    _until(lambda: bool(done))
    assert done == [True] and time.monotonic() - started < 15
    if kind == "html":
        text = output.read_text("utf-8")
        assert text.count('data-diagram-state="ready"') == 2
        assert 'data-diagram-state="error"' in text
    else:
        with fitz.open(output) as pdf:
            assert pdf.page_count == 1
            text = pdf[0].get_text()
    assert "FIRSTGRAPH" in text and "LASTGRAPH" in text
    assert "THIS IS INVALID MERMAID SOURCE" in text
    assert source.read_text("utf-8") == "Saved source"
    owner.close()


def test_converter_output_still_renders_a_real_graph(qapp, tmp_path):
    view = QWebEngineView()
    html, _ = convert_text("```mermaid\nsequenceDiagram\nAlice->>Bob: Hello\n```", "light")
    view.resize(800, 600)
    view.show()
    view.setHtml(html, QUrl.fromLocalFile(str(tmp_path) + "/"))
    _until(lambda: _evaluate(view, "window.mdDiagrams && window.mdDiagrams.status().complete"))
    assert _evaluate(view, "window.mdDiagrams.status().rendered") == 1
    assert _evaluate(view, "document.querySelector('.mermaid svg').textContent.includes('Hello')")
    view.close()


@pytest.mark.parametrize("operation", ["pdf", "measure"])
def test_replacing_document_cancels_pending_graph_export(qapp, tmp_path, operation):
    view = RendererView()
    _fake_page(view, ["%% Large diagram\n" * 200])
    assert _evaluate(view, "window.mdDiagrams.status().pending") == 1
    done = []
    output = tmp_path / "stale.pdf"
    if operation == "pdf":
        view.export_pdf(output, lambda _path, ok: done.append(ok))
    else:
        view.content_size(done.append)
    view.render_html("<html><body>Replacement document</body></html>")
    _until(lambda: bool(done))
    assert done == ([False] if operation == "pdf" else [None])
    assert not output.exists()
    view.close()


def test_office_fragment_stays_eager_and_invalid_source_finishes_promptly(qapp):
    from PySide6.QtGui import QImage

    from app.fragment_render import FragmentRenderer

    fragment = FragmentRenderer()
    try:
        image_path = fragment.provide("mermaid", "flowchart LR\nA[Complete]-->B[Fragment]")
        assert image_path is not None
        image = QImage(str(image_path))
        assert not image.isNull() and image.width() > 100 and image.height() > 20
        started = time.monotonic()
        assert fragment.provide("mermaid", "THIS IS INVALID MERMAID SOURCE") is None
        assert time.monotonic() - started < 5
    finally:
        fragment.cleanup()
