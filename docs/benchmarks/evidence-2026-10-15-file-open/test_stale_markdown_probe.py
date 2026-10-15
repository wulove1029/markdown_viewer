"""Controlled late-result probe; no Chromium and no app source modifications.

Runs real MainWindow switching and RendererView generation/ready methods.
Only the render dispatch and browser page are replaced with a delayed queue
and a recording page. This verifies acceptance of late callbacks, not visual
paint timing or the real worker scheduler.
"""
from pathlib import Path
import json
import threading

import pytest

from app import window as window_mod
from app.renderer import RendererView
from tests.conftest import qapp
from tests.test_editor_workspace_integration import (
    _isolated_workspace_dependencies,
    make_workspace_window,
)
from tests.test_window_integration import _FakePanel, _FakeRenderer, _Noop


class RecordingToc(_Noop):
    def __init__(self):
        self.headings = []
        self.outline = []
        self.events = []

    def update_headings(self, headings):
        self.headings = list(headings)
        self.events.append(('headings', list(headings)))

    def update_outline(self, outline):
        self.headings = []
        self.outline = list(outline)
        self.events.append(('outline', list(outline)))


class RecordingPanel(_FakePanel):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.toc = RecordingToc()


class DelayedRenderer(_FakeRenderer):
    load_file = RendererView.load_file
    _next_render_generation = RendererView._next_render_generation
    _stale_render = RendererView._stale_render
    _on_file_render_ready = RendererView._on_file_render_ready

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._render_generation = 0
        self._render_cancel = threading.Event()
        self._pending_export = None
        self._current_path = None
        self._partial_restore = None
        self._pending_scroll = None
        self._pending_scroll_generation = None
        self.pending = []
        self.delivered_html = []

    def show_loading(self, path):
        self.loading_path = Path(path)

    def _finish_load_file(self, path, generation):
        self.pending.append((generation, Path(path), self._render_cancel))

    def page(self):
        return self

    def setHtml(self, html, base_url=None):
        self.delivered_html.append(html)


@pytest.mark.parametrize('target_kind', ['text', 'pdf', 'markdown'])
def test_delayed_markdown_completion_after_switch(
    target_kind, make_workspace_window, tmp_path, monkeypatch
):
    monkeypatch.setattr(window_mod, 'RendererView', DelayedRenderer)
    monkeypatch.setattr(window_mod, 'LeftPanel', RecordingPanel)
    first = tmp_path / 'source.md'
    first.write_text('# Old Markdown\n', encoding='utf-8')
    suffix = {'text': '.txt', 'pdf': '.pdf', 'markdown': '.md'}[target_kind]
    target = tmp_path / ('target' + suffix)
    target.write_text('New target\n', encoding='utf-8')
    window = make_workspace_window()
    window.open_path(str(first))
    renderer = window._renderer
    generation, source, cancellation = renderer.pending[0]
    window.open_path(str(target))
    assert window._current_kind == target_kind
    assert window._current_file == target
    toc_before = list(window._panel.toc.headings)
    old_headings = [(1, 'OLD RESULT', 'old-result')]
    stale_before_delivery = renderer._stale_render(generation, source)
    renderer._on_file_render_ready(
        generation, source, '<html><body>OLD RESULT</body></html>', old_headings
    )
    accepted = window._panel.toc.headings == old_headings
    result = {
        'target_kind': target_kind,
        'window_current_kind': window._current_kind,
        'renderer_current_path': renderer._current_path.name,
        'old_render_cancelled': cancellation.is_set(),
        'old_result_considered_stale': stale_before_delivery,
        'toc_before': toc_before,
        'toc_after': window._panel.toc.headings,
        'late_old_headings_accepted': accepted,
        'scope': 'controlled callback; real MainWindow and RendererView methods; browser/PDF/panel are fakes',
    }
    print(json.dumps(result, ensure_ascii=False))
    assert accepted is (target_kind != 'markdown')
    assert cancellation.is_set() is (target_kind == 'markdown')
