"""Contract checks for assets/vditor_glue.js and the WYSIWYG host page.

The behavioural half runs in Node against a fake Vditor + DOM (tests/js/
vditor_glue_harness.js) and is skipped where Node is unavailable; the asset
wiring is checked here regardless, mirroring tests/test_inline_edit_js.py.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_ASSETS = _ROOT / "assets"
_HARNESS = Path(__file__).parent / "js" / "vditor_glue_harness.js"

node = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is not installed"
)


@node
def test_vditor_glue_script_parses():
    result = subprocess.run(
        ["node", "--check", str(_ASSETS / "vditor_glue.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@node
def test_vditor_glue_behaviour_harness():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_vditor_glue_exposes_the_boot_and_glue_hooks():
    script = (_ASSETS / "vditor_glue.js").read_text(encoding="utf-8")
    assert "__wysiwygBoot" in script
    assert "__wysiwygGlue" in script
    assert "contentChanged" in script
    assert "saveRequested" in script


def test_vditor_glue_disables_cache_and_autosave():
    script = (_ASSETS / "vditor_glue.js").read_text(encoding="utf-8")
    assert "cache: { enable: false }" in script


def test_vditor_host_page_loads_offline_assets_only():
    host = _ASSETS / "vditor_host.html"
    assert host.exists()
    html = host.read_text(encoding="utf-8")
    for forbidden in ("http://", "https://", "//fonts.", "//cdn."):
        assert forbidden not in html, forbidden
    assert "vditor/dist/index.css" in html
    assert "vditor/dist/index.min.js" in html
    assert "vditor_glue.js" in html
    assert "wysiwygBridge" in html
    # qwebchannel.js ships as a Qt resource, not a file under assets/, so
    # wysiwyg_view.py inlines it into this placeholder (see renderer.py's
    # ``_read_resource(":/qtwebchannel/qwebchannel.js")`` for the source).
    assert "__WYSIWYG_QWEBCHANNEL_JS__" in html


def test_wysiwyg_view_fills_in_the_qwebchannel_placeholder():
    module = (_ROOT / "app" / "wysiwyg_view.py").read_text(encoding="utf-8")
    assert "__WYSIWYG_QWEBCHANNEL_JS__" in module
    assert ":/qtwebchannel/qwebchannel.js" in module
