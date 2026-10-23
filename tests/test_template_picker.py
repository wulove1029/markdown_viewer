"""Template preview matches inserted source and handles changed/missing files."""

from datetime import datetime

from PySide6.QtWidgets import QDialog

from app.template_picker import TemplatePickerDialog


def _template(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def test_recent_order_rendered_variables_and_search(qapp, tmp_path):
    first = _template(tmp_path / "first.md", "first")
    recent = _template(tmp_path / "meeting.md", "# {{title}}\n{{date}} {{time}}")
    dialog = TemplatePickerDialog(
        [first, recent], title="新筆記😀", recent=[recent], folder=tmp_path,
        now=datetime(2026, 10, 22, 9, 5),
    )
    try:
        dialog.show()
        qapp.processEvents()
        assert dialog.selected_template() == recent
        assert dialog.templates.item(0).text() == "最近｜meeting.md"
        assert dialog.rendered_text() == "# 新筆記😀\n2026-10-22 09:05"
        assert dialog.source.toPlainText() == dialog.rendered_text()
        assert "新筆記😀" in dialog.preview.toPlainText()
        assert not dialog.grab().isNull()
        dialog.search.setText("first")
        assert dialog.selected_template() == first
        assert dialog.rendered_text() == "first"
        dialog.search.setText("no matching template")
        assert dialog.selected_template() is None
        assert not dialog.insert_button.isEnabled()
    finally:
        dialog.close()


def test_changed_template_refreshes_preview_before_acceptance(qapp, tmp_path):
    path = _template(tmp_path / "template.md", "before")
    dialog = TemplatePickerDialog([path], title="note")
    try:
        path.write_text("after", encoding="utf-8")
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.rendered_text() == "after"
        assert "範本已變更" in dialog.message.text()
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.rendered_text() == "after"
    finally:
        dialog.close()


def test_unreadable_or_removed_template_cannot_be_inserted(qapp, tmp_path):
    path = tmp_path / "bad.md"
    path.write_bytes(b"\xff\xfe")
    dialog = TemplatePickerDialog([path], title="note")
    try:
        assert not dialog.insert_button.isEnabled()
        assert "無法讀取範本" in dialog.message.text()
        dialog.accept()
        assert dialog.result() != QDialog.DialogCode.Accepted
    finally:
        dialog.close()
