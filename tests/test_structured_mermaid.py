"""Tests for structured Mermaid visual-editor diagram types."""

from app.mermaid_visual import visual_editor_kind
from app.structured_mermaid import (
    parse_structured_mermaid,
    render_structured_mermaid,
)


SEQUENCE = """sequenceDiagram
    participant User
    participant App
    User->>App: Edit Mermaid source
    App-->>User: Update preview
"""

CLASS = """classDiagram
    class MarkdownDocument {
        +path
        +save()
    }
    class MermaidBlock {
        +source
    }
    MarkdownDocument "1" --> "*" MermaidBlock
"""

STATE = """stateDiagram-v2
    [*] --> Editing
    Editing --> Previewing: debounce
    Previewing --> Valid: render ok
"""

ER = """erDiagram
    DOCUMENT ||--o{ DIAGRAM : contains
    DOCUMENT {
        string path
        string title
    }
    DIAGRAM {
        string type
    }
"""


def test_sequence_parse_and_render():
    result = parse_structured_mermaid(SEQUENCE)
    assert result.supported
    diagram = result.require_diagram()
    assert diagram.kind == "sequence"
    assert render_structured_mermaid(diagram) == SEQUENCE.rstrip()


def test_class_parse_and_render():
    result = parse_structured_mermaid(CLASS)
    assert result.supported
    diagram = result.require_diagram()
    assert diagram.kind == "class"
    assert render_structured_mermaid(diagram) == CLASS.rstrip()


def test_state_parse_and_render():
    result = parse_structured_mermaid(STATE)
    assert result.supported
    diagram = result.require_diagram()
    assert diagram.kind == "state"
    assert render_structured_mermaid(diagram) == STATE.rstrip()


def test_er_parse_and_render():
    result = parse_structured_mermaid(ER)
    assert result.supported
    diagram = result.require_diagram()
    assert diagram.kind == "er"
    assert render_structured_mermaid(diagram) == ER.rstrip()


def test_visual_routes_all_structured_template_types():
    assert visual_editor_kind(SEQUENCE) == "sequence"
    assert visual_editor_kind(CLASS) == "class"
    assert visual_editor_kind(STATE) == "state"
    assert visual_editor_kind(ER) == "er"
