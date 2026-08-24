"""The preview's table grid editor, running in a real QWebEngineView.

Same opt-in gate as tests/test_inline_edit_webengine.py: headless Chromium can
hard-abort a whole pytest run, so these only execute with
``RUN_WEBENGINE_TESTS=1``.

The Node harnesses under tests/js drive both scripts against a hand-written DOM
stub, which by construction cannot answer the questions only a browser can:
whether ``contentEditable = "plaintext-only"`` is actually honoured, whether
``document.execCommand("insertText")`` really types into a cell, whether a real
``click()`` on the toolbar runs the handler, and whether table_edit.js and
inline_edit.js can share one page without either of them throwing.
"""

import json
import os

import pytest
from PySide6.QtCore import QEventLoop, QTimer

from app.inline_edit import extract_source_lines, replace_source_lines
from app.md_table import parse_table, serialize_table
from app.renderer import RendererView

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

    def done(value):
        box["v"] = value
        loop.quit()

    view.page().runJavaScript(js, done)
    QTimer.singleShot(4000, loop.quit)
    loop.exec()
    return box.get("v")


def _json(view, js):
    """Evaluate *js* and decode its JSON string result."""
    return json.loads(_eval(view, "JSON.stringify(%s)" % js))


def _sig(path):
    """Mirror MainWindow._inline_edit_signature: the revision, as a string."""
    try:
        stat = path.stat()
    except OSError:
        return ""
    return "%d:%d" % (stat.st_mtime_ns, stat.st_size)


def _wire(view, path):
    """Back the bridge with the same pure functions the window uses.

    Mirrors MainWindow._inline_edit_fetch / _inline_edit_commit_table without
    dragging a whole MainWindow into a WebEngine test.
    """
    committed = []

    reloads = []

    def fetch(start, end):
        text = path.read_text(encoding="utf-8")
        source = extract_source_lines(text, start, end)
        if source is None:
            return {"ok": False, "error": "out-of-range"}
        reply = {"ok": True, "text": source, "sig": _sig(path)}
        model = parse_table(source)
        if model is not None:
            reply["table"] = model
        return reply

    def commit(start, end, original, new, sig=""):
        # Revision first, exactly as MainWindow does it: a stale signature
        # means the line numbers came from a different version of the file, so
        # what the text says about them proves nothing.
        if sig and sig != _sig(path):
            return {"ok": False, "error": "stale"}
        text = path.read_text(encoding="utf-8")
        out = replace_source_lines(text, start, end, original, new)
        if out is None:
            return {"ok": False, "error": "stale"}
        path.write_text(out, encoding="utf-8")
        committed.append((start, end, original, new))
        return {"ok": True}

    def _model(model_json):
        try:
            model = json.loads(model_json)
        except (TypeError, ValueError):
            return None
        if not isinstance(model, dict) or not isinstance(
            model.get("headers"), list
        ):
            return None
        if not model["headers"]:
            return None
        return model

    def commit_table(start, end, original, model_json, sig=""):
        model = _model(model_json)
        if model is None:
            return {"ok": False, "error": "bad-model"}
        return commit(start, end, original, serialize_table(model), sig)

    def serialize(model_json):
        model = _model(model_json)
        if model is None:
            return {"ok": False, "error": "bad-model"}
        return {"ok": True, "text": serialize_table(model)}

    def reload():
        reloads.append(True)
        return {"ok": True}

    view.bridge.set_inline_edit_handlers(
        fetch=fetch,
        commit=commit,
        commit_table=commit_table,
        serialize_table=serialize,
        reload=reload,
    )
    return committed


# Line 0 is the heading, 2..4 is the table, 6 is the trailing paragraph.
_DOC = (
    "# 標題\n"
    "\n"
    "| 名稱 | 數量 |\n"
    "|---|--:|\n"
    "| 蘋果 | 3 |\n"
    "\n"
    "尾段文字\n"
)
_TABLE_SRC = "| 名稱 | 數量 |\n|---|--:|\n| 蘋果 | 3 |"

