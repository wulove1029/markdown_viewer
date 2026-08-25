"""Opening policy for local attachments clicked from rendered notes."""

from __future__ import annotations

from pathlib import Path


BLOCKED_EXTENSIONS = frozenset(
    {
        ".appref-ms", ".application", ".bat", ".cmd", ".com", ".cpl",
        ".exe", ".gadget", ".hta", ".inf", ".ins", ".isp", ".jar",
        ".js", ".jse", ".lnk", ".msc", ".msi", ".msp", ".mst",
        ".pif", ".ps1", ".psm1", ".py", ".pyw", ".reg", ".scr",
        ".sh", ".url", ".vb", ".vbe", ".vbs", ".ws", ".wsc",
        ".wsf", ".wsh",
    }
)

SAFE_EXTENSIONS = frozenset(
    {
        ".7z", ".aac", ".avi", ".bmp", ".csv", ".doc", ".docx",
        ".epub", ".flac", ".gif", ".jpeg", ".jpg", ".json", ".m4a",
        ".md", ".markdown", ".mkv", ".mov", ".mp3", ".mp4", ".odp",
        ".ods", ".odt", ".pdf", ".png", ".ppt", ".pptx", ".rar",
        ".rtf", ".svg", ".tar", ".text", ".tif", ".tiff", ".tsv",
        ".txt", ".wav", ".webm", ".webp", ".xls", ".xlsx", ".xml",
        ".yaml", ".yml", ".zip",
    }
)


def attachment_open_policy(path: str | Path) -> str:
    """Return ``open``, ``confirm``, or ``block`` for a local attachment."""

    suffix = Path(path).suffix.casefold()
    if suffix in BLOCKED_EXTENSIONS:
        return "block"
    if suffix in SAFE_EXTENSIONS:
        return "open"
    return "confirm"
