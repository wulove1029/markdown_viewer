"""F1a external entry points: URL protocol, folder launch, tab identity, IPC wake."""
# ruff: noqa: F811

from __future__ import annotations

import os
from urllib.parse import quote

import pytest

import main as main_mod
from app.document_tabs import tab_path_key
from app.url_protocol import is_protocol_url, parse_open_url
from tests.test_editor_workspace_integration import (  # noqa: F401
    _isolated_workspace_dependencies,
    make_workspace_window,
)


def _url(path: str) -> str:
    return "markdownviewer://open?path=" + quote(path, safe="")


# ---------------- URL parser ----------------
def test_parse_percent_encoded_chinese_and_spaces():
    path = "C:\\Users\\測試 user\\筆記 本\\a b.md"
    assert parse_open_url(_url(path)) == path


def test_parse_accepts_both_forms_and_ignores_extras_and_case():
    enc = quote("D:\\docs\\x.md", safe="")
    assert parse_open_url(f"MarkdownViewer:open?path={enc}") == "D:\\docs\\x.md"
    assert parse_open_url(f"markdownviewer://open/?foo=1&path={enc}&bar=2") == "D:\\docs\\x.md"
    assert parse_open_url(f"MARKDOWNVIEWER://OPEN?path={enc}") == "D:\\docs\\x.md"


def test_parse_keeps_plus_literal():
    assert parse_open_url("markdownviewer://open?path=C%3A%5Ca+b.md") == "C:\\a+b.md"


@pytest.mark.parametrize("name", ["a.MD", "b.PDF", "c.Markdown", "d.pdf"])
def test_parse_accepts_whitelisted_extensions_case_insensitive(name):
    path = "C:\\docs\\" + name
    assert parse_open_url(_url(path)) == path


@pytest.mark.parametrize(
    "path",
    [
        "\\\\server\\share\\a.md",
        "//server/share/a.md",
        "\\\\?\\C:\\a.md",
        "\\\\.\\PhysicalDrive0",
        "relative\\a.md",
        "a.md",
        "C:a.md",
        "/etc/passwd",
        "C:\\a\x00.md",
    ],
)
def test_parse_rejects_unsafe_paths(path):
    assert parse_open_url(_url(path)) is None


@pytest.mark.parametrize(
    "path",
    [
        "C:\\x\\a.exe",
        "C:\\x\\a.lnk",
        "C:\\x\\a.url",
        "C:\\x\\a.bat",
        "C:\\x\\a.txt",
        "C:\\x\\folder",
        "C:\\x\\folder\\",
        "C:\\x\\a.md.exe",
        "C:\\x\\..\\a.md",
        "C:/x/../a.md",
        "C:\\..\\a.md",
    ],
)
def test_parse_rejects_non_documents_and_dotdot(path):
    assert parse_open_url(_url(path)) is None


@pytest.mark.parametrize(
    "url",
    [
        "http://open?path=C%3A%5Ca.md",
        "markdownviewer2://open?path=C%3A%5Ca.md",
        "markdownviewer://run?path=C%3A%5Ca.md",
        "markdownviewer://open",
        "markdownviewer://open?path=",
        "markdownviewer://open?other=C%3A%5Ca.md",
        "",
    ],
)
def test_parse_rejects_bad_scheme_action_or_missing_path(url):
    assert parse_open_url(url) is None


def test_is_protocol_url():
    assert is_protocol_url("MarkdownViewer://x")
    assert not is_protocol_url("C:\\a.md")


def test_resolve_launch_arg(tmp_path):
    assert main_mod.resolve_launch_arg("") == ""
    assert main_mod.resolve_launch_arg("markdownviewer://run?path=C%3A%5Ca") is None
    target = str(tmp_path / "n.md")
    assert main_mod.resolve_launch_arg(_url(target)) == str(tmp_path.resolve() / "n.md")


def test_url_to_folder_named_like_document_is_rejected(tmp_path):
    folder = tmp_path / "tricky.md"
    folder.mkdir()
    assert main_mod.resolve_launch_arg(_url(str(folder))) is None


