"""Real WebEngine regression coverage for continuous-page PDF export.

These tests are opt-in for the same reason as the other WebEngine suites:
headless Chromium can terminate the whole pytest process on some machines.
Run with ``RUN_WEBENGINE_TESTS=1``.
"""

import os

import fitz
import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage

from app import export_actions
from app.renderer import RendererView


_skip_webengine = pytest.mark.skipif(
    os.environ.get("RUN_WEBENGINE_TESTS") != "1",
    reason="headless WebEngine is flaky; set RUN_WEBENGINE_TESTS=1 to run",
)


def _wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _content_size(view):
    box = {}
    loop = QEventLoop()

    def done(value):
        box["value"] = value
        loop.quit()

    view.content_size(done)
    QTimer.singleShot(5000, loop.quit)
    loop.exec()
    return box.get("value")


class _ExportOwner:
    def __init__(self, renderer, path, done):
        self._renderer = renderer
        self._pending_pdf_path = str(path)
        self._on_pdf_exported = done


@_skip_webengine
def test_tall_image_document_exports_as_exactly_one_page(qapp, tmp_path, monkeypatch):
    image_path = tmp_path / "phone.png"
    image = QImage(323, 677, QImage.Format.Format_RGB32)
    image.fill(QColor("#f2f3f5"))
    assert image.save(str(image_path))

    sections = []
    for index in range(1, 31):
        sections.append(
            f"## SECTION-{index}\n\nTwo short lines for section {index}.\n\n"
            "![](phone.png)"
        )
    markdown_path = tmp_path / "manual.md"
    markdown_path.write_text("\n\n".join(sections), encoding="utf-8")

    view = RendererView()
    view.resize(900, 700)
    view.show()
    view.load_file(markdown_path)
    _wait(5000)

    dims = _content_size(view)
    assert dims is not None
    assert dims[1] > 20000

    output_path = tmp_path / "continuous.pdf"
    result = {}
    pdf_loop = QEventLoop()

    def finished(path, ok):
        result.update(path=path, ok=ok)
        pdf_loop.quit()

    owner = _ExportOwner(view, output_path, finished)
    monkeypatch.setattr(export_actions, "show_pdf_progress", lambda *_args: None)
    export_actions.export_single_page(owner, dims)
    QTimer.singleShot(30000, pdf_loop.quit)
    pdf_loop.exec()

    assert result == {"path": str(output_path), "ok": True}
    with fitz.open(output_path) as document:
        assert document.page_count == 1
        page = document[0]
        assert page.rect.width == pytest.approx(
            max(view.width(), dims[0]) * 72 / 96, abs=1
        )
        assert page.rect.height == pytest.approx((dims[1] + 4) * 72 / 96, abs=1)

    view.close()
