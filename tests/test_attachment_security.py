import pytest

from app.attachment_security import attachment_open_policy


@pytest.mark.parametrize(
    "name",
    ["run.exe", "script.BAT", "shortcut.lnk", "setup.ps1", "payload.py"],
)
def test_executable_or_script_attachments_are_blocked(name):
    assert attachment_open_policy(name) == "block"


@pytest.mark.parametrize(
    "name", ["manual.pdf", "sheet.xlsx", "photo.png", "archive.zip", "note.txt"]
)
def test_common_document_attachments_open_normally(name):
    assert attachment_open_policy(name) == "open"


def test_unknown_attachment_requires_confirmation():
    assert attachment_open_policy("board.custom-format") == "confirm"
    assert attachment_open_policy("README") == "confirm"
