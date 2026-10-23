"""Explicit complete-source fallback for Markdown beyond the preview budget."""

from pathlib import Path

from .file_types import is_markdown
from .md_converter import MAX_PREVIEW_BYTES

NOTICE = (
    "大文件來源模式：全文已載入，可編輯與儲存；"
    "預覽、Office 與語法標色暫停，以減少排版負擔。"
)


def requires_source(path: Path | None) -> bool:
    if path is None or not is_markdown(path):
        return False
    try:
        return Path(path).stat().st_size > MAX_PREVIEW_BYTES
    except OSError:
        return False
