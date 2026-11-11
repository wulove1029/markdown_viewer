from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox

from app import export_actions


@pytest.mark.parametrize("choice", ["開啟檔案", "開啟所在資料夾", "關閉"])
def test_export_dialog_opens_selected_local_destination(qapp, monkeypatch, tmp_path, choice):
    destination = tmp_path / "中文 export.pdf"
    opened = []
    clicked = []

    def execute(box):
        assert {button.text() for button in box.buttons()} == {
            "開啟檔案", "開啟所在資料夾", "關閉"
        }
        clicked.append(next(button for button in box.buttons() if button.text() == choice))
        return 0

    monkeypatch.setattr(QMessageBox, "exec", execute)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: clicked[-1])
    monkeypatch.setattr(export_actions.QDesktopServices, "openUrl", opened.append)
    export_actions.show_export_complete(None, str(destination))
    expected = [] if choice == "關閉" else [
        destination if choice == "開啟檔案" else destination.parent
    ]
    assert [Path(url.toLocalFile()) for url in opened] == expected


def test_ppt_export_calls_shared_completion_only_after_success(qapp, monkeypatch, tmp_path):
    from app import fragment_render, pptx_export

    destination = tmp_path / "slides.pptx"
    calls = []
    window = SimpleNamespace(
        exporting=False, current_file=tmp_path / "note.md", edit_mode=False,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *args: None),
    )
    monkeypatch.setattr(export_actions, "_export_source_text", lambda w: "# Note")
    monkeypatch.setattr(export_actions.QFileDialog, "getSaveFileName",
                        lambda *args: (str(destination), ""))
    monkeypatch.setattr(fragment_render, "FragmentRenderer", lambda **kwargs: None)
    monkeypatch.setattr(pptx_export, "export_markdown_to_pptx", lambda *args, **kwargs: 1)
    monkeypatch.setattr(export_actions, "show_export_complete", lambda *args: calls.append(args))
    export_actions.export_pptx(window)
    assert calls == [(window, str(destination))]
    assert window.exporting is False


@pytest.mark.parametrize("rendered", [False, True])
def test_word_export_warns_when_fragments_stay_source(qapp, monkeypatch, tmp_path, rendered):
    """A Mermaid/math fragment the web engine could not render (for example a
    starved renderer on a busy machine) must never vanish without a word."""
    pytest.importorskip("docx")
    import pymupdf
    from docx import Document

    from app import fragment_render

    png = tmp_path / "fragment.png"
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 24))
    pix.clear_with(210)
    pix.save(str(png))
    destination = tmp_path / "note.docx"
    window = SimpleNamespace(
        exporting=False, current_file=tmp_path / "note.md", edit_mode=False,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *args: None),
    )
    cleaned = []
    renderer = SimpleNamespace(
        provide=lambda kind, source: str(png) if rendered else None,
        cleanup=lambda: cleaned.append(True),
    )
    warnings, completed = [], []
    monkeypatch.setattr(export_actions, "_export_source_text",
                        lambda w: "```mermaid\ngraph TD; A-->B;\n```\n\n$$x^2$$\n")
    monkeypatch.setattr(export_actions.QFileDialog, "getSaveFileName",
                        lambda *args: (str(destination), ""))
    monkeypatch.setattr(fragment_render, "FragmentRenderer", lambda **kwargs: renderer)
    monkeypatch.setattr(export_actions.QMessageBox, "warning",
                        lambda *args: warnings.append(args))
    monkeypatch.setattr(export_actions, "show_export_complete",
                        lambda *args: completed.append(args))

    export_actions.export_docx(window)

    assert completed == [(window, str(destination))] and cleaned == [True]
    assert window.exporting is False
    images = len(Document(str(destination)).inline_shapes)
    if rendered:
        assert images == 2 and warnings == []
    else:
        assert images == 0
        assert len(warnings) == 1
        message = warnings[0][2]
        assert "1 個 Mermaid 圖表" in message and "1 個數學式" in message
        assert "未能轉成圖片" in message
