# Markdown Viewer UI/UX Improvement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign Markdown Viewer into a quiet Windows desktop Markdown reading workspace with a top toolbar, tabbed side panel, improved reading states, SVG icons, and first-class light/dark themes.

**Architecture:** Add a small theme layer that owns semantic tokens, SVG icon generation, and shared PyQt styles. Rework the main window into toolbar + side panel + renderer while preserving existing file browser, recent files, outline, conversion, drag/drop, keyboard shortcuts, and updater behavior.

**Tech Stack:** Python 3, PyQt6, PyQt6-WebEngine, markdown-it-py, mdit_py_plugins, Pygments, Qt stylesheets, inline SVG icons via `QIcon`.

---

## File Structure

- Create `app/theme.py`
  - Owns light/dark design tokens, Qt stylesheet builders, SVG icon helper, and reusable UI constants.
- Modify `app/window.py`
  - Replaces ribbon-first layout with top toolbar + collapsible side panel + reading area.
  - Owns current file path, current theme, search state, reload action, and theme switching.
- Modify `app/left_panel.py`
  - Converts the panel into direct tabs: 檔案, 最近, 目錄.
  - Applies shared list/tree styling and 44px header controls.
- Modify `app/file_browser.py`
  - Accepts a theme object or stylesheet string so file tree states match the new design.
- Modify `app/recent_files.py`
  - Adds empty-state behavior and shared styling.
- Modify `app/toc.py`
  - Adds empty-state behavior and shared styling.
- Modify `app/renderer.py`
  - Adds empty/loading/error state rendering, theme switching, current-file reload support, and reduced-motion-aware scroll.
- Modify `app/md_converter.py`
  - Accepts a theme name, injects theme-specific Markdown CSS, removes emoji from error rendering, and returns structured error HTML consistently.
- Modify `assets/obsidian-light.css`
  - Replaces the old single-theme CSS with token-driven CSS for light and dark modes.
- Optional modify `README.md`
  - Only if verification reveals new run/build instructions are needed.

## Task 1: Theme Tokens And SVG Icon Infrastructure

**Files:**

- Create: `app/theme.py`

- [ ] **Step 1: Create the theme module**

Create `app/theme.py` with this content:

```python
"""Shared UI theme tokens, styles, and SVG icons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PyQt6.QtCore import QByteArray, QSize
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtSvg import QSvgRenderer

ThemeName = Literal["light", "dark"]

TOOLBAR_HEIGHT = 48
HIT_TARGET = 44
PANEL_WIDTH = 280


@dataclass(frozen=True)
class Theme:
    name: ThemeName
    window: str
    surface: str
    surface_alt: str
    surface_hover: str
    surface_active: str
    border: str
    text: str
    text_muted: str
    text_subtle: str
    accent: str
    accent_hover: str
    accent_soft: str
    accent_text: str
    danger: str
    warning: str
    success: str
    code_bg: str
    code_text: str
    shadow: str


LIGHT = Theme(
    name="light",
    window="#f7f7f4",
    surface="#ffffff",
    surface_alt="#efefeb",
    surface_hover="#e9eefc",
    surface_active="#dce6ff",
    border="#d7d8d2",
    text="#1d1f23",
    text_muted="#515760",
    text_subtle="#707780",
    accent="#315fbd",
    accent_hover="#244f9f",
    accent_soft="#dce6ff",
    accent_text="#ffffff",
    danger="#b42318",
    warning="#9a6700",
    success="#0f766e",
    code_bg="#1f2430",
    code_text="#d7dae0",
    shadow="rgba(20, 24, 31, 0.10)",
)

DARK = Theme(
    name="dark",
    window="#171b22",
    surface="#20252d",
    surface_alt="#252b34",
    surface_hover="#2e3a4c",
    surface_active="#34486a",
    border="#3a414c",
    text="#f2f5f8",
    text_muted="#c1c8d2",
    text_subtle="#8f98a6",
    accent="#8fb4ff",
    accent_hover="#adc8ff",
    accent_soft="#26395d",
    accent_text="#101620",
    danger="#ff9b93",
    warning="#f4c75f",
    success="#76d7c4",
    code_bg="#11161d",
    code_text="#d9dee7",
    shadow="rgba(0, 0, 0, 0.35)",
)


def get_theme(name: ThemeName) -> Theme:
    return DARK if name == "dark" else LIGHT


ICONS: dict[str, str] = {
    "folder-open": '<path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H9l2 2h7.5A2.5 2.5 0 0 1 21 9.5v.5H7.2a2 2 0 0 0-1.9 1.37L3 18V7.5Z"/><path d="M5.3 11.37A2 2 0 0 1 7.2 10H21l-2.15 7.18A2.5 2.5 0 0 1 16.45 19H4.5a1.5 1.5 0 0 1-1.42-1.97l2.22-5.66Z"/>',
    "search": '<path d="m21 21-4.35-4.35"/><circle cx="11" cy="11" r="7"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    "moon": '<path d="M21 14.5A8.5 8.5 0 0 1 9.5 3a7 7 0 1 0 11.5 11.5Z"/>',
    "refresh": '<path d="M21 12a9 9 0 0 1-15.3 6.36"/><path d="M3 12A9 9 0 0 1 18.3 5.64"/><path d="M18 2v4h-4"/><path d="M6 22v-4h4"/>',
    "download": '<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
    "panel-left": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "file-text": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h6"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/><path d="M12 7v5l3 2"/>',
    "list-tree": '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/>',
    "alert": '<path d="M12 9v4"/><path d="M12 17h.01"/><path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/>',
}


def svg_icon(name: str, color: str, size: int = 20) -> QIcon:
    paths = ICONS[name]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{paths}</svg>"""
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill()
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)
```

