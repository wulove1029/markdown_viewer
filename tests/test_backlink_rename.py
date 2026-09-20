from pathlib import Path

import pytest

from app import backlink_rename, file_ops


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-be", "cp950"])
def test_rename_preserves_unrelated_bytes_and_link_alias_heading(tmp_path, encoding):
    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"# B\n")
    source = tmp_path / "A.md"
    text = "\ufeff" if encoding == "utf-16-be" else ""
    text += "中文\r\n[[ B.md#標題 | 別名 ]] and [[B]]\r\nlast\n"
    before = text.encode(encoding)
    source.write_bytes(before)
    updates = backlink_rename.prepare_backlink_updates(old, new, [tmp_path])
    assert source.read_bytes() == before
    file_ops.rename_document(old, new, backlink_updates=updates)
    assert source.read_bytes() == text.replace("B.md", "B2.md").replace(
        "[[B]]", "[[B2]]").encode(encoding)
    assert new.read_bytes() == b"# B\n"
    assert not old.exists()


def test_only_visible_wikilinks_are_rewritten(tmp_path):
    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"# B")
    source = tmp_path / "A.md"
    protected = ('`[[B]]`\n\n~~~\n[[B]]\n~~~\n\n    [[B]]\n\n'
                 '<!-- [[B]] -->\n\n<span title="[[B]]">inline</span>\n\n'
                 '\\[[B]]\n\n`` [[B]] ` [[B]] ``\n\n')
    text = protected + 'unmatched ` literal\n\n[[B]]\n'
    source.write_bytes(text.encode())
    plan = backlink_rename.prepare_backlink_updates(old, new, [tmp_path])
    assert plan[source][1] == (protected + 'unmatched ` literal\n\n[[B2]]\n').encode()


def test_publish_failure_rolls_back_all_referrers_document_and_sidecars(tmp_path, monkeypatch):
    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"# B")
    notes = old.with_name(old.name + ".notes.json")
    notes.write_bytes(b'{}')
    sources = [tmp_path / f"A{i}.md" for i in range(3)]
    for path in sources:
        path.write_bytes(b"[[B]]\r\n")
    plan = backlink_rename.prepare_backlink_updates(old, new, [tmp_path])
    publish = file_ops._rename_no_replace

    def fail_once(source, target):
        if Path(source).parent.name.startswith('.markdown-relocate-prepared-'):
            if target == sources[1]:
                raise OSError("injected publish failure")
        publish(source, target)

    monkeypatch.setattr(file_ops, "_rename_no_replace", fail_once)
    with pytest.raises(OSError, match="injected publish failure"):
        file_ops.rename_document(old, new, backlink_updates=plan)
    assert old.read_bytes() == b"# B"
    assert notes.read_bytes() == b"{}"
    assert not new.exists()
    assert all(path.read_bytes() == b"[[B]]\r\n" for path in sources)
    assert not list(tmp_path.glob('.markdown-relocate-*'))


def test_atomic_staging_failure_leaves_every_original_untouched(tmp_path, monkeypatch):
    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"# B")
    source = tmp_path / "A.md"
    source.write_bytes(b"[[B]]")
    plan = backlink_rename.prepare_backlink_updates(old, new, [tmp_path])
    monkeypatch.setattr(file_ops, "atomic_write_bytes",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        file_ops.rename_document(old, new, backlink_updates=plan)
    assert old.read_bytes() == b"# B"
    assert source.read_bytes() == b"[[B]]"
    assert not new.exists()


def test_external_change_after_confirmation_is_not_overwritten(tmp_path):
    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"# B")
    source = tmp_path / "A.md"
    source.write_bytes(b"[[B]]")
    plan = backlink_rename.prepare_backlink_updates(old, new, [tmp_path])
    source.write_bytes(b"external change")
    with pytest.raises(OSError, match="已變更"):
        file_ops.rename_document(old, new, backlink_updates=plan)
    assert old.exists() and not new.exists()
    assert source.read_bytes() == b"external change"


def test_self_link_is_rewritten_in_renamed_document(tmp_path):
    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"[[B]]")
    plan = backlink_rename.prepare_backlink_updates(old, new, [tmp_path])
    file_ops.rename_document(old, new, backlink_updates=plan)
    assert new.read_bytes() == b"[[B2]]"


def test_unmatched_backtick_on_same_line_does_not_hide_link(tmp_path):
    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"# B")
    source = tmp_path / "A.md"
    source.write_bytes(b"literal ` and [[B]]")
    plan = backlink_rename.prepare_backlink_updates(old, new, [tmp_path])
    assert plan[source][1] == b"literal ` and [[B2]]"