_MODEL = {
    "headers": ["名稱 🍎", "數量"],
    "aligns": ["", "center"],
    "rows": [["`FF FF`", "**從未校正**"], ["反斜線 \\| 管線", "3"]],
    "indent": "  ",
}

_TRIPLE_CLICK_TABLE = (
    "document.querySelector('table[data-src-start=\"2\"]')"
    ".dispatchEvent(new MouseEvent('click', {bubbles: true, detail: 3}))"
)


def _open_preview(tmp_path, body=_DOC):
    md = tmp_path / "doc.md"
    md.write_text(body, encoding="utf-8")
    view = RendererView()
    view.resize(900, 700)
    # Shown, not just sized: an unshown widget lays out at zero width, which
    # would make the geometry assertions below vacuously true.
    view.show()
    committed = _wire(view, md)
    view.load_file(md)
    _wait(4000)
    return md, view, committed


def _build_grid(view, model=None):
    """Create a standalone grid on the live page and park it on window.__t."""
    payload = json.dumps(model if model is not None else _MODEL)
    _eval(
        view,
        "(function () { window.__t = window.__tableEdit.create(%s, {});"
        "document.body.appendChild(window.__t.element); return 1; })()" % payload,
    )


# --------------------------------------------------------------------------
# 1. the script is live and the model survives a real round trip
# --------------------------------------------------------------------------


@_skip_webengine
def test_both_editor_scripts_boot_on_the_same_page(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)

    # All three globals present means the one concatenated runJavaScript that
    # carries annotations.js + table_edit.js + inline_edit.js ran to the end;
    # an exception anywhere in it would have cut the tail off.
    assert _eval(view, "typeof window.__tableEdit") == "object"
    assert _eval(view, "typeof window.__tableEdit.create") == "function"
    assert _eval(view, "typeof window.__inlineEdit") == "object"
    assert _eval(view, "typeof window.__inlineEditBoot") == "function"
    assert _eval(view, "typeof window.__annot") == "object"