After adding this file, fix the missing imports by adding `QPainter` and `Qt` to the import section:

```python
from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
```

- [ ] **Step 2: Add shared stylesheet builders**

Append these functions to `app/theme.py`:

```python
def app_stylesheet(theme: Theme) -> str:
    return f"""
    QMainWindow {{
        background: {theme.window};
        color: {theme.text};
        font-family: "Segoe UI", sans-serif;
    }}
    QMenuBar {{
        background: {theme.surface};
        color: {theme.text};
        border-bottom: 1px solid {theme.border};
    }}
    QMenuBar::item:selected {{
        background: {theme.surface_hover};
    }}
    QMenu {{
        background: {theme.surface};
        border: 1px solid {theme.border};
        color: {theme.text};
    }}
    QMenu::item {{
        min-height: 28px;
        padding: 6px 24px;
    }}
    QMenu::item:selected {{
        background: {theme.surface_hover};
        color: {theme.accent};
    }}
    QStatusBar {{
        background: {theme.surface};
        color: {theme.text_muted};
        border-top: 1px solid {theme.border};
    }}
    """


def toolbar_stylesheet(theme: Theme) -> str:
    return f"""
    QWidget#topToolbar {{
        background: {theme.surface};
        border-bottom: 1px solid {theme.border};
    }}
    QLabel#toolbarTitle {{
        color: {theme.text};
        font-size: 14px;
        font-weight: 600;
    }}
    QLabel#toolbarSubtitle {{
        color: {theme.text_subtle};
        font-size: 12px;
    }}
    QPushButton {{
        min-width: {HIT_TARGET}px;
        max-width: {HIT_TARGET}px;
        min-height: {HIT_TARGET}px;
        max-height: {HIT_TARGET}px;
        border: 1px solid transparent;
        border-radius: 6px;
        background: transparent;
    }}
    QPushButton:hover {{
        background: {theme.surface_hover};
        border-color: {theme.border};
    }}
    QPushButton:pressed {{
        background: {theme.surface_active};
    }}
    QPushButton:focus {{
        border: 2px solid {theme.accent};
    }}
    QPushButton:disabled {{
        opacity: 0.45;
        background: transparent;
    }}
    """


def panel_stylesheet(theme: Theme) -> str:
    return f"""
    QWidget#leftPanel {{
        background: {theme.surface_alt};
        border-right: 1px solid {theme.border};
    }}
    QWidget#panelHeader {{
        background: {theme.surface_alt};
        border-bottom: 1px solid {theme.border};
    }}
    QLabel#panelTitle {{
        color: {theme.text};
        font-size: 13px;
        font-weight: 700;
    }}
    QTabBar::tab {{
        min-height: 36px;
        padding: 0 12px;
        color: {theme.text_muted};
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{
        color: {theme.accent};
        border-bottom-color: {theme.accent};
        background: {theme.surface};
    }}
    QTabBar::tab:hover {{
        color: {theme.accent};
        background: {theme.surface_hover};
    }}
    QTabWidget::pane {{
        border: none;
    }}
    QPushButton {{
        min-width: {HIT_TARGET}px;
        min-height: {HIT_TARGET}px;
        border: 1px solid transparent;
        border-radius: 6px;
        background: transparent;
        color: {theme.text_muted};
    }}
    QPushButton:hover {{
        background: {theme.surface_hover};
        color: {theme.accent};
    }}
    QPushButton:focus {{
        border: 2px solid {theme.accent};
    }}
    """


def collection_stylesheet(theme: Theme, widget_name: str) -> str:
    return f"""
    {widget_name} {{
        background: {theme.surface_alt};
        border: none;
        color: {theme.text};
        font-size: 13px;
        outline: 0;
    }}
    {widget_name}::item {{
        min-height: 32px;
        padding: 6px 10px;
        color: {theme.text};
        border-bottom: 1px solid {theme.border};
    }}
    {widget_name}::item:hover {{
        background: {theme.surface_hover};
        color: {theme.accent};
    }}
    {widget_name}::item:selected {{
        background: {theme.surface_active};
        color: {theme.accent};
    }}
    {widget_name}::item:focus {{
        border: 1px solid {theme.accent};
    }}
    """
```

