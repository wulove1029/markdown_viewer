"""Declarative keyboard-shortcut registry used by the window and help UI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShortcutSpec:
    """One user-facing keyboard command.

    ``handler`` is set only for shortcuts registered on ``MainWindow``.  The
    contextual entries describe bindings owned by focused widgets or the
    rendered preview, so the help dialog can cover them without creating a
    second, conflicting shortcut.
    """

    command_id: str
    group: str
    label: str
    sequences: tuple[str, ...]
    scope: str
    handler: str | None = None

    @property
    def is_window_shortcut(self) -> bool:
        return self.handler is not None

    @property
    def menu_hint(self) -> str:
        return self.sequences[0]


# These entries are the single source of truth for MainWindow QShortcuts,
# their menu hints, and their rows in the keyboard-shortcut dialog.
WINDOW_SHORTCUTS: tuple[ShortcutSpec, ...] = (
    ShortcutSpec(
        "file.open",
        "文件",
        "開啟文件",
        ("Ctrl+O",),
        "主視窗",
        "_panel_open_file",
    ),
    ShortcutSpec(
        "file.quick_open",
        "文件",
        "快速開啟文件",
        ("Ctrl+P",),
        "主視窗",
        "_quick_open",
    ),
    ShortcutSpec(
        "file.daily_note",
        "文件",
        "開啟今日筆記",
        ("Ctrl+D",),
        "主視窗",
        "_open_daily_note",
    ),
    ShortcutSpec(
        "file.export_pdf",
        "文件",
        "匯出目前 Markdown 為 PDF",
        ("Ctrl+Shift+P",),
        "Markdown 預覽",
        "_export_pdf",
    ),
    ShortcutSpec(
        "tabs.next",
        "分頁",
        "下一個分頁",
        ("Ctrl+Tab",),
        "多分頁",
        "_next_tab",
    ),
    ShortcutSpec(
        "tabs.previous",
        "分頁",
        "上一個分頁",
        ("Ctrl+Shift+Tab",),
        "多分頁",
        "_prev_tab",
    ),
    ShortcutSpec(
        "tabs.close",
        "分頁",
        "關閉目前分頁",
        ("Ctrl+W",),
        "有文件",
        "_close_current_tab",
    ),
    ShortcutSpec(
        "edit.toggle",
        "編輯與搜尋",
        "切換編輯／預覽",
        ("Ctrl+E",),
        "Markdown",
        "_toggle_edit_mode",
    ),
    ShortcutSpec(
        "edit.split",
        "編輯與搜尋",
        "切換並排編輯與即時預覽",
        ("Ctrl+Shift+E",),
        "Markdown",
        "_toggle_split_mode",
    ),
    ShortcutSpec(
        "edit.save",
        "編輯與搜尋",
        "儲存目前修改",
        ("Ctrl+S",),
        "編輯模式",
        "_save_edits",
    ),
    ShortcutSpec(
        "search.current",
        "編輯與搜尋",
        "搜尋目前文件；編輯模式為尋找／取代",
        ("Ctrl+F",),
        "有文件",
        "_toggle_search",
    ),
    ShortcutSpec(
        "search.library",
        "編輯與搜尋",
        "搜尋所有文件庫內容",
        ("Ctrl+Shift+F",),
        "文件庫",
        "_open_global_search",
    ),
    ShortcutSpec(
        "view.graph",
        "檢視與工具",
        "開啟筆記關聯圖",
        ("Ctrl+G",),
        "文件庫",
        "_open_graph_view",
    ),
    ShortcutSpec(
        "view.zoom_in",
        "檢視與工具",
        "放大內容",
        ("Ctrl++", "Ctrl+="),
        "預覽／PDF",
        "_zoom_in",
    ),
    ShortcutSpec(
        "view.zoom_out",
        "檢視與工具",
        "縮小內容",
        ("Ctrl+-",),
        "預覽／PDF",
        "_zoom_out",
    ),
    ShortcutSpec(
        "view.zoom_reset",
        "檢視與工具",
        "重設內容縮放",
        ("Ctrl+0",),
        "預覽／PDF",
        "_zoom_reset",
    ),
    ShortcutSpec(
        "tools.mermaid_workspace",
        "檢視與工具",
        "開啟 Mermaid 工作區",
        ("Ctrl+Shift+M",),
        "主視窗",
        "_open_mermaid_workspace",
    ),
)


# Explicit keyboard handling outside MainWindow's WindowShortcut context.
# Keeping these entries here makes the help window complete while their
# context labels explain why the same key can perform different operations.
CONTEXT_SHORTCUTS: tuple[ShortcutSpec, ...] = (
    ShortcutSpec(
        "search.next",
        "編輯與搜尋",
        "前往下一個搜尋結果",
        ("Enter",),
        "搜尋列",
    ),
    ShortcutSpec(
        "search.previous",
        "編輯與搜尋",
        "返回上一個搜尋結果",
        ("Shift+Enter",),
        "搜尋列",
    ),
    ShortcutSpec(
        "search.close",
        "編輯與搜尋",
        "關閉目前搜尋列",
        ("Esc",),
        "搜尋開啟時",
    ),
    ShortcutSpec(
        "search.library_submit",
        "編輯與搜尋",
        "執行文件庫搜尋",
        ("Enter",),
        "文件庫搜尋",
    ),
    ShortcutSpec(
        "search.replace_one",
        "編輯與搜尋",
        "取代目前找到的一筆",
        ("Enter",),
        "取代欄位",
    ),
    ShortcutSpec(
        "editor.paste_image",
        "編輯與搜尋",
        "貼上圖片並插入 Markdown 圖片連結",
        ("Ctrl+V",),
        "Markdown 編輯器",
    ),
    ShortcutSpec(
        "quick_open.navigate",
        "快速開啟與連結建議",
        "移動目前選取項目",
        ("↑", "↓"),
        "快速開啟",
    ),
    ShortcutSpec(
        "quick_open.accept",
        "快速開啟與連結建議",
        "開啟選取的文件",
        ("Enter",),
        "快速開啟",
    ),
    ShortcutSpec(
        "quick_open.close",
        "快速開啟與連結建議",
        "關閉快速開啟",
        ("Esc",),
        "快速開啟",
    ),
    ShortcutSpec(
        "wikilink.navigate",
        "快速開啟與連結建議",
        "移動 Wiki 連結建議",
        ("↑", "↓"),
        "Wiki 建議",
    ),
    ShortcutSpec(
        "wikilink.accept",
        "快速開啟與連結建議",
        "套用 Wiki 連結建議",
        ("Enter", "Tab", "Shift+Tab"),
        "Wiki 建議",
    ),
    ShortcutSpec(
        "wikilink.close",
        "快速開啟與連結建議",
        "關閉 Wiki 連結建議",
        ("Esc",),
        "Wiki 建議",
    ),
    ShortcutSpec(
        "inline.commit",
        "預覽內編輯與標註",
        "儲存預覽內的編輯",
        ("Ctrl+Enter",),
        "預覽內編輯",
    ),
    ShortcutSpec(
        "inline.cancel",
        "預覽內編輯與標註",
        "取消預覽內的編輯",
        ("Esc",),
        "預覽內編輯",
    ),
    ShortcutSpec(
        "inline.paste_image",
        "預覽內編輯與標註",
        "貼上剪貼簿圖片",
        ("Ctrl+V",),
        "預覽內編輯",
    ),
    ShortcutSpec(
        "table.move",
        "預覽內編輯與標註",
        "移至下一格／上一格",
        ("Tab", "Shift+Tab"),
        "表格編輯",
    ),
    ShortcutSpec(
        "table.next_row",
        "預覽內編輯與標註",
        "移至同欄下一列",
        ("Enter",),
        "表格編輯",
    ),
    ShortcutSpec(
        "table.line_break",
        "預覽內編輯與標註",
        "在儲存格內插入換行",
        ("Shift+Enter",),
        "表格編輯",
    ),
    ShortcutSpec(
        "table.paste_grid",
        "預覽內編輯與標註",
        "貼入多格 TSV／試算表資料",
        ("Ctrl+V",),
        "表格編輯",
    ),
    ShortcutSpec(
        "annotation.commit_note",
        "預覽內編輯與標註",
        "儲存標註備註",
        ("Ctrl+Enter",),
        "標註備註",
    ),
    ShortcutSpec(
        "annotation.cancel_note",
        "預覽內編輯與標註",
        "取消編輯標註備註",
        ("Esc",),
        "標註備註",
    ),
    ShortcutSpec(
        "annotation.close_menu",
        "預覽內編輯與標註",
        "關閉目前的標註選單",
        ("Esc",),
        "預覽標註",
    ),
    ShortcutSpec(
        "annotation.delete",
        "預覽內編輯與標註",
        "刪除目前選取的標註",
        ("Delete",),
        "預覽標註",
    ),
    ShortcutSpec(
        "pdf.pointer_zoom",
        "PDF",
        "以游標位置放大／縮小",
        ("Ctrl+滾輪",),
        "PDF",
    ),
    ShortcutSpec(
        "pdf.copy",
        "PDF",
        "複製選取的文字",
        ("Ctrl+C", "Ctrl+Insert", "Copy 鍵"),
        "PDF 選取",
    ),
    ShortcutSpec(
        "pdf.highlight",
        "PDF",
        "螢光標記選取的文字",
        ("H",),
        "PDF 選取",
    ),
    ShortcutSpec(
        "pdf.undo_highlight",
        "PDF",
        "撤銷上一筆螢光標記",
        ("Ctrl+Z", "Alt+Backspace", "Undo 鍵"),
        "螢光筆模式",
    ),
    ShortcutSpec(
        "mermaid.delete",
        "Mermaid 視覺編輯",
        "刪除選取的節點或連線",
        ("Delete", "Backspace"),
        "Mermaid 畫布",
    ),
    ShortcutSpec(
        "mermaid.zoom_reset",
        "Mermaid 視覺編輯",
        "重設畫布縮放",
        ("Ctrl+0",),
        "Mermaid 畫布",
    ),
    ShortcutSpec(
        "mermaid.cancel_connect",
        "Mermaid 視覺編輯",
        "取消目前的連線操作",
        ("Esc",),
        "Mermaid 畫布",
    ),
    ShortcutSpec(
        "tags.create",
        "其他情境",
        "建立輸入的新標籤",
        ("Enter",),
        "管理標籤",
    ),
)


ALL_SHORTCUTS: tuple[ShortcutSpec, ...] = WINDOW_SHORTCUTS + CONTEXT_SHORTCUTS
GROUP_ORDER: tuple[str, ...] = (
    "文件",
    "編輯與搜尋",
    "分頁",
    "檢視與工具",
    "快速開啟與連結建議",
    "預覽內編輯與標註",
    "PDF",
    "Mermaid 視覺編輯",
    "其他情境",
)


def shortcut_by_id(command_id: str) -> ShortcutSpec:
    for spec in ALL_SHORTCUTS:
        if spec.command_id == command_id:
            return spec
    raise KeyError(command_id)


def grouped_shortcuts() -> tuple[tuple[str, tuple[ShortcutSpec, ...]], ...]:
    return tuple(
        (group, tuple(spec for spec in ALL_SHORTCUTS if spec.group == group))
        for group in GROUP_ORDER
    )