def test_plain_folder_argument_still_resolves(tmp_path):
    assert main_mod.resolve_launch_arg(str(tmp_path)) == str(tmp_path.resolve())


# ---------------- tab identity ----------------
def test_same_file_different_spelling_opens_one_tab(make_workspace_window, tmp_path):
    note = tmp_path / "Note.md"
    note.write_text("# hi", encoding="utf-8")
    window = make_workspace_window()
    window.open_path(str(note))
    count = window._tab_bar.count()
    variants = [
        str(note).swapcase(),
        str(note).replace("\\", "/"),
        os.path.join(str(tmp_path), "sub", "..", "Note.md"),
    ]
    for variant in variants:
        window.open_path(variant)
        assert window._tab_bar.count() == count
    assert window._tab_bar.tabData(0) == str(note)  # display spelling unchanged
    assert tab_path_key(variants[0]) == tab_path_key(str(note))


# ---------------- folder launch ----------------
def test_folder_argument_opens_folder_not_warning(make_workspace_window, tmp_path, monkeypatch):
    from app import window as window_mod

    warned = []
    monkeypatch.setattr(window_mod.QMessageBox, "warning", lambda *a: warned.append(a))
    window = make_workspace_window()
    opened = []
    window._panel.file_browser.open_folder = opened.append
    window.open_external_path(str(tmp_path))
    assert opened == [tmp_path]
    assert not warned
    assert window._tab_bar.count() == 0


def test_file_browser_open_folder_adds_library_once(qapp, tmp_path):
    from app import file_browser as fb

    added = []

    class Store:
        def add(self, folder):
            added.append(str(folder))

    class Fake:
        _store = Store()

        def contains_library_path(self, p):
            return False

        def _remember_expanded(self, f):
            pass

        def refresh_libraries(self):
            pass

        def _select_path(self, p):
            pass

    fb.FileBrowserView.open_folder(Fake(), tmp_path)
    assert added == [str(tmp_path)]
    Fake.contains_library_path = lambda self, p: True
    fb.FileBrowserView.open_folder(Fake(), tmp_path)
    assert added == [str(tmp_path)]


# ---------------- IPC wake ----------------
@pytest.mark.parametrize("payload_is_empty", [True, False])
def test_hidden_window_is_shown_by_forwarded_path(
    make_workspace_window, tmp_path, payload_is_empty
):
    note = tmp_path / "a.md"
    note.write_text("# a", encoding="utf-8")
    window = make_workspace_window()
    window.show()
    window.close()  # main window close only hides it
    assert not window.isVisible()
    main_mod.handle_forwarded_path(window, "" if payload_is_empty else str(note))
    assert window.isVisible()
    if not payload_is_empty:
        assert window._index_of_path(str(note)) >= 0


def test_minimized_window_is_restored(make_workspace_window):
    from PySide6.QtCore import Qt

    window = make_workspace_window()
    window.show()
    window.showMinimized()
    main_mod.handle_forwarded_path(window, "")
    assert not (window.windowState() & Qt.WindowState.WindowMinimized)


def test_invalid_url_over_ipc_opens_nothing(make_workspace_window):
    window = make_workspace_window()
    main_mod.handle_forwarded_path(window, "markdownviewer://run?path=C%3A%5Ca.md")
    assert window._tab_bar.count() == 0


# ---------------- IPC startup race ----------------
def test_second_instance_never_steals_live_ipc_name(qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget

    monkeypatch.setattr(main_mod, "_IPC_SERVER_NAME", f"mv-test-{os.getpid()}-race")
    first_owner, second_owner = QWidget(), QWidget()
    first = main_mod._setup_ipc_server(first_owner)
    assert first is not None and first.isListening()
    try:
        assert main_mod._setup_ipc_server(second_owner) is None
        assert first.isListening()  # still ours, not removed
    finally:
        first.close()
        first_owner.deleteLater()
        second_owner.deleteLater()