- [ ] **Step 3: Run syntax check**

Run:

```powershell
py -3 -m py_compile app/theme.py
```

Expected: no output and exit code 0.

- [ ] **Step 4: Commit**

Run:

```powershell
git add app/theme.py
git commit -m "Add UI theme tokens and icons"
```

Expected: commit succeeds.

## Task 2: Markdown CSS And Renderer State Pages

**Files:**

- Modify: `assets/obsidian-light.css`
- Modify: `app/md_converter.py`
- Modify: `app/renderer.py`

- [ ] **Step 1: Replace Markdown CSS with token-aware CSS**

Replace `assets/obsidian-light.css` with CSS that uses light defaults and a
`body.theme-dark` override:

```css
/* Markdown document theme for Markdown Viewer */
:root {
  --bg: #fbfbf8;
  --surface: #ffffff;
  --surface-alt: #f0f1ed;
  --border: #d7d8d2;
  --text: #1d1f23;
  --text-muted: #515760;
  --text-subtle: #707780;
  --accent: #315fbd;
  --accent-soft: #dce6ff;
  --danger: #b42318;
  --code-bg: #1f2430;
  --code-text: #d7dae0;
}

body.theme-dark {
  --bg: #171b22;
  --surface: #20252d;
  --surface-alt: #252b34;
  --border: #3a414c;
  --text: #f2f5f8;
  --text-muted: #c1c8d2;
  --text-subtle: #8f98a6;
  --accent: #8fb4ff;
  --accent-soft: #26395d;
  --danger: #ff9b93;
  --code-bg: #11161d;
  --code-text: #d9dee7;
}

* { box-sizing: border-box; }

html { font-size: 16px; }

body {
  font-family: "Segoe UI", "Inter", -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.72;
  padding: 48px 56px;
  max-width: 900px;
  margin: 0 auto;
}

h1, h2, h3, h4, h5, h6 {
  color: var(--text);
  font-weight: 600;
  line-height: 1.3;
  margin-top: 1.9em;
  margin-bottom: 0.55em;
}

h1 { font-size: 2rem; border-bottom: 2px solid var(--border); padding-bottom: 0.35em; }
h2 { font-size: 1.5rem; border-bottom: 1px solid var(--border); padding-bottom: 0.25em; }
h3 { font-size: 1.2rem; }
h4 { font-size: 1rem; }
h5, h6 { font-size: 0.95rem; color: var(--text-muted); }

p { margin: 0.8em 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

code {
  font-family: "Cascadia Code", "Fira Code", Consolas, monospace;
  font-size: 0.88em;
  background: var(--surface-alt);
  color: var(--danger);
  padding: 0.15em 0.4em;
  border-radius: 4px;
}

.highlight {
  margin: 1.2em 0;
  border: 1px solid var(--border);
  border-radius: 8px;
  overflow: hidden;
}

.highlight pre {
  background: var(--code-bg) !important;
  color: var(--code-text);
  margin: 0;
  padding: 16px 20px;
  overflow-x: auto;
  font-family: "Cascadia Code", "Fira Code", Consolas, monospace;
  font-size: 0.875em;
  line-height: 1.6;
}

.highlight code { background: none; color: inherit; padding: 0; }

blockquote {
  margin: 1em 0;
  padding: 0.75em 1.2em;
  border-left: 4px solid var(--accent);
  background: var(--accent-soft);
  border-radius: 0 6px 6px 0;
  color: var(--text);
}

table {
  border-collapse: collapse;
  width: 100%;
  margin: 1.2em 0;
  font-size: 0.95em;
}

thead tr { background: var(--surface-alt); }
th, td {
  padding: 9px 14px;
  border: 1px solid var(--border);
  text-align: left;
}
th { font-weight: 600; color: var(--text); }
tbody tr:nth-child(even) { background: var(--surface); }
tbody tr:hover { background: var(--accent-soft); }

ul, ol { padding-left: 1.6em; margin: 0.6em 0; }
li { margin: 0.25em 0; }
.task-list-item { list-style: none; margin-left: -1.2em; }
.task-list-item input[type="checkbox"] { margin-right: 0.5em; accent-color: var(--accent); }

hr {
  border: none;
  border-top: 1px solid var(--border);
  margin: 2em 0;
}

img {
  max-width: 100%;
  border-radius: 6px;
  border: 1px solid var(--border);
}

strong { font-weight: 700; }
em { color: var(--text-muted); }

.state-page {
  min-height: calc(100vh - 96px);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  text-align: center;
  color: var(--text-muted);
}

.state-page h1 {
  border: none;
  margin: 0;
  padding: 0;
  font-size: 1.35rem;
  color: var(--text);
}

.state-page p {
  max-width: 520px;
  margin: 0;
}

.state-page .status-label {
  color: var(--accent);
  font-weight: 600;
}

::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 6px; }

@media (max-width: 720px) {
  body { padding: 28px 24px; }
}
```

