import subprocess
import sys
from pathlib import Path

from app.version import RELEASE_NOTES, VERSION
from tools.write_release_notes import render_release_notes


def test_release_body_contains_only_current_version_notes(tmp_path):
    output = tmp_path / "release.md"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "tools/write_release_notes.py",
         "--output", str(output), "--version", f"v{VERSION}"],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == render_release_notes(VERSION, RELEASE_NOTES)
    assert len(output.read_bytes()) < len(Path("CHANGELOG.md").read_bytes())


def test_release_body_rejects_mismatched_tag(tmp_path):
    output = tmp_path / "release.md"
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "tools/write_release_notes.py",
         "--output", str(output), "--version", "0.0.0"],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode != 0
    assert not output.exists()
