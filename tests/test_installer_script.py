"""Static checks on the Inno Setup script."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_setup_file_carries_the_app_version():
    # Without VersionInfoVersion the setup's file properties show 0.0.0.0.
    script = (ROOT / "installer.iss").read_text(encoding="utf-8")
    lines = [line.strip() for line in script.splitlines()]
    assert "VersionInfoVersion={#MyAppVersion}" in lines