- [ ] **Step 2: Add theme argument to converter**

Modify `app/md_converter.py`:

```python
def convert(filepath: str | Path, theme: str = "light") -> tuple[str, list[tuple[int, str, str]]]:
    """Return (html, headings). headings = list of (level, text, anchor_id)."""
    path = Path(filepath)

    if not path.exists():
        return _error_page(f"找不到檔案：{path}", theme), []

    if path.stat().st_size > 10 * 1024 * 1024:
        return _error_page(f"檔案過大（>10MB），無法預覽：{path.name}", theme), []

    text = _read_text(path)
    if text is None:
        return _error_page(f"無法讀取檔案：編碼不支援（{path.name}）", theme), []

    body = _PARSER.render(text)
    body_with_anchors, headings = _inject_anchors(body)
    return _wrap(body_with_anchors, path.stem, theme), headings
```

Then update `_wrap` and `_error_page`:

```python
def _wrap(body: str, title: str, theme: str = "light") -> str:
    theme_class = "theme-dark" if theme == "dark" else "theme-light"
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{_FULL_CSS}</style>
</head>
<body class="{theme_class}">
{body}
</body>
</html>"""


def _state_page(title: str, message: str, theme: str = "light", label: str = "") -> str:
    label_html = f'<div class="status-label">{label}</div>' if label else ""
    return _wrap(
        f'<main class="state-page">{label_html}<h1>{title}</h1><p>{message}</p></main>',
        title,
        theme,
    )


def _error_page(message: str, theme: str = "light") -> str:
    return _state_page("無法預覽 Markdown", message, theme, "錯誤")
```

- [ ] **Step 3: Update renderer state handling**

Modify `app/renderer.py` to store theme and current file:

```python
class RendererView(QWebEngineView):
    active_anchor_changed = pyqtSignal(str)

    def __init__(self, on_headings_ready=None, parent=None):
        super().__init__(parent)
        self._on_headings_ready = on_headings_ready
        self._current_anchor = ""
        self._current_path: Path | None = None
        self._theme = "light"
        self.setAcceptDrops(True)
```

Replace `_PLACEHOLDER` usage with state page helpers:

```python
def _state_html(self, title: str, message: str, label: str = "") -> str:
    from .md_converter import _state_page
    return _state_page(title, message, self._theme, label)


def show_empty(self):
    self._current_path = None
    self._spy_timer.stop()
    self.setHtml(self._state_html(
        "開啟 Markdown 檔案",
        "從左側檔案清單選擇檔案，或使用上方開啟按鈕與拖放操作開始預覽。",
        "尚未載入文件",
    ))
    if self._on_headings_ready:
        self._on_headings_ready([])


def show_loading(self, path: Path):
    self.setHtml(self._state_html(
        "正在載入文件",
        f"正在準備預覽：{path.name}",
        "載入中",
    ))
```

Update load/reload/theme methods:

```python
def load_file(self, filepath: str | Path):
    path = Path(filepath)
    self._current_path = path
    self.show_loading(path)
    html, headings = convert(path, self._theme)
    base_url = QUrl.fromLocalFile(str(path.parent) + "/")
    self.page().setHtml(html, base_url)
    if self._on_headings_ready:
        self._on_headings_ready(headings)


def reload_current(self):
    if self._current_path:
        self.load_file(self._current_path)


def set_theme(self, theme: str):
    self._theme = theme
    if self._current_path:
        self.reload_current()
    else:
        self.show_empty()
```

In `__init__`, call `self.show_empty()` instead of `_show_placeholder()`.

- [ ] **Step 4: Run syntax check**

Run:

```powershell
py -3 -m py_compile app/md_converter.py app/renderer.py
```

Expected: no output and exit code 0.

- [ ] **Step 5: Commit**

Run:

```powershell
git add assets/obsidian-light.css app/md_converter.py app/renderer.py
git commit -m "Add themed markdown rendering states"
```

Expected: commit succeeds.

## Task 3: Tabbed Side Panel

**Files:**

- Modify: `app/left_panel.py`
- Modify: `app/file_browser.py`
- Modify: `app/recent_files.py`
- Modify: `app/toc.py`

- [ ] **Step 1: Convert left panel to tabs**

Update `LeftPanel` to accept a theme and use `QTabWidget`:

```python
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QTabWidget, QPushButton, QHBoxLayout,
    QFileDialog,
)

from .theme import Theme, panel_stylesheet, svg_icon
```

Implement:

