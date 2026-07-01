"""Tests for the structured Mermaid visual editor widget."""

from app.structured_mermaid import parse_structured_mermaid, render_structured_mermaid
from app.structured_mermaid_editor import StructuredMermaidEditor


def test_structured_editor_updates_sequence_message(qapp):
    editor = StructuredMermaidEditor()
    diagram = parse_structured_mermaid(
        """sequenceDiagram
    participant User
    participant App
    User->>App: Edit Mermaid source
"""
    ).require_diagram()
    editor.set_diagram(diagram)
    calls = []
    editor.diagram_changed.connect(lambda _diagram: calls.append(True))

    editor.set_cell(2, "text", "Open diagram")

    updated = editor.diagram()
    assert updated.rows[2].cells["text"] == "Open diagram"
    assert calls


def test_structured_editor_adds_er_field(qapp):
    editor = StructuredMermaidEditor()
    diagram = parse_structured_mermaid(
        """erDiagram
    DOCUMENT ||--o{ DIAGRAM : contains
    DOCUMENT {
        string path
    }
"""
    ).require_diagram()
    editor.set_diagram(diagram)
    editor.add_row("field")
    editor.set_cell(2, "entity", "DOCUMENT")
    editor.set_cell(2, "field_type", "string")
    editor.set_cell(2, "field_name", "title")

    updated = editor.diagram()
    assert updated.rows[2].role == "field"
    assert updated.rows[2].cells["field_name"] == "title"


def test_structured_editor_updates_class_relation(qapp):
    editor = StructuredMermaidEditor()
    diagram = parse_structured_mermaid(
        """classDiagram
    class Document {
        +path
    }
    class Diagram {
        +source
    }
    Document "1" --> "*" Diagram
"""
    ).require_diagram()
    editor.set_diagram(diagram)

    editor.set_cell(4, "arrow", "..>")
    editor.set_cell(4, "right", "many")

    text = render_structured_mermaid(editor.diagram())
    assert 'Document "1" ..> "many" Diagram' in text


def test_structured_editor_updates_state_transition(qapp):
    editor = StructuredMermaidEditor()
    diagram = parse_structured_mermaid(
        """stateDiagram-v2
    [*] --> Editing
    Editing --> Previewing: debounce
"""
    ).require_diagram()
    editor.set_diagram(diagram)

    editor.set_cell(1, "label", "render")

    text = render_structured_mermaid(editor.diagram())
    assert "Editing --> Previewing: render" in text
