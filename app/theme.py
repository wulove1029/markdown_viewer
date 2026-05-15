"""Theme tokens, SVG icons, and stylesheet helpers for the desktop UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
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
    if name == "dark":
        return DARK
    return LIGHT


ICONS: dict[str, str] = {
    "folder-open": (
        '<path d="M6 17h12.4a2 2 0 0 0 1.9-1.4l2.1-6.4A1.5 1.5 0 0 0 21 7H10l-2-2H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h1.2a2 2 0 0 0 .8-2Z"/>'
        '<path d="M2 9h20"/>'
    ),
    "search": '<path d="m21 21-4.3-4.3"/><circle cx="11" cy="11" r="8"/>',
    "sun": (
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2"/>'
        '<path d="M12 20v2"/>'
        '<path d="m4.9 4.9 1.4 1.4"/>'
        '<path d="m17.7 17.7 1.4 1.4"/>'
        '<path d="M2 12h2"/>'
        '<path d="M20 12h2"/>'
        '<path d="m6.3 17.7-1.4 1.4"/>'
        '<path d="m19.1 4.9-1.4 1.4"/>'
    ),
    "moon": '<path d="M12 3a6.6 6.6 0 0 0 8.8 8.8A9 9 0 1 1 12 3Z"/>',
    "refresh": (
        '<path d="M21 12a9 9 0 0 1-15.3 6.4L3 16"/>'
        '<path d="M3 16h5v5"/>'
        '<path d="M3 12A9 9 0 0 1 18.3 5.6L21 8"/>'
        '<path d="M21 8h-5V3"/>'
    ),
    "download": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<path d="M7 10l5 5 5-5"/>'
        '<path d="M12 15V3"/>'
    ),
    "panel-left": (
        '<rect x="3" y="4" width="18" height="16" rx="2"/>'
        '<path d="M9 4v16"/>'
    ),
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "file-text": (
        '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>'
        '<path d="M14 2v6h6"/>'
        '<path d="M16 13H8"/>'
        '<path d="M16 17H8"/>'
        '<path d="M10 9H8"/>'
    ),
    "history": (
        '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/>'
        '<path d="M3 3v5h5"/>'
        '<path d="M12 7v5l3 2"/>'
    ),
    "list-tree": (
        '<path d="M21 12h-8"/>'
        '<path d="M21 6H8"/>'
        '<path d="M21 18h-8"/>'
        '<path d="M3 6h1"/>'
        '<path d="M3 12h1"/>'
        '<path d="M3 18h1"/>'
        '<path d="M8 12h1"/>'
        '<path d="M8 18h1"/>'
    ),
    "alert": (
        '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/>'
        '<path d="M12 9v4"/>'
        '<path d="M12 17h.01"/>'
    ),
}


def svg_icon(name: str, color: str, size: int = 20) -> QIcon:
    path = ICONS.get(name)
    if path is None:
        raise KeyError(f"Unknown icon: {name}")

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{path}</svg>"""
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()

    return QIcon(pixmap)


def app_stylesheet(theme: Theme) -> str:
    return f"""
QMainWindow {{
    background: {theme.window};
    color: {theme.text};
    font-family: "Segoe UI", "Microsoft JhengHei UI", sans-serif;
    font-size: 13px;
}}
QWidget {{
    color: {theme.text};
    font-family: "Segoe UI", "Microsoft JhengHei UI", sans-serif;
    font-size: 13px;
}}
QWidget:disabled {{
    color: {theme.text_subtle};
}}
QSplitter::handle {{
    background: {theme.border};
}}
QSplitter::handle:hover {{
    background: {theme.surface_hover};
}}
QToolTip {{
    background: {theme.surface_alt};
    border: 1px solid {theme.border};
    color: {theme.text};
    padding: 6px 8px;
}}
QLineEdit, QTextEdit, QPlainTextEdit {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text};
    min-height: 32px;
    padding: 6px 10px;
    selection-background-color: {theme.accent_soft};
    selection-color: {theme.text};
}}
QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover {{
    border-color: {theme.accent};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {theme.accent};
    background: {theme.surface};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    background: {theme.surface_alt};
    border-color: {theme.border};
    color: {theme.text_subtle};
}}
QPushButton {{
    background: {theme.surface};
    border: 1px solid {theme.border};
    border-radius: 6px;
    color: {theme.text};
    min-height: {HIT_TARGET}px;
    min-width: {HIT_TARGET}px;
    padding: 0 12px;
}}
QPushButton:hover {{
    background: {theme.surface_hover};
    border-color: {theme.accent};
    color: {theme.text};
}}
QPushButton:focus {{
    border: 1px solid {theme.accent};
}}
QPushButton:pressed, QPushButton:checked {{
    background: {theme.surface_active};
    border-color: {theme.accent};
}}
QPushButton:disabled {{
    background: {theme.surface_alt};
    border-color: {theme.border};
    color: {theme.text_subtle};
}}
QScrollBar:vertical {{
    background: {theme.surface_alt};
    width: 12px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {theme.border};
    border-radius: 6px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{
    background: {theme.text_subtle};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: {theme.surface_alt};
    height: 12px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {theme.border};
    border-radius: 6px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {theme.text_subtle};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
"""


