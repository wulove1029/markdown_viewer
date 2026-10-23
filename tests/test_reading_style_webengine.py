"""Real Chromium checks for reading layout, paper colors and large type."""

import json
import os
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from app.reading_style import ReadingPreferences, reading_css

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="set RUN_WEBENGINE_TESTS=1 for actual reading layout checks",
)


def _evaluate(view, script):
    result = {}
    loop = QEventLoop()

    def complete(value):
        result["value"] = value
        loop.quit()

    view.page().runJavaScript(script, complete)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    assert "value" in result, "Chromium evaluation timed out"
    return result["value"]


@pytest.mark.parametrize("theme,background", [
    ("light", "rgb(247, 240, 227)"), ("dark", "rgb(37, 34, 30)"),
])
def test_paper_large_type_and_wide_content_remain_readable(qapp, tmp_path, theme, background):
    from PySide6.QtWebEngineWidgets import QWebEngineView

    preferences = ReadingPreferences(
        preset="custom", font_size=28, width="custom", custom_width=600,
        spacing="custom", custom_line_height=1.7, paper="warm",
    )
    base = (Path(__file__).parents[1] / "assets" / "obsidian-light.css").read_text("utf-8")
    cells = "".join(f"<td>Wide column {i} unbrokenvalue1234567890</td>" for i in range(20))
    long_url = "https://example.test/" + "very-long-path/" * 40
    html = (
        f'<html class="theme-{theme}"><head><meta charset="utf-8">'
        f"<style>{base}\n{reading_css(preferences=preferences)}</style></head>"
        f'<body class="theme-{theme}"><h1>閱讀與思考 Reading</h1>'
        '<p id="sample">繁體中文與 English 文字，留下清晰的思路。</p>'
        f'<p><a href="{long_url}">{long_url}</a></p>'
        f"<table><tbody><tr>{cells}</tr></tbody></table>"
        f'<div class="highlight"><pre>{"sample_code = " * 80}</pre></div>'
        '<blockquote>記錄想法，整理知識。</blockquote></body></html>'
    )
    view = QWebEngineView()
    view.resize(760, 600)
    loaded = []
    view.loadFinished.connect(loaded.append)
    view.show()
    view.setHtml(html)
    deadline = time.monotonic() + 10
    while not loaded and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert loaded and loaded[-1]
    values = json.loads(_evaluate(view, """JSON.stringify((function () {
      const body = getComputedStyle(document.body);
      const table = document.querySelector('table');
      const pre = document.querySelector('pre');
      return {
        font: body.fontSize, height: body.lineHeight, background: body.backgroundColor,
        htmlBackground: getComputedStyle(document.documentElement).backgroundColor,
        viewport: window.innerWidth, page: document.documentElement.scrollWidth,
        tableWidth: table.clientWidth, tableScroll: table.scrollWidth,
        codeWidth: pre.clientWidth, codeScroll: pre.scrollWidth,
        text: document.getElementById('sample').textContent
      };
    })())"""))
    assert values["font"] == "28px"
    assert float(values["height"].removesuffix("px")) == pytest.approx(47.6)
    assert values["background"] == background
    assert values["htmlBackground"] == background
    assert values["page"] <= values["viewport"]
    assert values["tableScroll"] > values["tableWidth"]
    assert values["codeScroll"] > values["codeWidth"]
    assert values["text"] == "繁體中文與 English 文字，留下清晰的思路。"
    view.grab().save(str(tmp_path / f"reading-{theme}.png"))
    view.close()


def test_image_overlay_keyboard_long_image_and_focus_restore(qapp, tmp_path):
    import base64

    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtTest import QTest
    from PySide6.QtWebEngineWidgets import QWebEngineView

    assets = Path(__file__).parents[1] / "assets"
    # The Windows offscreen platform may start without any system font faces.
    for font in ("msjh.ttc", "msjhbd.ttc", "segoeui.ttf"):
        font_path = Path("C:/Windows/Fonts") / font
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    css = (assets / "obsidian-light.css").read_text("utf-8")
    script = (assets / "reading_media.js").read_text("utf-8")
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="5000">'
           '<rect width="1200" height="5000" fill="#3377bb"/></svg>')
    url = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
    view = QWebEngineView()
    view.resize(760, 600)
    loaded = []
    view.loadFinished.connect(loaded.append)
    view.show()
    view.setHtml(
        f'<html><head><style>{css}</style></head><body><h1>Image reading</h1>'
        f'<img id="photo" src="{url}" alt="Long diagram">'
        f'<a href="#destination"><img id="linked" src="{url}"></a>'
        f'<script>{script}</script></body></html>'
    )
    deadline = time.monotonic() + 10
    while not loaded and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert loaded and loaded[-1]
    assert json.loads(_evaluate(view, """JSON.stringify([
        document.activeElement.tagName, window.scrollY,
        document.querySelector('#linked').hasAttribute('tabindex')])""")) == ["BODY", 0, False]
    _evaluate(view, """document.querySelector('#photo').focus({preventScroll:true});
        document.querySelector('#photo').dispatchEvent(
            new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));""")
    values = json.loads(_evaluate(view, """JSON.stringify((function() {
      const stage = document.querySelector('.reading-media-stage');
      const image = stage.querySelector('img');
      return {open: document.querySelector('dialog').open,
        focused: document.activeElement.textContent,
        width: image.clientWidth, natural: image.naturalWidth, stage:stage.clientHeight,
        height: stage.scrollHeight, locked:document.body.style.overflow};
    })())"""))
    assert values["open"] is True and values["focused"] == "關閉"
    assert values["height"] > values["stage"] * 2
    assert values["width"] < values["natural"]
    assert values["locked"] == "hidden"
    QTest.qWait(200)
    view.grab().save(str(tmp_path / "image-overlay.png"))
    _evaluate(view, "document.querySelector('.reading-media-toolbar button').click()")
    assert _evaluate(view, "document.querySelector('.reading-media-stage img').clientWidth") == 1200
    assert _evaluate(view, """document.querySelector('.reading-media-stage').scrollWidth >
        document.querySelector('.reading-media-stage').clientWidth""") is True
    # Native keyboard dispatch exercises the browser's modal focus containment.
    QTest.keyClick(view.focusProxy(), Qt.Key.Key_Tab)
    QTest.qWait(40)
    assert _evaluate(view, "document.querySelector('dialog').contains(document.activeElement)")
    QTest.keyClick(view.focusProxy(), Qt.Key.Key_Escape)
    QTest.qWait(40)
    assert json.loads(_evaluate(view, """JSON.stringify([!!document.querySelector('dialog'),
        document.activeElement.id, document.body.style.overflow,
        window.scrollY])""")) == [False, "photo", "", 0]
    _evaluate(view, """document.querySelector('#photo').click();
        document.querySelector('.reading-media-stage').click()""")
    assert _evaluate(view, "!!document.querySelector('dialog')") is False
    view.close()
