"""Exercise deferred syntax in real Chromium, including native find and PDF."""

import json
import os
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtTest import QTest

from app.code_loading import defer_code_highlighting
from tests.test_reading_style_webengine import _evaluate

pytestmark = pytest.mark.skipif(os.environ.get('RUN_WEBENGINE_TESTS') != '1',
                                reason='set RUN_WEBENGINE_TESTS=1 for real Chromium')


def wait_until(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        QTest.qWait(20)
    assert check()


@pytest.fixture
def code_view(qapp, tmp_path):
    from PySide6.QtWebEngineWidgets import QWebEngineView

    markup = '<span class="k">return</span> &lt;/script&gt;\n' * 110
    blocks = ''.join(f'<h2 id="section{i}">Section {i}</h2>'
                     f'<div class="highlight"><pre><span></span>\nUNIQUE_{i}\n{markup}</pre></div>'
                     for i in range(12))
    html = ('<html><head><meta charset="utf-8"><style>'
            'pre{font:14px/22px monospace;margin:0}.k{color:red}'
            '</style></head><body><!--' + 'x' * (2 * 1024 * 1024)
            + '-->' + blocks + '</body></html>')
    path = tmp_path / 'deferred.html'
    path.write_text(defer_code_highlighting(html), encoding='utf-8')
    view = QWebEngineView()
    view.resize(800, 600)
    loaded = []
    view.loadFinished.connect(loaded.append)
    view.show()
    view.load(QUrl.fromLocalFile(str(path)))
    wait_until(lambda: bool(loaded))
    assert loaded[-1]
    wait_until(lambda: _evaluate(view, 'document.querySelector("pre .k") !== null'))
    yield view
    view.close()
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_complete_text_copy_scroll_zoom_and_native_search(code_view):
    view = code_view
    assert _evaluate(view, 'window.mdCode.pending()') >= 10
    source = '\nUNIQUE_11\n' + 'return </script>\n' * 110
    assert _evaluate(view, 'document.querySelectorAll("pre")[11].innerText') == source
    # Native search must find text before that block has any syntax spans.
    _evaluate(view, 'window.mdCode.freeze()')
    results = []
    from PySide6.QtWebEngineCore import QWebEnginePage
    view.page().findText('UNIQUE_11', QWebEnginePage.FindFlag(0), results.append)
    wait_until(lambda: bool(results))
    assert results[-1]
    assert _evaluate(view, 'document.querySelectorAll("pre")[11].hasAttribute("data-md-code")')
    view.page().findText('')
    view.setZoomFactor(1.5)
    _evaluate(view, 'getSelection().removeAllRanges(); window.mdCode.resume(); '
                    'document.getElementById("section11").scrollIntoView()')
    wait_until(lambda: _evaluate(view,
               'document.querySelectorAll("pre")[11].querySelector(".k") !== null'))
    assert _evaluate(view, 'document.querySelectorAll("pre")[11].innerText') == source
    color = _evaluate(view, 'getComputedStyle('
                      'document.querySelectorAll("pre")[11].querySelector(".k")).color')
    assert color == 'rgb(255, 0, 0)'


def test_selection_annotation_and_edit_nodes_are_not_replaced(code_view):
    state = json.loads(_evaluate(code_view, '''JSON.stringify((function () {
      const p=document.querySelectorAll('pre');
      const node=p[5].lastChild;
      const range=document.createRange();range.setStart(node,1);
      range.setEnd(node,9);getSelection().removeAllRanges();getSelection().addRange(range);
      p[6].innerHTML='<mark class="annot">KEEP ANNOTATION</mark>';
      p[7].contentEditable='true';p[7].textContent='EDITED CONTENT';
      window.mdCode.ensureAll();
      return {selection:getSelection().toString(),annotation:p[6].innerText,edited:p[7].innerText,
        pending:window.mdCode.pending(),selectedPlain:p[5].hasAttribute('data-md-code')};
    })())'''))
    assert state == {'selection': 'UNIQUE_5', 'annotation': 'KEEP ANNOTATION',
                     'edited': 'EDITED CONTENT', 'pending': 3, 'selectedPlain': True}
    _evaluate(code_view, 'getSelection().removeAllRanges(); '
                         'document.getElementById("section5").scrollIntoView()')
    wait_until(lambda: _evaluate(code_view,
               'document.querySelectorAll("pre")[5].querySelector(".k") !== null'))
    assert _evaluate(code_view,
                     'document.querySelectorAll("pre")[6].innerText') == 'KEEP ANNOTATION'
    assert _evaluate(code_view, 'document.querySelectorAll("pre")[7].innerText') == 'EDITED CONTENT'


def test_print_restores_all_code_and_keeps_last_block(code_view, tmp_path):
    assert _evaluate(code_view, 'window.mdCode.pending()') > 0
    _evaluate(code_view, 'window.dispatchEvent(new Event("beforeprint"))')
    assert _evaluate(code_view, 'window.mdCode.pending()') == 0
    target = tmp_path / 'code.pdf'
    printed = []
    code_view.page().pdfPrintingFinished.connect(lambda *args: printed.append(args))
    code_view.page().printToPdf(str(target))
    wait_until(lambda: bool(printed), 30)
    assert printed[-1][1]
    import fitz
    with fitz.open(target) as doc:
        text = ''.join(page.get_text() for page in doc)
    assert 'UNIQUE_0' in text and 'UNIQUE_11' in text


@pytest.mark.parametrize('stored_annotation', [False, True])
def test_renderer_large_code_annotation_and_inline_edit(qapp, tmp_path, stored_annotation):
    from app.annotations import Annotation
    from app.renderer import RendererView
    from tests.test_inline_edit_webengine import _wire

    source = '# Large code\n\n' + ''.join(
        f'```python\n# UNIQUE_{i}\n' + 'value = 1\n' * 110 + '```\n\n'
        for i in range(12)) + '<!--' + 'x' * (2 * 1024 * 1024) + '-->\n'
    path = tmp_path / 'large.md'
    path.write_text(source, encoding='utf-8')
    view = RendererView()
    view.resize(800, 600)
    view.show()
    committed = _wire(view, path)
    annotation = Annotation.new(exact='UNIQUE_11', textPosition=0)
    if stored_annotation:
        view.set_annotations([annotation.to_dict()])
    view.load_file(path)
    wait_until(lambda: _evaluate(view, '!!window.__inlineEdit && !!window.mdCode'), 30)
    try:
        if stored_annotation:
            wait_until(lambda: _evaluate(view, 'document.querySelector("mark.annot") !== null'))
            assert _evaluate(view,
                             'document.querySelector("mark.annot").textContent') == 'UNIQUE_11'
            assert _evaluate(view, 'window.mdCode.pending()') == 0
        else:
            assert _evaluate(view, 'window.mdCode.pending()') > 0
            found = []
            view.find_text('UNIQUE_11', found.append)
            wait_until(lambda: bool(found))
            assert found[-1]
            view.find_next('UNIQUE_11')
            view.find_prev('UNIQUE_11')
            # Editing an offscreen plain block must still fetch its fenced source.
            _evaluate(view, '''document.querySelectorAll('.highlight')[5].dispatchEvent(
                new MouseEvent('click', {bubbles:true, detail:3}))''')
            wait_until(lambda: _evaluate(view,
                       'document.querySelector(".inline-edit textarea") !== null'))
            original = _evaluate(view, 'document.querySelector(".inline-edit textarea").value')
            assert original.startswith('```python\n# UNIQUE_5\n')
            replacement = json.dumps('```python\n# EDITED_CODE\n```')
            _evaluate(view, "(function(){const t=document.querySelector('.inline-edit textarea');"
                      + 't.value=' + replacement + ';'
                      + "t.dispatchEvent(new KeyboardEvent('keydown',"
                      "{key:'Enter',ctrlKey:true,bubbles:true}));})()")
            wait_until(lambda: bool(committed))
            edited = path.read_text(encoding='utf-8')
            assert '# EDITED_CODE' in edited and '# UNIQUE_5\n' not in edited
            assert '# UNIQUE_11\n' in edited
    finally:
        view.deactivate()
        view.close()


def test_large_html_snapshot_exports_all_syntax_without_runtime_payload(
    qapp, tmp_path, monkeypatch,
):
    from app import export_actions
    from tests.test_source_export import _Owner

    source = '# Export code\n\n' + ''.join(
        f'```python\n# UNIQUE_{i}\n' + 'value = 1\n' * 110 + '```\n\n'
        for i in range(12)) + '<!--' + 'x' * (2 * 1024 * 1024) + '-->\n'
    path = tmp_path / 'source.md'
    path.write_text('# Saved disk source', encoding='utf-8')
    owner = _Owner(path)
    owner._editor.toPlainText = lambda: source
    monkeypatch.setattr(export_actions, 'show_export_complete', lambda *_args: None)
    target = tmp_path / 'export.html'
    try:
        export_actions._start_source_snapshot_export(owner, str(target), None)
        wait_until(lambda: getattr(owner, '_source_export_job', None) is None, 30)
        html = target.read_text(encoding='utf-8')
        assert 'UNIQUE_11' in html and html.count('<span class=') > 1000
        assert 'data-md-code' not in html and 'md-code-payload' not in html
        assert path.read_text(encoding='utf-8') == '# Saved disk source'
    finally:
        job = getattr(owner, '_source_export_job', None)
        if job is not None:
            job._finish(False)
        owner.close()
        owner.deleteLater()
