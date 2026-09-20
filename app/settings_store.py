"""Stable settings identity and backward-compatible application data paths."""

from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings

ORG = "markdown-viewer"
APP = "MarkdownViewer"
DISPLAY_NAME = "Markdown Viewer"


def settings() -> QSettings:
    return QSettings(ORG, APP)


def legacy_data_path(base: str) -> Path:
    """Keep existing libraries, tags, recovery drafts and logs in place.

    Qt includes applicationName in AppDataLocation. Aligning the registry
    identity must not silently move these previously persisted JSON files.
    Test/embedded application identities keep their own supplied data path.
    """
    path = Path(base or ".")
    if (base and QCoreApplication.applicationName() == APP
            and QCoreApplication.organizationName() == ORG):
        return path.parent / DISPLAY_NAME
    return path
