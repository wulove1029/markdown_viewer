"""Contract checks for the preview-side JavaScript.

The behavioural half runs in Node against a DOM stub (tests/js/
inline_edit_harness.js) and is skipped where Node is unavailable; the asset
wiring is checked here so a missing injection is caught even then.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_ASSETS = _ROOT / "assets"
_HARNESS = Path(__file__).parent / "js" / "inline_edit_harness.js"
_TABLE_HARNESS = Path(__file__).parent / "js" / "table_edit_harness.js"

node = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is not installed"
)


@node
@pytest.mark.parametrize(
    "name", ["annotations.js", "inline_edit.js", "table_edit.js"]
)
def test_asset_script_parses(name):
    result = subprocess.run(
        ["node", "--check", str(_ASSETS / name)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@node
def test_inline_edit_behaviour_harness():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


@node
def test_table_edit_behaviour_harness():
    result = subprocess.run(
        ["node", str(_TABLE_HARNESS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_renderer_injects_the_inline_edit_script_with_the_bridge():
    renderer = (_ROOT / "app" / "renderer.py").read_text(encoding="utf-8")
    assert "inline_edit.js" in renderer
    assert "self._inline_edit_js" in renderer
    assert "set_inline_edit_enabled" in renderer


def test_annotations_boot_hands_the_bridge_to_the_inline_editor():
    annotations = (_ASSETS / "annotations.js").read_text(encoding="utf-8")
    # One QWebChannel per page: the inline editor must reuse this bridge
    # rather than opening a second transport.
    assert "__inlineEditBoot(bridge, inlineEdit)" in annotations
    assert "function (jsonStr, sideNotes, inlineEdit)" in annotations


def test_inline_edit_script_exposes_the_boot_and_enable_hooks():
    script = (_ASSETS / "inline_edit.js").read_text(encoding="utf-8")
    assert "window.__inlineEditBoot" in script
    assert "window.__inlineEdit" in script
    assert "data-src-start" in script


def test_renderer_injects_the_table_editor_before_the_inline_editor():
    renderer = (_ROOT / "app" / "renderer.py").read_text(encoding="utf-8")
    assert "table_edit.js" in renderer
    assert "self._table_edit_js" in renderer
    # inline_edit.js looks for window.__tableEdit as it opens a block, so
    # the grid editor has to be evaluated first.
    assert renderer.index("+ self._table_edit_js") < renderer.index(
        "+ self._inline_edit_js"
    )


def test_inline_edit_script_hands_tables_to_the_grid_editor():
    script = (_ASSETS / "inline_edit.js").read_text(encoding="utf-8")
    assert "__tableEdit" in script
    assert "inlineEditCommitTable" in script
