"""Tests for Mermaid workspace theme selection behavior."""

from app.mermaid_workspace import MermaidWorkspaceDialog, _mermaid_workspace_stylesheet
from app.flowchart_canvas import FlowchartCanvas
from app.gantt_editor import GanttEditor
from app.structured_mermaid_editor import StructuredMermaidEditor
from app.theme import DARK


class _Combo:
    def __init__(self, value: str):
        self._value = value

    def currentData(self):
        return self._value


class _ThemeHarness:
    def __init__(self, selected: str, current: str = "light", base: str = "light"):
        self._theme_combo = _Combo(selected)
        self._theme_name = current
        self._base_theme_name = base
        self._preview_theme_mode = "auto"
        self.applied = []

    def apply_theme(self, theme):
        self._theme_name = theme.name
        self.applied.append(theme.name)

    def _render_preview(self):
        raise AssertionError("Theme selection should rely on apply_theme rendering.")


def test_mermaid_workspace_theme_combo_applies_dark_to_whole_dialog():
    harness = _ThemeHarness("dark")

    MermaidWorkspaceDialog._on_workspace_theme_changed(harness)

    assert harness.applied == ["dark"]
    assert harness._theme_name == "dark"
    assert harness._preview_theme_mode == "auto"


def test_mermaid_workspace_auto_theme_returns_to_base_theme():
    harness = _ThemeHarness("auto", current="dark", base="light")

    MermaidWorkspaceDialog._on_workspace_theme_changed(harness)

    assert harness.applied == ["light"]
    assert harness._theme_name == "light"
    assert harness._preview_theme_mode == "auto"


def test_mermaid_dark_stylesheet_covers_editor_tabs_and_visual_surfaces():
    stylesheet = _mermaid_workspace_stylesheet(DARK)

    assert "QTabWidget#mermaidEditorTabs::pane" in stylesheet
    assert "QTabWidget#mermaidEditorTabs QTabBar::tab:selected" in stylesheet
    assert "QStackedWidget#mermaidVisualStack" in stylesheet
    assert "QWidget#flowchartCanvas" in stylesheet
    assert "QWidget#flowchartToolbar" in stylesheet
    assert DARK.surface in stylesheet
    assert DARK.text in stylesheet


def test_mermaid_visual_editors_expose_themeable_object_names(qapp):
    flowchart = FlowchartCanvas()

    assert flowchart.objectName() == "flowchartCanvas"
    assert flowchart._toolbar_widget.objectName() == "flowchartToolbar"
    assert GanttEditor().objectName() == "ganttEditor"
    assert StructuredMermaidEditor().objectName() == "structuredMermaidEditor"