@_skip_webengine
def test_create_builds_a_grid_and_round_trips_a_chinese_model(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    assert _eval(view, "document.querySelectorAll('.tedit-grid').length") == 1
    # (rows + header) * cols editable cells.
    assert _eval(view, "document.querySelectorAll('.tedit-cell').length") == 6
    assert _eval(view, "document.querySelectorAll('.tedit-colhead').length") == 2
    assert _json(view, "window.__t.getModel()") == _MODEL


@_skip_webengine
def test_a_created_grid_shows_the_model_text_in_its_cells(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    texts = _json(
        view,
        "Array.prototype.map.call(document.querySelectorAll('.tedit-cell'),"
        "function (c) { return c.textContent; })",
    )
    assert texts == [
        "名稱 🍎", "數量",
        "`FF FF`", "**從未校正**",
        "反斜線 \\| 管線", "3",
    ]
    # Alignment reaches the real style property, not just the model.
    assert _eval(
        view, "document.querySelectorAll('.tedit-cell')[1].style.textAlign"
    ) == "center"


# --------------------------------------------------------------------------
# 2. contenteditable really works here
# --------------------------------------------------------------------------


@_skip_webengine
def test_cells_are_genuinely_contenteditable_in_chromium(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    modes = _json(
        view,
        "Array.prototype.map.call(document.querySelectorAll('.tedit-cell'),"
        "function (c) { return c.contentEditable; })",
    )
    editable = _json(
        view,
        "Array.prototype.map.call(document.querySelectorAll('.tedit-cell'),"
        "function (c) { return c.isContentEditable; })",
    )
    assert editable == [True] * 6, modes
    # Chromium accepts plaintext-only; the "true" fallback only exists for
    # engines that do not, and must not be what runs here.
    assert modes == ["plaintext-only"] * 6
    assert _eval(view, "document.querySelector('.tedit-cell').spellcheck") is False


@_skip_webengine
def test_focus_puts_the_caret_in_the_first_header_cell(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    _eval(view, "window.__t.focus()")

    assert _eval(
        view,
        "document.activeElement === document.querySelectorAll('.tedit-cell')[0]",
    ) is True
    # caretToEnd collapsed a real Selection inside that cell.
    assert _eval(view, "window.getSelection().isCollapsed") is True
    assert _eval(
        view,
        "document.querySelectorAll('.tedit-cell')[0].contains("
        "window.getSelection().anchorNode)",
    ) is True


@_skip_webengine
def test_editing_a_cell_is_read_back_by_get_model(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    _eval(
        view,
        "(function () { var c = document.querySelectorAll('.tedit-cell');"
        "c[0].textContent = '零件'; c[5].textContent = '七'; return 1; })()",
    )

    model = _json(view, "window.__t.getModel()")
    assert model["headers"] == ["零件", "數量"]
    assert model["rows"][1][1] == "七"
    # Nothing else moved.
    assert model["rows"][0] == _MODEL["rows"][0]
    assert model["aligns"] == _MODEL["aligns"]
    assert model["indent"] == "  "


@_skip_webengine
def test_shift_enter_types_a_literal_br_through_exec_command(qapp, tmp_path):
    """The stub has no execCommand at all, so only a browser proves this path."""
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    _eval(
        view,
        "(function () { var c = document.querySelectorAll('.tedit-cell')[0];"
        "c.focus();"
        "var r = document.createRange(); r.selectNodeContents(c);"
        "r.collapse(false);"
        "var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);"
        "c.dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Enter', shiftKey: true, bubbles: true})); return 1; })()",
    )
    _wait(200)

    assert _json(view, "window.__t.getModel()")["headers"][0] == "名稱 🍎<br>"
    # And the cell holds one flat text run, not a <br> element.
    assert _eval(
        view, "document.querySelectorAll('.tedit-cell')[0].querySelectorAll('br').length"
    ) == 0


# --------------------------------------------------------------------------
# 3. structural edits through real clicks
# --------------------------------------------------------------------------


@_skip_webengine
def test_adding_a_row_and_a_column_changes_both_model_and_dom(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    _eval(view, "document.querySelectorAll('.tedit-btn')[0].click()")  # ＋列
    after_row = _json(view, "window.__t.getModel()")
    assert len(after_row["rows"]) == 3
    assert after_row["rows"][2] == ["", ""]
    assert _eval(view, "document.querySelectorAll('.tedit-cell').length") == 8

    _eval(view, "document.querySelectorAll('.tedit-btn')[1].click()")  # ＋欄
    after_col = _json(view, "window.__t.getModel()")
    assert len(after_col["headers"]) == 3
    assert len(after_col["aligns"]) == 3
    assert all(len(row) == 3 for row in after_col["rows"])
    assert _eval(view, "document.querySelectorAll('.tedit-cell').length") == 12
    assert _eval(view, "document.querySelectorAll('.tedit-colhead').length") == 3


@_skip_webengine
def test_deleting_columns_stops_at_the_last_one(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    _eval(view, "document.querySelectorAll('.tedit-coldel')[1].click()")
    assert _json(view, "window.__t.getModel()")["headers"] == ["名稱 🍎"]

    _eval(view, "document.querySelectorAll('.tedit-coldel')[0].click()")
    assert _json(view, "window.__t.getModel()")["headers"] == ["名稱 🍎"]
    assert _eval(view, "document.querySelectorAll('.tedit-colhead').length") == 1


@_skip_webengine
def test_deleting_a_row_takes_the_right_one_out(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    _eval(view, "document.querySelectorAll('.tedit-rowdel')[0].click()")
    model = _json(view, "window.__t.getModel()")
    assert model["rows"] == [_MODEL["rows"][1]]
    assert _eval(view, "document.querySelectorAll('.tedit-cell').length") == 4

    _eval(view, "document.querySelectorAll('.tedit-rowdel')[0].click()")
    assert _json(view, "window.__t.getModel()")["rows"] == []
    # The header row is never removable.
    assert _eval(view, "document.querySelectorAll('.tedit-cell').length") == 2


@_skip_webengine
@pytest.mark.parametrize(
    "index, value", [(0, "left"), (1, "center"), (2, "right")]
)
def test_every_alignment_button_moves_the_real_text_align(
    qapp, tmp_path, index, value
):
    _md, view, _ = _open_preview(tmp_path)
    _build_grid(view)

    _eval(view, "document.querySelectorAll('.tedit-align')[%d].click()" % index)
    assert _json(view, "window.__t.getModel()")["aligns"] == [value, "center"]
    assert _eval(
        view, "document.querySelectorAll('.tedit-cell')[0].style.textAlign"
    ) == value
    assert _eval(
        view,
        "document.querySelectorAll('.tedit-align')[%d].className" % index,
    ).endswith("is-on")

    # Clicking the lit button again is how "no alignment" stays reachable.
    _eval(view, "document.querySelectorAll('.tedit-align')[%d].click()" % index)
    assert _json(view, "window.__t.getModel()")["aligns"] == ["", "center"]
    assert _eval(
        view, "document.querySelectorAll('.tedit-cell')[0].style.textAlign"
    ) == ""


# --------------------------------------------------------------------------
# 4. the two scripts on one page, driven through the real triple-click
# --------------------------------------------------------------------------


@_skip_webengine
def test_triple_clicking_a_table_opens_the_grid_not_a_textarea(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)

    assert _eval(view, "document.querySelectorAll('.inline-edit').length") == 1
    assert _eval(view, "document.querySelectorAll('.inline-edit .tedit-grid').length") == 1
    assert _eval(view, "document.querySelectorAll('.inline-edit textarea').length") == 0
    assert _eval(
        view, "document.querySelector('table[data-src-start=\"2\"]').style.display"
    ) == "none"
    assert _json(
        view,
        "Array.prototype.map.call("
        "document.querySelectorAll('.inline-edit .tedit-cell'),"
        "function (c) { return c.textContent; })",
    ) == ["名稱", "數量", "蘋果", "3"]


@_skip_webengine
def test_triple_clicking_a_paragraph_still_opens_the_raw_textarea(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)

    _eval(
        view,
        "document.querySelector('p[data-src-start=\"6\"]')"
        ".dispatchEvent(new MouseEvent('click', {bubbles: true, detail: 3}))",
    )
    _wait(700)

    assert _eval(view, "document.querySelectorAll('.inline-edit textarea').length") == 1
    assert _eval(view, "document.querySelectorAll('.tedit-grid').length") == 0
    assert _eval(view, "document.querySelector('.inline-edit textarea').value") == (
        "尾段文字"
    )


@_skip_webengine
def test_ctrl_enter_in_the_grid_rewrites_only_the_table_lines(qapp, tmp_path):
    md, view, committed = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)
    _eval(
        view,
        "(function () {"
        "var c = document.querySelectorAll('.inline-edit .tedit-cell');"
        "c[2].textContent = '香蕉';"
        "c[2].dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Enter', ctrlKey: true, bubbles: true})); return 1; })()",
    )
    _wait(900)

    assert md.read_text(encoding="utf-8") == (
        "# 標題\n"
        "\n"
        "| 名稱 | 數量 |\n"
        "| ---- | ---: |\n"
        "| 香蕉 | 3    |\n"
        "\n"
        "尾段文字\n"
    )
    assert len(committed) == 1
    assert committed[0][:3] == (2, 4, _TABLE_SRC)


@_skip_webengine
def test_escape_in_the_grid_touches_nothing_and_shows_the_table_again(
    qapp, tmp_path
):
    md, view, committed = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)
    _eval(
        view,
        "(function () {"
        "var c = document.querySelectorAll('.inline-edit .tedit-cell');"
        "c[2].textContent = '丟掉的編輯';"
        "c[2].dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Escape', bubbles: true})); return 1; })()",
    )
    _wait(500)

    assert md.read_text(encoding="utf-8") == _DOC
    assert committed == []
    assert _eval(view, "document.querySelectorAll('.inline-edit').length") == 0
    assert _eval(view, "document.querySelectorAll('.tedit').length") == 0
    assert _eval(
        view, "document.querySelector('table[data-src-start=\"2\"]').style.display"
    ) == ""


@_skip_webengine
def test_a_stale_file_refuses_the_write_and_keeps_the_grid_standing(
    qapp, tmp_path
):
    md, view, committed = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)
    external = _DOC.replace("蘋果", "別人寫的")
    md.write_text(external, encoding="utf-8")
    _eval(
        view,
        "(function () {"
        "var c = document.querySelectorAll('.inline-edit .tedit-cell');"
        "c[2].textContent = '不能弄丟';"
        "c[2].dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Enter', ctrlKey: true, bubbles: true})); return 1; })()",
    )
    _wait(900)

    assert md.read_text(encoding="utf-8") == external
    assert committed == []
    # The grid is still up and still holds what the user typed.
    #
    # NOTE: this pins the *JavaScript* half of the contract only. The real
    # MainWindow._inline_edit_commit additionally calls reload_current() on a
    # stale write, which reloads the page and takes the grid (and these edits)
    # with it -- see the review notes; that is a Python-side behaviour, not
    # something inline_edit.js can be blamed for.
    assert _eval(view, "document.querySelectorAll('.inline-edit .tedit-grid').length") == 1
    assert _eval(
        view, "document.querySelectorAll('.inline-edit .tedit-cell')[2].textContent"
    ) == "不能弄丟"
    assert _eval(view, "document.querySelector('.inline-edit').className") == (
        "inline-edit"
    )


@_skip_webengine
def test_a_missing_table_edit_script_degrades_to_the_raw_textarea(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)

    _eval(view, "delete window.__tableEdit; typeof window.__tableEdit")
    assert _eval(view, "typeof window.__tableEdit") == "undefined"
    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)

    assert _eval(view, "document.querySelectorAll('.inline-edit textarea').length") == 1
    assert _eval(view, "document.querySelector('.inline-edit textarea').value") == (
        _TABLE_SRC
    )


@_skip_webengine
def test_disabling_inline_edit_tears_the_grid_down(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)
    assert _eval(view, "document.querySelectorAll('.tedit-grid').length") == 1

    view.set_inline_edit_enabled(False)
    _wait(400)

    assert _eval(view, "document.querySelectorAll('.tedit').length") == 0
    assert _eval(view, "document.querySelectorAll('.inline-edit').length") == 0
    assert _eval(
        view, "document.querySelector('table[data-src-start=\"2\"]').style.display"
    ) == ""


@_skip_webengine
def test_the_raw_toggle_swaps_the_grid_for_a_textarea_and_escape_restores(
    qapp, tmp_path
):
    md, view, committed = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)
    # Type into the grid first: the whole point of the switch is that it
    # carries the work over instead of reverting to what is on disk.
    _eval(
        view,
        "(function () {"
        "var c = document.querySelectorAll('.inline-edit .tedit-cell');"
        "c[2].textContent = '香蕉'; return c.length; })()",
    )
    _eval(view, "document.querySelector('.tedit-raw').click()")
    _wait(600)

    assert _eval(view, "document.querySelectorAll('.tedit-grid').length") == 0
    value = _eval(view, "document.querySelector('.inline-edit textarea').value")
    # Python rendered the live model, so the cell edit is in the source text
    # and the layout is the normalized one -- not the file's own bytes.
    assert value == serialize_table(
        {
            "headers": ["名稱", "數量"],
            "aligns": ["", "right"],
            "rows": [["香蕉", "3"]],
            "indent": "",
        }
    )
    assert "香蕉" in value
    assert value != _TABLE_SRC
    assert _eval(
        view, "document.querySelector('table[data-src-start=\"2\"]').style.display"
    ) == "none"

    _eval(
        view,
        "document.querySelector('.inline-edit textarea')"
        ".dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Escape', bubbles: true}))",
    )
    _wait(400)

    assert _eval(view, "document.querySelectorAll('.inline-edit').length") == 0
    assert _eval(
        view, "document.querySelector('table[data-src-start=\"2\"]').style.display"
    ) == ""
    assert md.read_text(encoding="utf-8") == _DOC
    assert committed == []


@_skip_webengine
def test_selecting_inside_a_grid_cell_does_not_raise_the_highlight_toolbar(
    qapp, tmp_path
):
    _md, view, _ = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)
    shown = _eval(
        view,
        "(function () {"
        "var c = document.querySelectorAll('.inline-edit .tedit-cell')[2];"
        "var r = document.createRange(); r.selectNodeContents(c);"
        "var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);"
        "c.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));"
        "var t = document.querySelector('.annot-toolbar');"
        "return t ? t.style.display : 'absent'; })()",
    )

    assert shown in ("absent", "none")

    # Positive control: the very same gesture outside the editor still raises
    # the toolbar, so the assertion above is the guard working, not annotations
    # being dead on this page.
    outside = _eval(
        view,
        "(function () {"
        "var h = document.querySelector('h1');"
        "var r = document.createRange(); r.selectNodeContents(h);"
        "var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);"
        "h.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));"
        "var t = document.querySelector('.annot-toolbar');"
        "return t ? t.style.display : 'absent'; })()",
    )

    assert outside == "flex"


_ANNOT = json.dumps([{
    "id": "a1",
    "exact": "重要字",
    "prefix": "",
    "suffix": "在這裡",
    "textPosition": 0,
    "color": "#ffd54f",
    "note": "",
    "tags": [],
}])


@_skip_webengine
def test_rendering_highlights_while_the_grid_is_open_keeps_them_off_the_grid(
    qapp, tmp_path
):
    """The grid puts a second copy of the table's text into the DOM, which the
    annotation text walker also sees. A re-render must still land the mark on
    the real document, never inside an editable cell."""
    _md, view, _ = _open_preview(tmp_path, _DOC.replace("尾段文字", "重要字在這裡"))

    _eval(view, "window.__annot.render(%s)" % json.dumps(_ANNOT))
    _wait(300)
    assert _eval(view, "document.querySelectorAll('mark.annot').length") == 1

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)
    assert _eval(view, "document.querySelectorAll('.tedit-grid').length") == 1

    _eval(view, "window.__annot.render(%s)" % json.dumps(_ANNOT))
    _wait(300)

    assert _eval(view, "document.querySelectorAll('mark.annot').length") == 1
    assert _eval(view, "document.querySelectorAll('.tedit mark.annot').length") == 0
    assert _eval(
        view, "document.querySelector('mark.annot').closest('p') !== null"
    ) is True
    # And the grid survived the re-render unharmed.
    assert _json(
        view,
        "Array.prototype.map.call("
        "document.querySelectorAll('.inline-edit .tedit-cell'),"
        "function (c) { return c.textContent; })",
    ) == ["名稱", "數量", "蘋果", "3"]


@_skip_webengine
def test_a_full_editing_session_raises_no_javascript_errors(qapp, tmp_path):
    md, view, _ = _open_preview(tmp_path)
    _eval(
        view,
        "(function () { window.__errs = [];"
        "window.addEventListener('error', function (e) {"
        "  window.__errs.push(String(e.message)); });"
        "return 1; })()",
    )

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)
    _eval(view, "document.querySelectorAll('.tedit-btn')[0].click()")
    _eval(view, "document.querySelectorAll('.tedit-btn')[1].click()")
    _eval(view, "document.querySelectorAll('.tedit-align')[1].click()")
    _eval(view, "document.querySelectorAll('.tedit-rowdel')[0].click()")
    _eval(
        view,
        "(function () {"
        "var c = document.querySelectorAll('.inline-edit .tedit-cell');"
        "c[0].dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Tab', bubbles: true}));"
        "c[0].dispatchEvent(new KeyboardEvent('keydown',"
        "{key: 'Escape', bubbles: true})); return 1; })()",
    )
    _wait(500)

    assert _json(view, "window.__errs") == []
    # Both globals survived the session intact.
    assert _eval(view, "typeof window.__tableEdit.create") == "function"
    assert _eval(view, "typeof window.__inlineEdit.isEditing") == "function"
    assert _eval(view, "window.__inlineEdit.isEditing()") is False
    assert md.read_text(encoding="utf-8") == _DOC