def test_escaped_backtick_does_not_hide_link(tmp_path):
    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"# B")
    source = tmp_path / "A.md"
    source.write_bytes(br"\` literal [[B]] and `code`")
    plan = backlink_rename.prepare_backlink_updates(old, new, [tmp_path])
    assert plan[source][1] == br"\` literal [[B2]] and `code`"


@pytest.mark.parametrize("name", ["New#part.md", "New[part].md"])
def test_unrepresentable_wikilink_name_is_refused_before_changes(tmp_path, name):
    old, new = tmp_path / "B.md", tmp_path / name
    old.write_bytes(b"# B")
    source = tmp_path / "A.md"
    source.write_bytes(b"[[B]]")
    with pytest.raises(OSError, match="安全表示"):
        backlink_rename.prepare_backlink_updates(old, new, [tmp_path])
    assert old.exists() and not new.exists()
    assert source.read_bytes() == b"[[B]]"


@pytest.mark.parametrize("confirm", [False, True])
def test_real_confirmation_controls_browser_transaction(qapp, tmp_path, monkeypatch, confirm):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMessageBox, QWidget

    from app import backlink_rename_flow
    from app.file_browser import FileBrowserView

    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"# B")
    source = tmp_path / "A.md"
    source.write_bytes(b"[[B]]")
    window = QWidget()
    window._tab_state = {}
    window._active_path = None
    window._recovery_store = SimpleNamespace(load=lambda path: None)
    window._link_roots = lambda: [tmp_path]
    selected = []

    def execute(box):
        assert str(source) in box.detailedText()
        label = "重新命名並更新" if confirm else "取消"
        selected.append(next(button for button in box.buttons() if button.text() == label))

    monkeypatch.setattr(QMessageBox, "exec", execute)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: selected[-1])
    browser = SimpleNamespace(
        on_document_relocation=None,
        on_prepare_backlink_rename=lambda a, b: backlink_rename_flow.prepare_for_window(
            window, a, b),
        on_backlinks_rewritten=None,
        _finish_migration=lambda *args, **kwargs: None,
    )
    FileBrowserView._relocate_document(browser, old, new, "rename")
    assert old.exists() is not confirm
    assert new.exists() is confirm
    assert source.read_bytes() == (b"[[B2]]" if confirm else b"[[B]]")
    window.close()


def test_missing_index_service_never_silently_renames(qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMessageBox

    from app.file_browser import FileBrowserView

    old, new = tmp_path / "B.md", tmp_path / "B2.md"
    old.write_bytes(b"# B")
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    browser = SimpleNamespace(on_document_relocation=None, on_prepare_backlink_rename=None)
    FileBrowserView._relocate_document(browser, old, new, "rename")
    assert old.exists() and not new.exists()
    assert warnings and "索引" in warnings[0][-1]


@pytest.mark.parametrize("reason", ["dirty", "editing", "recovery"])
def test_live_or_recoverable_referrer_blocks_confirmation(qapp, tmp_path, reason):
    from types import SimpleNamespace

    from PySide6.QtGui import QTextDocument

    from app.backlink_rename_flow import _check_buffers

    source = tmp_path / "A.md"
    doc = QTextDocument("[[B]]")
    doc.setModified(reason == "dirty")
    window = SimpleNamespace(
        _tab_state={str(source): {"editor_document": doc}},
        _active_path=str(source), _edit_mode=reason == "editing", _preview_editing=False,
        _recovery_store=SimpleNamespace(load=lambda p: object() if reason == "recovery" else None),
    )
    with pytest.raises(OSError):
        _check_buffers(window, {source: (b"[[B]]", b"[[B2]]")})
    assert doc.toPlainText() == "[[B]]"


def test_clean_inactive_buffer_tracks_rewritten_disk(qapp, tmp_path):
    from types import SimpleNamespace

    from PySide6.QtGui import QTextDocument

    from app.backlink_rename_flow import apply_to_window

    source = tmp_path / "A.md"
    doc = QTextDocument("[[B]]")
    doc.setModified(False)
    state = {"editor_document": doc}
    window = SimpleNamespace(_tab_state={str(source): state}, _current_file=None,
                             _file_signature=lambda p: (12, 34))
    apply_to_window(window, {source: (b"[[B]]", b"[[B2]]\r\n")},
                    tmp_path / "B.md", tmp_path / "B2.md")
    assert doc.toPlainText() == "[[B2]]\n"
    assert not doc.isModified()
    assert state["source_signature"] == (12, 34)
    assert state["editing_newline"] == "\r\n"
