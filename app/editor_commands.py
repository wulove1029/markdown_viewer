"""Capabilities shared by host menus and the active editor backend."""

OFFICE_FORMAT_COMMANDS = frozenset({
    "bold", "italic", "strikethrough", "link", "bullet_list", "ordered_list",
    "task_list", "table", "quote", "code_block",
})
RESOURCE_COMMANDS = frozenset({"image", "attachment", "template", "recent_resource"})


def supports_office(command: str) -> bool:
    return command in OFFICE_FORMAT_COMMANDS or command in RESOURCE_COMMANDS