# --------------------------------------------------------------------------
# geometry: the theme owns every <table> in the page, the grid included
# --------------------------------------------------------------------------


@_skip_webengine
def test_the_theme_does_not_take_the_grids_layout_over(qapp, tmp_path):
    """The regression test for the grid rendering as an invisible block.

    Both bundled themes restyle <table>: github.css forces display:block plus
    width:max-content, obsidian-light.css adds margin-left:50% and
    translateX(-50%). The grid is a <table>, so it inherited all of it and was
    laid out as a zero-width block with its cells outside their own box --
    while every structural assertion in this file still passed.
    """
    _md, view, _ = _open_preview(tmp_path)
    # Where the block sits is the yardstick: the grid stands in for it.
    block = _json(
        view,
        "(function () { var r = document.querySelector('table')"
        ".getBoundingClientRect();"
        "return {x: Math.round(r.x), w: Math.round(r.width)}; })()",
    )
    assert block["w"] > 0, "the document's own table did not lay out"

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)

    grid = _json(
        view,
        "(function () { var g = document.querySelector('.tedit-grid');"
        "var s = getComputedStyle(g), r = g.getBoundingClientRect();"
        "return {display: s.display, transform: s.transform,"
        " w: Math.round(r.width), h: Math.round(r.height),"
        " x: Math.round(r.x), right: Math.round(r.right),"
        " vw: window.innerWidth}; })()",
    )

    assert grid["display"] == "table"
    assert grid["transform"] in ("none", "matrix(1, 0, 0, 1, 0, 0)")
    assert grid["w"] > 0 and grid["h"] > 0
    # On screen, and starting where the block it replaced started.
    assert grid["x"] >= 0
    assert grid["x"] < grid["vw"]
    assert abs(grid["x"] - block["x"]) <= 2


