"""Contract checks for the block-patch diff core.

The algorithm itself runs in Node (tests/js/patch_harness.js) and is skipped
where Node is unavailable; the two things that would silently break the next
step -- the export name and the promise that this half never touches the DOM
-- are checked here so they are caught even then.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_ASSETS = _ROOT / "assets"
_HARNESS = Path(__file__).parent / "js" / "patch_harness.js"

node = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is not installed"
)


@node
def test_patch_script_parses():
    result = subprocess.run(
        ["node", "--check", str(_ASSETS / "patch.js")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@node
def test_patch_diff_behaviour_harness():
    result = subprocess.run(
        ["node", str(_HARNESS)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "OK" in result.stdout


def test_patch_script_exposes_the_diff_core_and_stays_off_the_dom():
    script = (_ASSETS / "patch.js").read_text(encoding="utf-8")
    assert "window.__mdvPatch" in script
    assert "_diffKeys" in script
    # The decision half is pure so it can be tested without a DOM at all --
    # the node harness stubs nothing but `window`. Any of these turning up
    # would mean the apply step's DOM work had leaked back into it.
    for dom_api in (
        "createElement",
        "querySelector",
        "innerHTML",
        "appendChild",
        "insertBefore",
        "parentNode",
    ):
        assert dom_api not in script, dom_api