```python
class LeftPanel(QWidget):
    TITLES = ["檔案", "最近", "目錄"]

    def __init__(self, on_file_selected, on_anchor_clicked, theme: Theme, parent=None):
        super().__init__(parent)
        self.setObjectName("leftPanel")
        self.setMinimumWidth(180)
        self.setMaximumWidth(420)
        self._theme = theme
        self._on_file_selected = on_file_selected

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._header = QWidget()
        self._header.setObjectName("panelHeader")
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(12, 4, 8, 4)
        hl.setSpacing(4)

        self._title_label = QLabel("工作面板")
        self._title_label.setObjectName("panelTitle")

        self._open_btn = QPushButton()
        self._open_btn.setToolTip("開啟 Markdown 檔案 (Ctrl+O)")
        self._open_btn.setAccessibleName("開啟 Markdown 檔案")
        self._open_btn.clicked.connect(self.open_file_dialog)

        self._close_btn = QPushButton()
        self._close_btn.setToolTip("收合側邊欄")
        self._close_btn.setAccessibleName("收合側邊欄")

        hl.addWidget(self._title_label)
        hl.addStretch()
        hl.addWidget(self._open_btn)
        hl.addWidget(self._close_btn)
        layout.addWidget(self._header)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)
        self._tabs.setTabPosition(QTabWidget.TabPosition.North)

        self._file_browser = FileBrowserView(on_file_selected=on_file_selected)
        self._recent = RecentFilesView(on_file_selected=on_file_selected)
        self._toc = TocView(on_anchor_clicked=on_anchor_clicked)

        self._tabs.addTab(self._file_browser, "檔案")
        self._tabs.addTab(self._recent, "最近")
        self._tabs.addTab(self._toc, "目錄")
        layout.addWidget(self._tabs)

        self.apply_theme(theme)
```

Add:

```python
def apply_theme(self, theme: Theme):
    self._theme = theme
    self.setStyleSheet(panel_stylesheet(theme))
    self._open_btn.setIcon(svg_icon("folder-open", theme.text_muted))
    self._close_btn.setIcon(svg_icon("chevron-left", theme.text_muted))
    self._file_browser.apply_theme(theme)
    self._recent.apply_theme(theme)
    self._toc.apply_theme(theme)
```

Keep the existing properties and `open_file_dialog`, but update dialog text:

```python
path, _ = QFileDialog.getOpenFileName(
    self,
    "開啟 Markdown 檔案",
    "",
    "Markdown 檔案 (*.md *.markdown);;所有檔案 (*)",
)
```

- [ ] **Step 2: Add apply_theme to file browser**

In `app/file_browser.py`, import:

```python
from .theme import Theme, collection_stylesheet
```

Add method:

```python
def apply_theme(self, theme: Theme):
    self.setStyleSheet(collection_stylesheet(theme, "QTreeView"))
```

Replace the old hardcoded `setStyleSheet(...)` call in `__init__` with:

```python
self.apply_theme(theme=__import__("app.theme", fromlist=["LIGHT"]).LIGHT)
```

When implementing, prefer a direct import of `LIGHT`:

```python
from .theme import LIGHT, Theme, collection_stylesheet
...
self.apply_theme(LIGHT)
```

- [ ] **Step 3: Add apply_theme and empty text to recent files**

In `app/recent_files.py`, import:

```python
from .theme import LIGHT, Theme, collection_stylesheet
```

Replace hardcoded stylesheet with `self.apply_theme(LIGHT)`.

Add:

```python
def apply_theme(self, theme: Theme):
    self.setStyleSheet(collection_stylesheet(theme, "QListWidget"))
```

In `_refresh`, when no existing recent file is added:

```python
added = 0
for p in self._load():
    path = Path(p)
    if not path.exists():
        continue
    item = QListWidgetItem(path.name)
    item.setToolTip(p)
    item.setData(Qt.ItemDataRole.UserRole, p)
    self.addItem(item)
    added += 1
if added == 0:
    item = QListWidgetItem("尚無最近開啟的檔案")
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    self.addItem(item)
```

- [ ] **Step 4: Add apply_theme and empty text to outline**

In `app/toc.py`, import:

```python
from .theme import LIGHT, Theme, collection_stylesheet
```

Add:

```python
def apply_theme(self, theme: Theme):
    self.setStyleSheet(f"background: {theme.surface_alt};")
    self._list.setStyleSheet(collection_stylesheet(theme, "QListWidget"))
```

Call `self.apply_theme(LIGHT)` in `__init__`.

In `update_headings`, after clearing, add empty state:

```python
if not headings:
    item = QListWidgetItem("目前文件沒有標題")
    item.setFlags(Qt.ItemFlag.NoItemFlags)
    self._list.addItem(item)
    return
```

- [ ] **Step 5: Run syntax check**

Run:

```powershell
py -3 -m py_compile app/left_panel.py app/file_browser.py app/recent_files.py app/toc.py
```

Expected: no output and exit code 0.

- [ ] **Step 6: Commit**

Run:

```powershell
git add app/left_panel.py app/file_browser.py app/recent_files.py app/toc.py
git commit -m "Redesign side panel navigation"
```

Expected: commit succeeds.

## Task 4: Main Window Toolbar, Theme Switching, And Workspace Layout

**Files:**

- Modify: `app/window.py`