@_skip_webengine
def test_the_grid_cells_lay_out_inside_the_grid(qapp, tmp_path):
    _md, view, _ = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)

    box = _json(
        view,
        "(function () {"
        "var g = document.querySelector('.tedit-grid').getBoundingClientRect();"
        "var cells = document.querySelectorAll('.tedit-cell');"
        "var out = {n: cells.length, escaped: 0, empty: 0, display: ''};"
        "Array.prototype.forEach.call(cells, function (c) {"
        "var r = c.getBoundingClientRect();"
        "if (r.x < g.x - 1 || r.right > g.right + 1) out.escaped += 1;"
        "if (r.width === 0 || r.height === 0) out.empty += 1; });"
        "out.display = getComputedStyle(cells[0]).display;"
        "return out; })()",
    )

    assert box["n"] > 0
    assert box["display"] == "table-cell"
    # A cell outside its own table, or with no area, is the shape the block
    # layout produced -- and is invisible on screen.
    assert box["escaped"] == 0
    assert box["empty"] == 0


@_skip_webengine
def test_the_toolbar_and_hint_stay_on_one_line(qapp, tmp_path):
    """Zero-width ancestors show up here first.

    At zero width the flex toolbar wraps to three rows and the hint wraps to
    one character per line, which is how the collapsed layout announced itself
    (108px and 362px tall respectively).
    """
    _md, view, _ = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)

    sizes = _json(
        view,
        "(function () { var out = {};"
        "['.tedit', '.tedit-bar', '.tedit-scroll', '.tedit-hint']"
        ".forEach(function (sel) {"
        "var r = document.querySelector(sel).getBoundingClientRect();"
        "out[sel] = {w: Math.round(r.width), h: Math.round(r.height)}; });"
        "return out; })()",
    )

    for sel, box in sizes.items():
        assert box["w"] > 100, "%s collapsed to %dpx wide" % (sel, box["w"])
    assert sizes[".tedit-bar"]["h"] < 60
    assert sizes[".tedit-hint"]["h"] < 40


