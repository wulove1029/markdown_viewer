"""In-memory tab contract; optional keys preserve legacy/session behavior."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from PySide6.QtGui import QTextDocument


class TabState(TypedDict, total=False):
    kind: str
    view_mode: str
    edit_backend: str
    editor_document: QTextDocument | None
    editing_encoding: str
    editing_newline: str
    source_signature: tuple[int, int] | None
    pending_recovery: bool
    source_deleted: bool
    wysiwyg_parked: bool
    office_warning_ack: str
    office_warning_pending_ack: str
    cursor: int
    anchor: int
    scroll: int | None
    editor_scroll: int
    preview_scroll_ratio: float