- [ ] **Step 1: Replace imports and remove ribbon dependency**

Remove `Ribbon` import and add:

```python
from .theme import (
    HIT_TARGET,
    PANEL_WIDTH,
    TOOLBAR_HEIGHT,
    Theme,
    ThemeName,
    app_stylesheet,
    get_theme,
    svg_icon,
    toolbar_stylesheet,
)
```

Add instance fields in `__init__` before widgets:

```python
self._theme_name: ThemeName = QSettings(_ORG, _APP).value("theme", "light") or "light"
self._theme = get_theme(self._theme_name)
self._current_file: Path | None = None
self._sidebar_open = True
```

- [ ] **Step 2: Build top toolbar**

Add method:

```python
def _build_toolbar(self) -> QWidget:
    bar = QWidget()
    bar.setObjectName("topToolbar")
    bar.setFixedHeight(TOOLBAR_HEIGHT)
    bar.setStyleSheet(toolbar_stylesheet(self._theme))

    layout = QHBoxLayout(bar)
    layout.setContentsMargins(8, 2, 12, 2)
    layout.setSpacing(4)

    self._sidebar_btn = self._toolbar_button("panel-left", "顯示或收合側邊欄", self._toggle_sidebar)
    self._open_btn = self._toolbar_button("folder-open", "開啟 Markdown 檔案 (Ctrl+O)", self._panel_open_file)
    self._search_btn = self._toolbar_button("search", "搜尋文件 (Ctrl+F)", self._toggle_search)
    self._reload_btn = self._toolbar_button("refresh", "重新載入目前文件", self._reload_current)
    self._theme_btn = self._toolbar_button("moon", "切換深色模式", self._toggle_theme)
    self._update_btn = self._toolbar_button("download", "檢查更新", lambda: self._check_for_updates(manual=True))

    title_wrap = QWidget()
    title_layout = QVBoxLayout(title_wrap)
    title_layout.setContentsMargins(8, 0, 8, 0)
    title_layout.setSpacing(0)
    self._title_label = QLabel("Markdown Viewer")
    self._title_label.setObjectName("toolbarTitle")
    self._subtitle_label = QLabel("尚未載入文件")
    self._subtitle_label.setObjectName("toolbarSubtitle")
    title_layout.addWidget(self._title_label)
    title_layout.addWidget(self._subtitle_label)

    layout.addWidget(self._sidebar_btn)
    layout.addWidget(self._open_btn)
    layout.addWidget(self._search_btn)
    layout.addWidget(self._reload_btn)
    layout.addWidget(self._theme_btn)
    layout.addStretch()
    layout.addWidget(title_wrap)
    layout.addStretch()
    layout.addWidget(self._update_btn)
    return bar
```

Add helper:

```python
def _toolbar_button(self, icon_name: str, tooltip: str, callback) -> QPushButton:
    btn = QPushButton()
    btn.setFixedSize(HIT_TARGET, HIT_TARGET)
    btn.setToolTip(tooltip)
    btn.setAccessibleName(tooltip.split(" (")[0])
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setIcon(svg_icon(icon_name, self._theme.text_muted))
    btn.clicked.connect(callback)
    return btn
```

- [ ] **Step 3: Rebuild central layout**

Replace the old root layout with:

```python
self._panel = LeftPanel(
    on_file_selected=self._open_file,
    on_anchor_clicked=self._scroll_to_anchor,
    theme=self._theme,
)
self._renderer = RendererView(on_headings_ready=self._panel.toc.update_headings)
self._renderer.active_anchor_changed.connect(self._panel.toc.set_active_anchor)
self._panel.close_btn.clicked.connect(self._toggle_sidebar)

self._search_bar = self._build_search_bar()
self._search_bar.hide()

renderer_wrap = QWidget()
rv = QVBoxLayout(renderer_wrap)
rv.setContentsMargins(0, 0, 0, 0)
rv.setSpacing(0)
rv.addWidget(self._search_bar)
rv.addWidget(self._renderer)

self._splitter = QSplitter(Qt.Orientation.Horizontal)
self._splitter.addWidget(self._panel)
self._splitter.addWidget(renderer_wrap)
self._splitter.setStretchFactor(0, 0)
self._splitter.setStretchFactor(1, 1)
self._splitter.setSizes([PANEL_WIDTH, 960])
self._splitter.setHandleWidth(4)

self._restore_btn = QPushButton()
self._restore_btn.setFixedSize(HIT_TARGET, HIT_TARGET)
self._restore_btn.setToolTip("展開側邊欄")
self._restore_btn.setAccessibleName("展開側邊欄")
self._restore_btn.setCursor(Qt.CursorShape.PointingHandCursor)
self._restore_btn.clicked.connect(self._toggle_sidebar)
self._restore_btn.setParent(self._renderer)
self._restore_btn.move(12, 12)
self._restore_btn.hide()

self._toolbar = self._build_toolbar()

root = QWidget()
root_layout = QVBoxLayout(root)
root_layout.setContentsMargins(0, 0, 0, 0)
root_layout.setSpacing(0)
root_layout.addWidget(self._toolbar)
root_layout.addWidget(self._splitter, stretch=1)
self.setCentralWidget(root)
```