@_skip_webengine
def test_a_wide_grid_scrolls_instead_of_stretching_the_page(qapp, tmp_path):
    wide = "| " + " | ".join("欄位 %d" % i for i in range(12)) + " |\n"
    wide += "|" + "|".join([" --- "] * 12) + "|\n"
    wide += "| " + " | ".join("內容內容內容 %d" % i for i in range(12)) + " |\n"
    _md, view, _ = _open_preview(tmp_path, "# 標題\n\n" + wide + "\n尾段\n")

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)

    box = _json(
        view,
        "(function () {"
        "var sc = document.querySelector('.tedit-scroll');"
        "var g = document.querySelector('.tedit-grid');"
        "return {scrollable: sc.scrollWidth > sc.clientWidth + 1,"
        " scroll_w: Math.round(sc.getBoundingClientRect().width),"
        " grid_w: Math.round(g.getBoundingClientRect().width),"
        " body_scrolls: document.body.scrollWidth >"
        "   document.documentElement.clientWidth + 1}; })()",
    )

    assert box["grid_w"] > box["scroll_w"], "the grid is not actually wider"
    assert box["scrollable"], "the wide grid must scroll inside its own box"
    assert not box["body_scrolls"], "the page itself must not scroll sideways"


@_skip_webengine
def test_the_column_controls_stand_over_their_own_column(qapp, tmp_path):
    """Each column's buttons must line up with the column they act on.

    They were pinned to width:1px along with the left-hand scaffolding column,
    which collapsed every column's controls into one clump at the far left.
    """
    _md, view, _ = _open_preview(tmp_path)

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)

    cols = _json(
        view,
        "(function () {"
        "var heads = document.querySelectorAll('.tedit-colhead');"
        "var cells = document.querySelectorAll('.tedit-head .tedit-cell');"
        "var out = [];"
        "for (var i = 0; i < heads.length; i++) {"
        "var h = heads[i].getBoundingClientRect();"
        "var c = cells[i].getBoundingClientRect();"
        "out.push({dx: Math.round(Math.abs(h.x - c.x)),"
        " dw: Math.round(Math.abs(h.width - c.width)),"
        " w: Math.round(h.width)}); }"
        "return out; })()",
    )

    assert len(cols) == 2, "expected one control cell per column"
    for i, col in enumerate(cols):
        assert col["dx"] <= 2, "column %d controls are %dpx off" % (i, col["dx"])
        assert col["dw"] <= 2
        # A hairline cell is the collapsed shape this guards against.
        assert col["w"] > 20


