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
        # Also execute code or run installers/shell handlers when opened.
        ".appx", ".appxbundle", ".chm", ".diagcab", ".hlp", ".library-ms",
        ".msix", ".msixbundle", ".scf", ".search-ms", ".searchconnector-ms",
        ".settingcontent-ms", ".website", ".xll",
    }
)

# Opened without a confirmation prompt.  Legacy binary Office formats
# (.doc/.xls/.ppt, macros and OLE exploits), RTF (OLE/equation-editor
# exploits), SVG (script in the browser's file:// origin) and XML (handler
# chosen by processing instructions) were removed: they now ask first.
SAFE_EXTENSIONS = frozenset(
    {
        ".7z", ".aac", ".avi", ".bmp", ".csv", ".docx",
        ".epub", ".flac", ".gif", ".jpeg", ".jpg", ".json", ".m4a",
        ".md", ".markdown", ".mkv", ".mov", ".mp3", ".mp4", ".odp",
        ".ods", ".odt", ".pdf", ".png", ".pptx", ".rar",
        ".tar", ".text", ".tif", ".tiff", ".tsv",
        ".txt", ".wav", ".webm", ".webp", ".xlsx",
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
