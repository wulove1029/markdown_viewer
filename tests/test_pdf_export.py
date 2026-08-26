from pathlib import Path

import pytest
from PySide6.QtCore import QMarginsF
from PySide6.QtGui import QPageLayout, QPageSize

from app import edit_backend, export_actions
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


class _Signal:
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def disconnect(self, callback):
        self.callbacks.remove(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class _OfficePage:
    def __init__(self, events):
        self.pdfPrintingFinished = _Signal()
        self.print_calls = []
        self.events = events
        self.raise_on_print = False

    def printToPdf(self, path, layout):
        self.events.append("print")
        self.print_calls.append((path, layout))
        if self.raise_on_print:
            raise RuntimeError("page was destroyed")


class _OfficeView:
    def __init__(self, events, width=960):
        self._page = _OfficePage(events)
        self._width = width
        self.events = events
        self.prepare_callback = None
        self.finish_count = 0

    def width(self):
        return self._width

    def page(self):
        return self._page

    def prepare_pdf_export(self, callback):
        self.events.append("prepare")
        self.prepare_callback = callback

    def finish_pdf_export(self):
        self.events.append("finish")
        self.finish_count += 1


class _Window:
    def __init__(self, width=1536):
        self._renderer = _Renderer(width)
        self._pending_pdf_path = "manual.pdf"
        self._on_pdf_exported = object()
        self._export_btn = _Button()
        self._current_file = Path("manual.md")
        self._edit_mode = False
        self._active_edit_backend = edit_backend.SOURCE_BACKEND
        self._wysiwyg_view = None
        self.refresh_count = 0

    def _refresh_icons(self):
        self.refresh_count += 1


def _start_pdf_export(monkeypatch, window, setup, events):
    completed = []
    monkeypatch.setattr(export_actions, "ask_page_setup", lambda _owner: setup)
    monkeypatch.setattr(
        export_actions.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: ("chosen.pdf", "PDF 檔案 (*.pdf)"),
    )
    monkeypatch.setattr(
        export_actions,
        "show_pdf_progress",
        lambda owner: events.append("progress"),
    )
    monkeypatch.setattr(
        export_actions,
        "on_pdf_exported",
        lambda owner, path, ok: (
            events.append("complete"),
            completed.append((owner, path, ok)),
        ),
    )
    export_actions.export_pdf(window)
    return completed


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


@pytest.mark.parametrize(
    "prepared",
    [True, {"ok": True, "width": 840, "height": 2400}],
)
def test_office_pdf_waits_for_prepare_then_finishes_once_after_print(
    monkeypatch, prepared
):
    events = []
    window = _Window()
    window._edit_mode = True
    window._active_edit_backend = edit_backend.WYSIWYG_BACKEND
    window._wysiwyg_view = _OfficeView(events)

    completed = _start_pdf_export(
        monkeypatch,
        window,
        {"size": "A4", "orientation": "portrait"},
        events,
    )

    assert events == ["progress", "prepare"]
    assert window._wysiwyg_view._page.print_calls == []
    assert completed == []

    window._wysiwyg_view.prepare_callback(prepared)

    assert events == ["progress", "prepare", "print"]
    assert window._wysiwyg_view.finish_count == 0
    path, layout = window._wysiwyg_view._page.print_calls[0]
    assert path == "chosen.pdf"
    assert layout.pageSize().id() == QPageSize.PageSizeId.A4

    window._wysiwyg_view._page.pdfPrintingFinished.emit("chosen.pdf", True)

    assert events == ["progress", "prepare", "print", "finish", "complete"]
    assert window._wysiwyg_view.finish_count == 1
    assert completed == [(window, "chosen.pdf", True)]
    assert window._wysiwyg_view._page.pdfPrintingFinished.callbacks == []

    # A stale/duplicate completion cannot clean up or notify twice.
    window._wysiwyg_view._page.pdfPrintingFinished.emit("chosen.pdf", True)
    window._wysiwyg_view.prepare_callback(prepared)
    assert window._wysiwyg_view.finish_count == 1
    assert len(completed) == 1


@pytest.mark.parametrize("prepared", [None, False, {"ok": False}])
def test_office_pdf_prepare_failure_never_prints_and_still_cleans_up(
    monkeypatch, prepared
):
    events = []
    window = _Window()
    window._edit_mode = True
    window._active_edit_backend = edit_backend.WYSIWYG_BACKEND
    window._wysiwyg_view = _OfficeView(events)
    completed = _start_pdf_export(
        monkeypatch,
        window,
        {"size": "A4", "orientation": "portrait"},
        events,
    )

    window._wysiwyg_view.prepare_callback(prepared)

    assert window._wysiwyg_view._page.print_calls == []
    assert window._wysiwyg_view.finish_count == 1
    assert completed == [(window, "chosen.pdf", False)]
    assert events == ["progress", "prepare", "finish", "complete"]


@pytest.mark.parametrize("printed_ok", [True, False])
def test_office_pdf_print_result_always_restores_live_surface(
    monkeypatch, printed_ok
):
    events = []
    window = _Window()
    window._edit_mode = True
    window._active_edit_backend = edit_backend.WYSIWYG_BACKEND
    window._wysiwyg_view = _OfficeView(events)
    completed = _start_pdf_export(
        monkeypatch,
        window,
        {"size": "Letter", "orientation": "landscape"},
        events,
    )
    window._wysiwyg_view.prepare_callback(True)

    window._wysiwyg_view._page.pdfPrintingFinished.emit(
        "chosen.pdf", printed_ok
    )

    assert window._wysiwyg_view.finish_count == 1
    assert completed == [(window, "chosen.pdf", printed_ok)]


def test_office_pdf_print_exception_restores_live_surface(monkeypatch):
    events = []
    window = _Window()
    window._edit_mode = True
    window._active_edit_backend = edit_backend.WYSIWYG_BACKEND
    window._wysiwyg_view = _OfficeView(events)
    window._wysiwyg_view._page.raise_on_print = True
    completed = _start_pdf_export(
        monkeypatch,
        window,
        {"size": "A4", "orientation": "portrait"},
        events,
    )

    window._wysiwyg_view.prepare_callback(True)

    assert window._wysiwyg_view.finish_count == 1
    assert completed == [(window, "chosen.pdf", False)]
    assert window._wysiwyg_view._page.pdfPrintingFinished.callbacks == []


def test_office_single_page_uses_prepared_content_dimensions(monkeypatch):
    events = []
    window = _Window()
    window._edit_mode = True
    window._active_edit_backend = edit_backend.WYSIWYG_BACKEND
    window._wysiwyg_view = _OfficeView(events, width=960)
    completed = _start_pdf_export(
        monkeypatch,
        window,
        {"size": "single", "orientation": "portrait"},
        events,
    )

    window._wysiwyg_view.prepare_callback(
        {"ok": True, "width": 1200, "height": 30000}
    )

    assert completed == []
    _path, layout = window._wysiwyg_view._page.print_calls[0]
    points = layout.pageSize().size(QPageSize.Unit.Point)
    assert points.width() == pytest.approx(1200 * 72 / 96)
    assert points.height() == pytest.approx((30000 + 4) * 72 / 96)
    assert layout.margins(QPageLayout.Unit.Point) == QMarginsF(0, 0, 0, 0)


def test_office_single_page_does_not_expand_to_view_width(monkeypatch):
    events = []
    window = _Window()
    window._edit_mode = True
    window._active_edit_backend = edit_backend.WYSIWYG_BACKEND
    window._wysiwyg_view = _OfficeView(events, width=900)
    _start_pdf_export(
        monkeypatch,
        window,
        {"size": "single", "orientation": "portrait"},
        events,
    )

    window._wysiwyg_view.prepare_callback(
        {"ok": True, "width": 620, "height": 2400}
    )

    _path, layout = window._wysiwyg_view._page.print_calls[0]
    points = layout.pageSize().size(QPageSize.Unit.Point)
    assert points.width() == pytest.approx(620 * 72 / 96)


@pytest.mark.parametrize(
    "prepared",
    [True, {"ok": True, "width": 800, "height": 0}],
)
def test_office_single_page_rejects_missing_or_invalid_prepared_dimensions(
    monkeypatch, prepared
):
    events = []
    window = _Window()
    window._edit_mode = True
    window._active_edit_backend = edit_backend.WYSIWYG_BACKEND
    window._wysiwyg_view = _OfficeView(events)
    completed = _start_pdf_export(
        monkeypatch,
        window,
        {"size": "single", "orientation": "portrait"},
        events,
    )

    window._wysiwyg_view.prepare_callback(prepared)

    assert window._wysiwyg_view._page.print_calls == []
    assert window._wysiwyg_view.finish_count == 1
    assert completed == [(window, "chosen.pdf", False)]


def test_renderer_pdf_path_does_not_use_office_prepare(monkeypatch):
    events = []
    window = _Window()
    inactive_office_view = _OfficeView(events)
    window._wysiwyg_view = inactive_office_view

    completed = _start_pdf_export(
        monkeypatch,
        window,
        {"size": "A3", "orientation": "landscape"},
        events,
    )

    assert completed == []
    assert events == ["progress"]
    assert inactive_office_view.prepare_callback is None
    assert inactive_office_view.finish_count == 0
    assert len(window._renderer.exports) == 1
    path, callback, layout = window._renderer.exports[0]
    assert path == "chosen.pdf"
    assert callback is window._on_pdf_exported
    assert layout.pageSize().id() == QPageSize.PageSizeId.A3
    assert layout.orientation() == QPageLayout.Orientation.Landscape


def test_window_office_pdf_enters_live_snapshot_gate_before_export(monkeypatch):
    from app.window import MainWindow

    events = []

    class _Owner:
        _edit_mode = True
        _active_edit_backend = edit_backend.WYSIWYG_BACKEND

        def _request_live_wysiwyg_snapshot(self, continuation, *, purpose):
            events.append(("snapshot", purpose))
            self.continuation = continuation

    owner = _Owner()
    monkeypatch.setattr(
        export_actions,
        "export_pdf",
        lambda received: events.append(("export", received)),
    )

    MainWindow._export_pdf(owner)

    assert events == [("snapshot", "匯出 PDF")]
    owner.continuation()
    assert events == [
        ("snapshot", "匯出 PDF"),
        ("export", owner),
    ]


@pytest.mark.parametrize("ok", [False, True])
def test_office_pdf_completion_keeps_export_available(monkeypatch, ok):
    window = _Window()
    window._edit_mode = True
    window._active_edit_backend = edit_backend.WYSIWYG_BACKEND
    window._wysiwyg_view = _OfficeView([])
    window._pdf_progress = None

    class _StatusBar:
        def showMessage(self, *_args):
            pass

        def clearMessage(self):
            pass

    class _MessageBox:
        class Icon:
            Information = object()

        class ButtonRole:
            AcceptRole = object()
            RejectRole = object()

        def __init__(self, *_args):
            self._buttons = []

        def setWindowTitle(self, *_args):
            pass

        def setIcon(self, *_args):
            pass

        def setText(self, *_args):
            pass

        def addButton(self, *_args):
            button = object()
            self._buttons.append(button)
            return button

        def exec(self):
            pass

        def clickedButton(self):
            return None

        @staticmethod
        def warning(*_args):
            pass

    window.statusBar = lambda: _StatusBar()
    monkeypatch.setattr(export_actions, "QMessageBox", _MessageBox)

    export_actions.on_pdf_exported(window, "chosen.pdf", ok)

    assert window._export_btn.enabled is True
    assert window.refresh_count == 1


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
