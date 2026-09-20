import pytest

from app.frontmatter_properties import PropertiesDocument
from app.properties_panel import PropertiesPanel
from tests import test_editor_data_safety as safety
from tests.test_editor_workspace_integration import _enter_markdown_editor

_isolated_editor_dependencies = safety._isolated_editor_dependencies
make_data_safety_window = safety.make_data_safety_window


def test_panel_edits_value_and_preserves_body(make_data_safety_window, tmp_path):
    path = tmp_path / "note.md"
    raw = b"---\ntitle: old\n---\n# body\r\nend  \n"
    path.write_bytes(raw)
    window = make_data_safety_window()
    window.open_path(str(path))
    panel = window._properties_panel
    panel.set_document(path)
    panel._table.item(0, 1).setText('"new"')
    panel._save_properties()
    result = PropertiesDocument.parse(path.read_bytes())
    assert result.values["title"] == "new"
    assert result.body_bytes == PropertiesDocument.parse(raw).body_bytes
    assert path.with_name("note.md.bak").read_bytes() == raw


def test_invalid_frontmatter_disables_save(qapp, tmp_path):
    path = tmp_path / "note.md"
    raw = b"---\ninvalid: [\n---\nbody"
    path.write_bytes(raw)
    panel = PropertiesPanel(lambda *args: pytest.fail("must not write invalid YAML"))
    panel.set_document(path)
    assert not panel._save.isEnabled()
    assert path.read_bytes() == raw
    panel.close()


def test_properties_cannot_overwrite_dirty_editor_or_external_change(
    make_data_safety_window, tmp_path,
):
    path = tmp_path / "note.md"
    raw = b"# body"
    path.write_bytes(raw)
    window = make_data_safety_window()
    _enter_markdown_editor(window, path)
    safety._replace_with_dirty_text(window, "draft")
    with pytest.raises(OSError, match="編輯模式"):
        window._save_properties(path, raw, b"replacement")
    assert path.read_bytes() == raw
    assert window._editor.toPlainText() == "draft"


def test_stale_properties_snapshot_refuses_external_overwrite(make_data_safety_window, tmp_path):
    path = tmp_path / "note.md"
    raw = b"# body"
    path.write_bytes(raw)
    window = make_data_safety_window()
    window.open_path(str(path))
    window._fs_watcher.blockSignals(True)
    path.write_bytes(b"external")
    with pytest.raises(OSError, match="磁碟文件已變更"):
        window._save_properties(path, raw, b"replacement")
    assert path.read_bytes() == b"external"