- [ ] **Step 4: Add theme application**

Add:

```python
def _apply_theme(self):
    self._theme = get_theme(self._theme_name)
    self.setStyleSheet(app_stylesheet(self._theme))
    self._toolbar.setStyleSheet(toolbar_stylesheet(self._theme))
    self._search_bar.setStyleSheet(self._search_style())
    self._panel.apply_theme(self._theme)
    self._renderer.set_theme(self._theme_name)
    self._splitter.setStyleSheet(f"QSplitter::handle {{ background: {self._theme.border}; }}")
    self._restore_btn.setStyleSheet(toolbar_stylesheet(self._theme))
    self._restore_btn.setIcon(svg_icon("chevron-right", self._theme.text_muted))
    self._theme_btn.setIcon(svg_icon("sun" if self._theme_name == "dark" else "moon", self._theme.text_muted))
    self._theme_btn.setToolTip("切換亮色模式" if self._theme_name == "dark" else "切換深色模式")
```

Add:

```python
def _toggle_theme(self):
    self._theme_name = "dark" if self._theme_name == "light" else "light"
    QSettings(_ORG, _APP).setValue("theme", self._theme_name)
    self._apply_theme()
```

- [ ] **Step 5: Update search styling**

Replace `_SEARCH_STYLE` with a method:

```python
def _search_style(self) -> str:
    t = self._theme
    return f"""
    QWidget#searchBar {{
        background: {t.surface};
        border-bottom: 1px solid {t.border};
    }}
    QLineEdit {{
        background: {t.window};
        border: 1px solid {t.border};
        border-radius: 6px;
        padding: 6px 10px;
        font-size: 13px;
        color: {t.text};
        min-height: 30px;
        min-width: 240px;
    }}
    QLineEdit:focus {{ border: 2px solid {t.accent}; }}
    QPushButton {{
        background: transparent;
        border: 1px solid transparent;
        color: {t.text_muted};
        min-width: 36px;
        min-height: 36px;
        border-radius: 6px;
    }}
    QPushButton:hover {{ background: {t.surface_hover}; color: {t.accent}; }}
    QPushButton:pressed {{ background: {t.surface_active}; }}
    QPushButton:focus {{ border: 2px solid {t.accent}; }}
    QLabel {{ font-size: 12px; color: {t.text_subtle}; padding: 0 4px; }}
    """
```

In `_build_search_bar`, set:

```python
bar.setStyleSheet(self._search_style())
self._search_input.setPlaceholderText("搜尋目前文件")
btn_prev.setIcon(svg_icon("chevron-left", self._theme.text_muted))
btn_next.setIcon(svg_icon("chevron-right", self._theme.text_muted))
btn_close.setIcon(svg_icon("x", self._theme.text_muted))
```

- [ ] **Step 6: Update file actions**

Add:

```python
def _panel_open_file(self):
    self._panel.open_file_dialog()


def _reload_current(self):
    if self._current_file:
        self._renderer.reload_current()
        self.statusBar().showMessage("已重新載入文件", 2500)
```

Update `_open_file`:

```python
def _open_file(self, filepath: str):
    path = Path(filepath)
    self._current_file = path
    self.setWindowTitle(f"{path.name} - Markdown Viewer")
    self._title_label.setText(path.name)
    self._subtitle_label.setText(str(path.parent))
    self._renderer.load_file(path)
    self._panel.file_browser.navigate_to(path.parent)
    self._panel.recent.add(filepath)
    self._reload_btn.setEnabled(True)
```

At startup, disable reload until a file is loaded:

```python
self._reload_btn.setEnabled(False)
```

- [ ] **Step 7: Run syntax check**

Run:

```powershell
py -3 -m py_compile app/window.py
```

Expected: no output and exit code 0.

- [ ] **Step 8: Commit**

Run:

```powershell
git add app/window.py
git commit -m "Redesign main reading workspace"
```

Expected: commit succeeds.

## Task 5: Interaction Polish, Error Messages, And Accessibility Pass

**Files:**

- Modify: `app/window.py`
- Modify: `app/renderer.py`
- Modify: `app/md_converter.py`
- Modify: `app/recent_files.py`

- [ ] **Step 1: Add no-results search feedback**

Update `RendererView.find_text` and `MainWindow._on_search_text_changed` so empty search clears text and non-empty search shows "正在搜尋..." immediately:

```python
def _on_search_text_changed(self, text: str):
    if not text:
        self._search_count.setText("")
        self._renderer.find_text("")
        return
    self._search_count.setText("正在搜尋...")
    self._renderer.find_text(text)
```

In this pass, keep WebEngine's built-in find behavior for matches; do not build a match counter unless it can be implemented safely with callbacks.

