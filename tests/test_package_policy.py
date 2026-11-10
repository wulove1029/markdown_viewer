import os
import shutil
import sys
from pathlib import Path

import pytest

from tools import package_policy as pp
from tools.package_policy import filter_webengine_locales


def test_locale_filter_keeps_required_locales_and_other_resource_packs():
    entries = [(f"PySide6/translations/qtwebengine_locales/{locale}.pak", "source", "DATA")
               for locale in ("zh-TW", "zh-CN", "en-US", "fr", "ja")]
    other = ("PySide6/resources/qtwebengine_resources.pak", "source", "DATA")
    result = filter_webengine_locales(entries + [other])
    assert result == entries[:3] + [other]


# --- the bundled Agent Watchdog never ships what a link points to -------------------


def _junction(target: Path, link: Path) -> None:
    """A directory junction on Windows (no privilege needed), else a symlink."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def _symlink(target: Path, link: Path, directory: bool = False) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symbolic link here: {exc}")


def _bundle_with_outside(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "agent_watchdog"
    (source / "static" / "css").mkdir(parents=True)
    (source / "aiwatch.py").write_text("import json\n", encoding="utf-8")
    (source / "static" / "app.js").write_text("//", encoding="utf-8")
    (source / "static" / "css" / "site.css").write_text("", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("import sqlite3\n", encoding="utf-8")
    (outside / "secret.js").write_text("//", encoding="utf-8")
    return source, outside


def _shipped(source: Path) -> list[str]:
    return sorted(Path(src).relative_to(source).as_posix()
                  for src, _ in pp.agent_bundle_datas(source))


EXPECTED = ["aiwatch.py", "static/app.js", "static/css/site.css"]


def test_junctions_inside_the_bundle_are_never_entered(tmp_path):
    source, outside = _bundle_with_outside(tmp_path)
    _junction(outside, source / "static" / "linked")
    _junction(outside, source / "static" / "css" / "deeper")
    _junction(outside, source / "folder.py")  # a directory named like a program file
    assert _shipped(source) == EXPECTED
    assert pp.stdlib_imports(source) == ["json"]  # secret.py was not read either


def test_a_junction_as_the_static_folder_is_not_shipped(tmp_path):
    source, outside = _bundle_with_outside(tmp_path)
    static = source / "static"
    shutil.rmtree(static)
    _junction(outside, static)
    assert _shipped(source) == ["aiwatch.py"]


def test_symbolic_links_inside_the_bundle_are_never_shipped(tmp_path):
    source, outside = _bundle_with_outside(tmp_path)
    _symlink(outside / "secret.py", source / "linked.py")
    _symlink(outside / "secret.js", source / "static" / "linked.js")
    _symlink(outside, source / "static" / "folder", directory=True)
    assert _shipped(source) == EXPECTED
    assert pp.stdlib_imports(source) == ["json"]
