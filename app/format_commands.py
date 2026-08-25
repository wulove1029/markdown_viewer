"""Shared metadata for Markdown formatting command surfaces.

The toolbar, slash-command palette, floating selection toolbar, and shortcut
help should describe the same operations.  Text mutation remains in
``format_actions``; this module only owns presentation and discovery data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


CommandSurface = Literal["toolbar", "slash", "selection"]


@dataclass(frozen=True)
class FormatCommandSpec:
    action_id: str
    group: str
    toolbar_label: str
    title: str
    description: str
    keywords: tuple[str, ...] = ()
    shortcut: str = ""
    surfaces: tuple[CommandSurface, ...] = ("toolbar",)

    @property
    def tooltip(self) -> str:
        parts = [self.title]
        if self.shortcut:
            parts.append(self.shortcut)
        parts.append(self.description)
        return " · ".join(parts)

    @property
    def search_text(self) -> str:
        return " ".join(
            (self.action_id, self.title, self.description, *self.keywords)
        ).casefold()


FORMAT_COMMANDS: tuple[FormatCommandSpec, ...] = (
    FormatCommandSpec(
        "bold", "text", "B", "粗體", "以 ** 包住文字",
        ("bold", "strong", "粗體"), "Ctrl+B", ("toolbar", "selection"),
    ),
    FormatCommandSpec(
        "italic", "text", "I", "斜體", "以 * 包住文字",
        ("italic", "emphasis", "斜體"), "Ctrl+I", ("toolbar", "selection"),
    ),
    FormatCommandSpec(
        "strikethrough", "text", "S", "刪除線", "以 ~~ 包住文字",
        ("strike", "delete", "刪除線"), surfaces=("toolbar", "selection"),
    ),
    FormatCommandSpec(
        "h1", "heading", "H1", "標題 1", "大型章節標題",
        ("heading", "title", "一級標題", "標題1"), surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "h2", "heading", "H2", "標題 2", "中型章節標題",
        ("heading", "subtitle", "二級標題", "標題2"), surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "h3", "heading", "H3", "標題 3", "小型章節標題",
        ("heading", "三階標題", "標題3"), surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "bullet_list", "structure", "•", "項目清單", "插入無序清單",
        ("bullet", "unordered", "list", "項目", "清單"), "Ctrl+Shift+8",
        ("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "ordered_list", "structure", "1.", "編號清單", "插入有序清單",
        ("numbered", "ordered", "list", "編號", "清單"), "Ctrl+Shift+7",
        ("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "task_list", "structure", "☑", "待辦清單", "插入可勾選工作項目",
        ("task", "todo", "checklist", "待辦", "核取"),
        surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "quote", "structure", ">", "引用", "插入引用段落",
        ("quote", "blockquote", "引用"), surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "inline_code", "code", "<>", "行內程式碼", "以反引號包住文字",
        ("inline", "code", "程式碼"), surfaces=("toolbar", "selection"),
    ),
    FormatCommandSpec(
        "code_block", "code", "```", "程式碼區塊", "插入 fenced code block",
        ("code", "fence", "程式碼", "代碼"), surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "link", "insert", "連結", "連結", "插入 [文字](網址)",
        ("link", "url", "網址", "連結"), "Ctrl+K",
        ("toolbar", "slash", "selection"),
    ),
    FormatCommandSpec(
        "table", "insert", "田", "表格", "插入 Markdown 表格",
        ("table", "grid", "表格"), surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "hr", "insert", "—", "分隔線", "插入水平分隔線",
        ("horizontal", "rule", "divider", "分隔線"),
        surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "image", "resource", "圖片", "圖片", "選擇圖片並複製到 assets",
        ("image", "picture", "photo", "圖片", "照片"),
        surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "attachment", "resource", "附件", "附件", "加入檔案並建立相對連結",
        ("attachment", "file", "resource", "附件", "檔案", "資源"),
        surfaces=("slash",),
    ),
    FormatCommandSpec(
        "template", "resource", "範本", "範本", "插入筆記範本",
        ("template", "snippet", "範本", "模板", "片段"),
        surfaces=("slash",),
    ),
    FormatCommandSpec(
        "recent_resource", "resource", "最近", "最近資源", "再次插入最近使用的資源",
        ("recent", "resource", "history", "最近", "資源", "附件"),
        surfaces=("slash",),
    ),
    FormatCommandSpec(
        "mermaid", "resource", "圖表", "Mermaid 圖表", "插入 Mermaid 圖表區塊",
        ("mermaid", "diagram", "chart", "圖表", "流程圖"),
        surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "math_inline", "math", "$x$", "行內公式", "插入行內數學式",
        ("math", "latex", "formula", "公式"), surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "math_block", "math", "$$", "公式區塊", "插入獨立數學式區塊",
        ("math", "latex", "equation", "公式"), surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "wikilink", "reference", "[[]]", "Wiki 連結", "連結其他筆記",
        ("wiki", "note", "筆記", "內部連結"), surfaces=("toolbar", "slash"),
    ),
    FormatCommandSpec(
        "highlight", "reference", "醒目", "醒目標示", "以 <mark> 標示文字",
        ("highlight", "mark", "螢光", "醒目"),
        surfaces=("toolbar", "selection"),
    ),
)


def command_for(action_id: str) -> FormatCommandSpec:
    for command in FORMAT_COMMANDS:
        if command.action_id == action_id:
            return command
    raise KeyError(action_id)


def commands_for(surface: CommandSurface) -> tuple[FormatCommandSpec, ...]:
    return tuple(command for command in FORMAT_COMMANDS if surface in command.surfaces)


def filter_commands(
    surface: CommandSurface, query: str
) -> tuple[FormatCommandSpec, ...]:
    """Return commands matching a Chinese or English slash query."""
    commands = commands_for(surface)
    normalized = query.strip().casefold()
    if not normalized:
        return commands

    ranked: list[tuple[int, int, FormatCommandSpec]] = []
    for index, command in enumerate(commands):
        title = command.title.casefold()
        keywords = tuple(keyword.casefold() for keyword in command.keywords)
        if title.startswith(normalized):
            rank = 0
        elif any(keyword.startswith(normalized) for keyword in keywords):
            rank = 1
        elif normalized in command.search_text:
            rank = 2
        else:
            continue
        ranked.append((rank, index, command))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(item[2] for item in ranked)
