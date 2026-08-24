from pathlib import Path

import pytest
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize

from app import export_actions
from app.renderer import _decode_content_size


class _Button:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, enabled):
        self.enabled = bool(enabled)


class _Renderer:
    def __init__(self, width=1536):
        self._width = width
        self.exports = []

    def width(self):
        return self._width

    def export_pdf(self, path, callback, layout):
        self.exports.append((path, callback, layout))


class _Window:
    def __init__(self, width=1536):
        self._renderer = _Renderer(width)
        self._pending_pdf_path = "manual.pdf"
        self._on_pdf_exported = object()
        self._export_btn = _Button()
        self._current_file = Path("manual.md")
        self._edit_mode = False
        self.refresh_count = 0

    def _refresh_icons(self):
        self.refresh_count += 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("[1526,29485]", [1526.0, 29485.0]),
        ([800, 1200], [800.0, 1200.0]),
    ],
)
def test_decode_content_size_accepts_serialized_and_native_arrays(raw, expected):
    assert _decode_content_size(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "not-json", None, [], [800], [0, 1200], [800, float("nan")]],
)
def test_decode_content_size_rejects_invalid_measurements(raw):
    assert _decode_content_size(raw) is None


def test_single_page_uses_the_full_measured_height_even_above_200_inches(
    monkeypatch,
):
    window = _Window()
    progress_calls = []
    monkeypatch.setattr(
        export_actions, "show_pdf_progress", lambda owner: progress_calls.append(owner)
    )
    monkeypatch.setattr(
        export_actions.QMessageBox,
        "question",
        lambda *_args, **_kwargs: pytest.fail(
            "a valid long page must not be downgraded to paginated A4"
        ),
    )

    export_actions.export_single_page(window, [1526, 29485])

    assert progress_calls == [window]
    assert len(window._renderer.exports) == 1
    path, callback, layout = window._renderer.exports[0]
    assert path == "manual.pdf"
    assert callback is window._on_pdf_exported
    assert layout.orientation() == QPageLayout.Orientation.Portrait
    assert layout.margins(QPageLayout.Unit.Point) == QMarginsF(0, 0, 0, 0)
    points = layout.pageSize().size(QPageSize.Unit.Point)
    assert points.width() == pytest.approx(1536 * 72 / 96)
    assert points.height() == pytest.approx((29485 + 4) * 72 / 96)
    assert points.height() > 14000
    assert window._pending_pdf_path is None


@pytest.mark.parametrize("dims", [None, "", [], [800], [800, 0]])
def test_single_page_aborts_instead_of_silently_using_viewport_height(
    monkeypatch, dims
):
    window = _Window()
    warnings = []
    monkeypatch.setattr(
        export_actions.QMessageBox,
        "warning",
        lambda *args: warnings.append(args),
    )
    monkeypatch.setattr(
        export_actions,
        "show_pdf_progress",
        lambda *_args: pytest.fail("invalid measurements must not start an export"),
    )

    export_actions.export_single_page(window, dims)

    assert window._renderer.exports == []
    assert window._pending_pdf_path is None
    assert window._export_btn.enabled is True
    assert window.refresh_count == 1
    assert len(warnings) == 1
    assert "頁面尺寸" in warnings[0][2]