@_skip_webengine
def test_the_grid_takes_the_width_the_block_had(qapp, tmp_path):
    """Sized to content the grid came out a third as wide, wrapping badly."""
    _md, view, _ = _open_preview(tmp_path)
    block = _json(
        view,
        "(function () { var r = document.querySelector('table')"
        ".getBoundingClientRect();"
        "return {w: Math.round(r.width)}; })()",
    )

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)

    grid = _json(
        view,
        "(function () { var g = document.querySelector('.tedit-grid');"
        "var tall = 0;"
        "Array.prototype.forEach.call(document.querySelectorAll('.tedit-cell'),"
        "function (c) { tall = Math.max(tall, c.getBoundingClientRect().height); });"
        "return {w: Math.round(g.getBoundingClientRect().width),"
        " tallest: Math.round(tall)}; })()",
    )

    assert grid["w"] >= block["w"] * 0.95
    # One short line per cell in this document; five-deep wrapping was the
    # symptom of the grid shrinking to its content.
    assert grid["tallest"] < 60


@_skip_webengine
def test_opening_an_editor_takes_the_highlight_toolbar_down(qapp, tmp_path):
    """The triple-click that opens the editor is a selection too.

    annotations.js raises the colour toolbar on mouseup, so without this it
    ends up floating over the editor that is about to open.
    """
    _md, view, _ = _open_preview(tmp_path)
    # Raise it the way a real selection does.
    _eval(
        view,
        "(function () { var h = document.querySelector('h1');"
        "var r = document.createRange(); r.selectNodeContents(h);"
        "var s = window.getSelection(); s.removeAllRanges(); s.addRange(r);"
        "document.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));"
        "return 1; })()",
    )
    _wait(400)
    assert _eval(
        view,
        "getComputedStyle(document.querySelector('.annot-toolbar')).display",
    ) != "none", "the toolbar never came up, so this proves nothing"

    _eval(view, _TRIPLE_CLICK_TABLE)
    _wait(700)

    assert _eval(view, "document.querySelectorAll('.tedit-grid').length") == 1
    assert _eval(
        view,
        "getComputedStyle(document.querySelector('.annot-toolbar')).display",
    ) == "none"