def toolbar_stylesheet(theme: Theme) -> str:
    return f"""
QWidget#toolbar, QFrame#toolbar, QWidget#topToolbar, QFrame#topToolbar {{
    background: {theme.surface};
    border-bottom: 1px solid {theme.border};
    min-height: {TOOLBAR_HEIGHT}px;
    max-height: {TOOLBAR_HEIGHT}px;
}}
QWidget#topToolbar QWidget, QFrame#topToolbar QWidget {{
    background: transparent;
}}
QToolButton,
QWidget#toolbar QPushButton, QFrame#toolbar QPushButton,
QWidget#topToolbar QPushButton, QFrame#topToolbar QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {theme.text_muted};
    min-height: {HIT_TARGET}px;
    min-width: {HIT_TARGET}px;
    padding: 0;
}}
QToolButton:hover,
QWidget#toolbar QPushButton:hover, QFrame#toolbar QPushButton:hover,
QWidget#topToolbar QPushButton:hover, QFrame#topToolbar QPushButton:hover {{
    background: {theme.surface_hover};
    border-color: {theme.surface_hover};
    color: {theme.text};
}}
QToolButton:focus,
QWidget#toolbar QPushButton:focus, QFrame#toolbar QPushButton:focus,
QWidget#topToolbar QPushButton:focus, QFrame#topToolbar QPushButton:focus {{
    border-color: {theme.accent};
}}
QToolButton:pressed, QToolButton:checked,
QWidget#toolbar QPushButton:pressed, QWidget#toolbar QPushButton:checked,
QFrame#toolbar QPushButton:pressed, QFrame#toolbar QPushButton:checked,
QWidget#topToolbar QPushButton:pressed, QWidget#topToolbar QPushButton:checked,
QFrame#topToolbar QPushButton:pressed, QFrame#topToolbar QPushButton:checked {{
    background: {theme.surface_active};
    border-color: {theme.accent};
    color: {theme.text};
}}
QToolButton:disabled,
QWidget#toolbar QPushButton:disabled, QFrame#toolbar QPushButton:disabled,
QWidget#topToolbar QPushButton:disabled, QFrame#topToolbar QPushButton:disabled {{
    background: transparent;
    border-color: transparent;
    color: {theme.text_subtle};
}}
"""


def panel_stylesheet(theme: Theme) -> str:
    return f"""
QWidget#panel, QFrame#panel, QWidget#leftPanel, QFrame#leftPanel {{
    background: {theme.surface};
    border-right: 1px solid {theme.border};
    color: {theme.text};
    min-width: {PANEL_WIDTH}px;
}}
QWidget#panelHeader, QFrame#panelHeader {{
    background: {theme.surface};
    border-bottom: 1px solid {theme.border};
    min-height: {TOOLBAR_HEIGHT}px;
}}
QWidget#panelHeader QWidget, QFrame#panelHeader QWidget {{
    background: transparent;
}}
QLabel {{
    background: transparent;
    color: {theme.text};
}}
QLabel[muted="true"] {{
    color: {theme.text_muted};
}}
QLabel#panelTitle {{
    color: {theme.text};
    font-size: 13px;
    font-weight: 600;
}}
QPushButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {theme.text_muted};
    min-height: {HIT_TARGET}px;
    min-width: {HIT_TARGET}px;
    padding: 0 8px;
}}
QPushButton:hover {{
    background: {theme.surface_hover};
    border-color: {theme.surface_hover};
    color: {theme.text};
}}
QPushButton:focus {{
    border-color: {theme.accent};
}}
QPushButton:pressed, QPushButton:checked {{
    background: {theme.surface_active};
    border-color: {theme.accent};
    color: {theme.text};
}}
QPushButton:disabled {{
    background: transparent;
    border-color: transparent;
    color: {theme.text_subtle};
}}
QTabWidget {{
    background: {theme.surface};
    border: none;
}}
QTabWidget::pane {{
    border: none;
    background: {theme.surface};
}}
QTabBar {{
    background: {theme.surface};
    border: none;
}}
QTabBar::tab {{
    background: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    color: {theme.text_muted};
    min-height: 36px;
    padding: 0 12px;
}}
QTabBar::tab:hover {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
QTabBar::tab:selected {{
    background: {theme.accent_soft};
    border-bottom: 2px solid {theme.accent};
    color: {theme.text};
}}
QTabBar::tab:!selected {{
    background: transparent;
}}
QTabBar::tab:disabled {{
    background: transparent;
    border-bottom-color: transparent;
    color: {theme.text_subtle};
}}
"""


def collection_stylesheet(theme: Theme, widget_name: str) -> str:
    return f"""
{widget_name} {{
    background: {theme.surface};
    border: none;
    color: {theme.text};
    outline: 0;
    show-decoration-selected: 1;
}}
{widget_name}:focus {{
    border: 1px solid {theme.accent};
}}
{widget_name}:disabled {{
    background: {theme.surface_alt};
    color: {theme.text_subtle};
}}
{widget_name}::item {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    color: {theme.text};
    min-height: 32px;
    padding: 6px 8px;
}}
{widget_name}::item:hover {{
    background: {theme.surface_hover};
    color: {theme.text};
}}
{widget_name}::item:selected {{
    background: {theme.surface_active};
    border-color: {theme.accent_soft};
    color: {theme.text};
}}
{widget_name}::item:selected:active {{
    background: {theme.surface_active};
    border-color: {theme.accent};
    color: {theme.text};
}}
{widget_name}::item:disabled {{
    background: transparent;
    color: {theme.text_subtle};
}}
{widget_name}::branch {{
    background: transparent;
}}
{widget_name}::branch:hover {{
    background: {theme.surface_hover};
}}
"""
