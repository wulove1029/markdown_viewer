"""Built-in Mermaid templates and snippets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MermaidTemplate:
    id: str
    group: str
    name: str
    description: str
    source: str


@dataclass(frozen=True)
class MermaidSnippet:
    id: str
    name: str
    source: str


TEMPLATES: tuple[MermaidTemplate, ...] = (
    MermaidTemplate(
        id="flowchart-basic",
        group="Flowchart",
        name="Basic Flowchart",
        description="A simple left-to-right process flow.",
        source="""flowchart LR
    Start([Start]) --> Input[Collect input]
    Input --> Decision{Valid?}
    Decision -- Yes --> Process[Process request]
    Decision -- No --> Fix[Fix input]
    Fix --> Input
    Process --> Done([Done])
""",
    ),
    MermaidTemplate(
        id="flowchart-system",
        group="Flowchart",
        name="System Flow",
        description="A practical software system flow.",
        source="""flowchart TD
    User[User] --> UI[Desktop app]
    UI --> Parser[Markdown parser]
    Parser --> Preview[Live preview]
    Preview --> Export{Export?}
    Export -- PDF --> Pdf[PDF output]
    Export -- PPTX --> Deck[PowerPoint deck]
""",
    ),
    MermaidTemplate(
        id="sequence-basic",
        group="Sequence",
        name="Sequence Diagram",
        description="Request and response between actors.",
        source="""sequenceDiagram
    participant User
    participant App
    participant Renderer

    User->>App: Edit Mermaid source
    App->>Renderer: Render diagram
    Renderer-->>App: SVG or error
    App-->>User: Update preview
""",
    ),
    MermaidTemplate(
        id="class-basic",
        group="Class",
        name="Class Diagram",
        description="Classes and relationships.",
        source="""classDiagram
    class MarkdownDocument {
        +path
        +text
        +save()
    }
    class MermaidBlock {
        +start_line
        +end_line
        +source
    }
    MarkdownDocument "1" --> "*" MermaidBlock
""",
    ),
    MermaidTemplate(
        id="state-basic",
        group="State",
        name="State Diagram",
        description="State transitions for a workflow.",
        source="""stateDiagram-v2
    [*] --> Editing
    Editing --> Previewing: debounce
    Previewing --> Valid: render ok
    Previewing --> Error: render failed
    Valid --> Editing: source changed
    Error --> Editing: fix source
""",
    ),
    MermaidTemplate(
        id="er-basic",
        group="ER",
        name="ER Diagram",
        description="Entities and relationships.",
        source="""erDiagram
    DOCUMENT ||--o{ DIAGRAM : contains
    DIAGRAM ||--o{ EXPORT : produces
    DOCUMENT {
        string path
        string title
    }
    DIAGRAM {
        string type
        string source
    }
""",
    ),
    MermaidTemplate(
        id="gantt-basic",
        group="Gantt",
        name="Gantt Plan",
        description="A small project timeline.",
        source="""gantt
    title Mermaid Workspace Rollout
    dateFormat  YYYY-MM-DD
    section Build
    Workspace      :active, ws, 2026-07-01, 4d
    Markdown link  :md, after ws, 3d
    Polish         :polish, after md, 2d
""",
    ),
)


SNIPPETS: tuple[MermaidSnippet, ...] = (
    MermaidSnippet(
        id="flowchart-decision",
        name="Flowchart decision",
        source="Decision{Decision?}\nDecision -- Yes --> Next[Next step]\nDecision -- No --> Back[Try again]",
    ),
    MermaidSnippet(
        id="flowchart-subgraph",
        name="Flowchart subgraph",
        source="subgraph GroupName[Group name]\n    A[Step A] --> B[Step B]\nend",
    ),
    MermaidSnippet(
        id="sequence-note",
        name="Sequence note",
        source="Note over User,App: Important context",
    ),
    MermaidSnippet(
        id="sequence-alt",
        name="Sequence alt",
        source="alt Success\n    App-->>User: Done\nelse Failure\n    App-->>User: Error\nend",
    ),
    MermaidSnippet(
        id="state-transition",
        name="State transition",
        source="StateA --> StateB: event",
    ),
)


def default_template() -> MermaidTemplate:
    return TEMPLATES[0]


def template_by_id(template_id: str) -> MermaidTemplate | None:
    for template in TEMPLATES:
        if template.id == template_id:
            return template
    return None


def snippet_by_id(snippet_id: str) -> MermaidSnippet | None:
    for snippet in SNIPPETS:
        if snippet.id == snippet_id:
            return snippet
    return None
