"""Preview link clicks must not change the page from inside Chromium's
navigation callback (work package F2).

``acceptNavigationRequest`` runs inside Chromium's navigation throttle.  Opening
another document from there (wiki-link -> ``_open_file`` -> ``deactivate`` ->
``stop()``) tripped a Chromium CHECK and killed the process with 0x80000003,
for real mouse clicks and scripted clicks alike.

The unit test below runs everywhere; the real-window probes need Chromium and
are opt-in like the other WebEngine suites (``RUN_WEBENGINE_TESTS=1``).  They run
in a subprocess so that a native crash fails the test instead of the session.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtTest import QTest
from PySide6.QtWebEngineCore import QWebEnginePage

from app import renderer

_REPO = Path(__file__).resolve().parent.parent
_CLICK = QWebEnginePage.NavigationType.NavigationTypeLinkClicked

_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="headless WebEngine is flaky; set RUN_WEBENGINE_TESTS=1 to run",
)


class _PreviewView(QObject):
    wikilink_clicked = Signal(str)
    home_action_requested = Signal(str)
    local_doc_clicked = Signal(str)
    local_doc_target_clicked = Signal(str, str)

    def __init__(self, base_url=None):
        super().__init__()
        self._current_path = "C:/notes/a.md"
        self._html_base_url = base_url
        self.scroll_calls: list[str] = []

    def scroll_to(self, anchor):
        self.scroll_calls.append(anchor)


def test_navigation_callback_only_decides_and_defers_every_side_effect(qapp, monkeypatch):
    view = _PreviewView(base_url=QUrl.fromLocalFile("C:/notes/"))
    page = renderer._DocumentPage(view)
    depth: list[bool] = []
    original = renderer._DocumentPage.acceptNavigationRequest

    def tracking(self, *args):
        depth.append(True)
        try:
            return original(self, *args)
        finally:
            depth.pop()

    monkeypatch.setattr(renderer._DocumentPage, "acceptNavigationRequest", tracking)
    events: list[tuple] = []

    def record(name):
        return lambda *args: events.append((name, bool(depth), *[
            a.toString() if isinstance(a, QUrl) else a for a in args]))

    view.wikilink_clicked.connect(record("wikilink"))
    view.local_doc_clicked.connect(record("doc"))
    view.local_doc_target_clicked.connect(record("doc#"))
    view.home_action_requested.connect(record("home"))
    monkeypatch.setattr(view, "scroll_to", record("scroll"))
    monkeypatch.setattr(renderer.QDesktopServices, "openUrl", record("os"))
    monkeypatch.setattr(renderer.QMessageBox, "warning", record("warning"))

    route = "https://markdown-viewer.invalid/"
    for target in (
        route + "wikilink/Target",
        route + "wikilink/%E9%A0%81%23%E6%A8%99%E9%A1%8C",   # [[頁#標題]]
        route + "wikilink/%23%E6%A8%99%E9%A1%8C",            # [[#標題]]
        "wikilink:Legacy",
        "file:///C:/notes/b.md",
        "file:///C:/notes/b.md#part",
        "file:///C:/notes/#section",
        "https://example.com/",
        "file://attacker.example/share/x.md",
    ):
        assert page.acceptNavigationRequest(QUrl(target), _CLICK, True) is False, target
    view._current_path = None
    assert page.acceptNavigationRequest(QUrl(route + "home/new"), _CLICK, True) is False

    assert events == [], "a side effect ran inside the navigation callback"
    QTest.qWait(30)
    assert all(inside is False for _name, inside, *_rest in events)
    warning = next(event for event in events if event[0] == "warning")
    assert warning[2] is view and warning[3] == "已封鎖網路路徑連結"
    assert "attacker.example" in warning[4]
    assert [(name, *rest) for name, _inside, *rest in events if name != "warning"] == [
        ("wikilink", "Target"),
        ("wikilink", "頁#標題"),
        ("wikilink", "#標題"),
        ("wikilink", "Legacy"),
        ("doc", "C:/notes/b.md"),
        ("doc#", "C:/notes/b.md", "part"),
        ("scroll", "section"),
        ("os", "https://example.com/"),
        ("home", "new"),
    ]
    assert [name for name, *_rest in events][-2:] == ["warning", "home"]


# --------------------------------------------------------------- real window
_PROBE = r'''
import faulthandler
import json
import os
import sys
import time
from pathlib import Path

faulthandler.enable()
work = Path(sys.argv[1])
how = sys.argv[2]
work.mkdir(parents=True, exist_ok=True)

import PySide6.QtCore as QtCore

_Base = QtCore.QSettings
_INI = str(work / "settings.ini")


class _IsolatedSettings(_Base):
    """Every QSettings the app opens lands in one temporary INI file."""

    def __init__(self, *args, **kwargs):
        if len(args) >= 2 and isinstance(args[0], str) and isinstance(args[1], _Base.Format):
            super().__init__(*args, **kwargs)
        else:
            super().__init__(_INI, _Base.Format.IniFormat)


QtCore.QSettings = _IsolatedSettings
_DATA = work / "appdata"


def _writable_location(location):
    path = _DATA / getattr(location, "name", str(location))
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


QtCore.QStandardPaths.writableLocation = staticmethod(_writable_location)
_settings = _IsolatedSettings()
_settings.setValue("update_check_enabled", False)
_settings.sync()

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.url_schemes import register_document_schemes

register_document_schemes()
app = QApplication([])
app.setOrganizationName("mdv-test-link-click")
app.setApplicationName("MDV-Test-Link-Click")
from app import window as window_mod

window_mod.MainWindow._check_updates_silent = lambda self: None
os_opened = []
QDesktopServices.openUrl = staticmethod(lambda url: os_opened.append(url.toString()) or True)


def pump(seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def until(predicate, seconds=20.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def run_js(view, script):
    box = []
    view.page().runJavaScript(script, 0, box.append)
    until(lambda: bool(box), 8.0)
    return box[0] if box else None


docs = work / "docs"
docs.mkdir(exist_ok=True)
filler = "".join(f"填充段落 {n}。\n\n" for n in range(150))
# Enough text after the heading for scrollIntoView to bring it to the top.
tail = "".join(f"結尾段落 {n}。\n\n" for n in range(80))
CASES = [
    # name, link template ({} = target note name), opens the other note, scrolls to heading
    ("wikilink", "[[{}]]", True, False),
    ("wikilink-heading", "[[{}#目標標題]]", True, True),
    ("md-link", "[另一頁]({}.md)", True, False),
    ("same-note-heading", "[[#目標標題]]", False, True),
]
HEADING_TOP = ("(() => { const h = Array.from(document.querySelectorAll('h2'))"
               ".find(e => e.textContent.includes('目標標題'));"
               " return h ? h.getBoundingClientRect().top : null; })()")

w = window_mod.MainWindow()
w.resize(1200, 800)
w.show()
pump(0.8)
view = w._renderer


def showing(path):
    current = w._current_file
    return (current is not None and Path(current).resolve() == path.resolve()
            and view._loaded_markdown_generation == view._render_generation)


def click(selector):
    if how == "js":
        run_js(view, "document.querySelector(" + json.dumps(selector) + ").click(); true")
        return
    rect = json.loads(run_js(view, (
        "JSON.stringify((() => { const r = document.querySelector(" + json.dumps(selector)
        + ").getBoundingClientRect(); return [r.left + Math.min(8, r.width / 2),"
        " r.top + r.height / 2]; })())")))
    zoom = view.zoomFactor()
    QTest.mouseClick(view.focusProxy() or view, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier,
                     QPoint(round(rect[0] * zoom), round(rect[1] * zoom)))


for index, (name, template, other_note, heading) in enumerate(CASES):
    target = docs / f"另一頁{index}.md"
    target.write_text(f"# 另一頁 {index}\n\n" + filler + "## 目標標題\n\n" + tail,
                      encoding="utf-8")
    source = docs / f"來源{index}.md"
    source.write_text(f"# 來源 {index}\n\n連結：{template.format(target.stem)}\n\n"
                      + filler + "## 目標標題\n\n" + tail, encoding="utf-8")
    w.open_path(str(source))
    assert until(lambda: showing(source), 30), name + ": source never loaded"
    pump(0.6)
    expected = target if other_note else source
    selector = "a.wikilink" if template.startswith("[[") else 'a[href$=".md"]'
    assert run_js(view, "!!document.querySelector(" + json.dumps(selector) + ")") is True, name
    print("CLICK " + name, flush=True)
    click(selector)
    if other_note:
        opened = until(lambda: showing(expected), 20)
    else:
        opened = showing(expected)
    top = None
    if heading:
        until(lambda: (lambda t: isinstance(t, (int, float)) and -2 <= t <= 80)(
            run_js(view, HEADING_TOP)), 12)
        top = run_js(view, HEADING_TOP)
    pump(0.3)
    print("RESULT " + json.dumps({
        "case": name, "opened": bool(opened), "heading": heading, "heading_top": top,
        "current": str(w._current_file)}, ensure_ascii=False), flush=True)

print("OS " + json.dumps(os_opened, ensure_ascii=False), flush=True)
w._editor.document().setModified(False)
w.close()
pump(0.5)
print("ALL-DONE", flush=True)
'''

_CASES = ["wikilink", "wikilink-heading", "md-link", "same-note-heading"]


@_webengine
@pytest.mark.parametrize("how", ["mouse", "js"])
def test_note_link_clicks_in_the_real_window_open_the_target_without_crashing(tmp_path, how):
    script = tmp_path / "link_click_probe.py"
    script.write_text(_PROBE, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(_REPO))
    env.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script), str(tmp_path / "work"), how],
        cwd=_REPO, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300,
    )
    report = f"exit {proc.returncode:#x}\n{proc.stdout}\n{proc.stderr[-6000:]}"
    assert proc.returncode == 0, report
    results = [json.loads(line[len("RESULT "):]) for line in proc.stdout.splitlines()
               if line.startswith("RESULT ")]
    assert [r["case"] for r in results] == _CASES, report
    for result in results:
        assert result["opened"], report
        if result["heading"]:
            top = result["heading_top"]
            assert isinstance(top, (int, float)) and -2 <= top <= 80, report
    assert 'OS []' in proc.stdout, report
    assert "ALL-DONE" in proc.stdout, report