- [ ] **Step 2: Make update status messages Traditional Chinese**

In `app/window.py`, update manual update strings:

```python
self.statusBar().showMessage("正在檢查更新...")
QMessageBox.warning(self, "更新檢查失敗", str(error))
QMessageBox.information(
    self,
    "目前已是最新版本",
    f"Markdown Viewer 已是最新版本。\n目前版本：{VERSION}",
)
```

Update available dialog:

```python
answer = QMessageBox.question(
    self,
    "有可用更新",
    f"版本 {update.latest_version} 已可下載。\n\n是否要立即下載並安裝？",
)
```

Update download messages:

```python
self._update_progress = QProgressDialog("正在下載更新...", None, 0, 0, self)
self._update_progress.setWindowTitle("Markdown Viewer 更新")
QMessageBox.warning(self, "更新下載失敗", str(error))
QMessageBox.warning(self, "更新失敗", "無法啟動安裝程式。")
```

- [ ] **Step 3: Verify accessible names**

For each icon-only button in `app/window.py` and `app/left_panel.py`, confirm both methods are called:

```python
button.setToolTip("清楚的繁體中文提示")
button.setAccessibleName("清楚的繁體中文名稱")
```

The buttons to check:

- Sidebar toggle.
- Open file.
- Search.
- Reload.
- Theme toggle.
- Update check.
- Panel close.
- Panel restore.
- Search previous.
- Search next.
- Search close.

- [ ] **Step 4: Remove emoji from error pages**

Confirm `app/md_converter.py` no longer renders warning emoji. Error state should use text label "錯誤" and the message.

- [ ] **Step 5: Run syntax check**

Run:

```powershell
py -3 -m py_compile app/window.py app/renderer.py app/md_converter.py app/recent_files.py
```

Expected: no output and exit code 0.

- [ ] **Step 6: Commit**

Run:

```powershell
git add app/window.py app/renderer.py app/md_converter.py app/recent_files.py
git commit -m "Polish interaction states and messages"
```

Expected: commit succeeds.

## Task 6: Verification And Final Fixes

**Files:**

- Modify only files that fail verification.

- [ ] **Step 1: Run full syntax check**

Run:

```powershell
py -3 -m py_compile main.py app/*.py tools/*.py
```

Expected: no output and exit code 0.

- [ ] **Step 2: Create a local visual test Markdown file**

Create `build/ui_test.md` using PowerShell:

```powershell
@'
# 測試文件

這是一段用來檢查閱讀行距、連結與一般段落的內容。

## 程式碼

```python
def hello(name: str) -> str:
    return f"Hello, {name}"
```

## 表格

| 欄位 | 說明 |
| --- | --- |
| 狀態 | 正常 |
| 主題 | 亮色與深色 |

> 這是一段引用內容，用來檢查背景與邊框。

- [x] 已完成項目
- [ ] 未完成項目
'@ | Set-Content -Encoding utf8 build\ui_test.md
```

- [ ] **Step 3: Launch app with test file**

Run:

```powershell
py -3 main.py build\ui_test.md
```

Expected:

- App opens without terminal traceback.
- Top toolbar is visible.
- Left panel shows 檔案, 最近, 目錄 tabs.
- Reading area displays the test Markdown.
- Reload is enabled after file load.
- Search opens with `Ctrl+F`.
- Theme button switches both widgets and rendered Markdown.

- [ ] **Step 4: Manual UI checklist**

Verify:

- Toolbar icon buttons are at least 44x44px.
- Search previous/next/close are visible and focusable.
- Left panel collapse leaves a 44x44 restore button.
- Recent empty state appears when no recent files exist.
- Outline empty state appears for documents without headings.
- Main text contrast is readable in both themes.
- Disabled reload is visually distinct before a file is loaded.

- [ ] **Step 5: Fix any verification failures**

If a syntax, launch, or UI check fails, make the smallest scoped fix in the file that owns the behavior. After each fix, rerun the failing command or manual check.

- [ ] **Step 6: Commit verification fixes**

If Step 5 changed files, run:

```powershell
git add app assets build/ui_test.md
git commit -m "Fix UI verification issues"
```

If Step 5 changed no files, do not create an empty commit.

## Self-Review

Spec coverage:

- Top toolbar: Task 4.
- Tabbed left work panel: Task 3.
- Central reading area and Markdown styling: Task 2.
- Light/dark mode: Tasks 1, 2, and 4.
- SVG icons instead of emoji: Tasks 1, 4, and 5.
- 44x44px interactive targets: Tasks 1, 3, 4, and 6.
- Empty/loading/error states: Tasks 2, 5, and 6.
- Traditional Chinese UI text: Tasks 3, 4, and 5.
- Accessibility labels and focus states: Tasks 1, 4, 5, and 6.

No unresolved placeholders remain in this plan. The plan intentionally avoids a
full editor, multi-tab model, full-text index, and i18n because those are out of
scope for the approved design.
