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
        group="流程圖",
        name="基礎流程圖",
        description="簡單的左右向流程圖。",
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
        group="流程圖",
        name="系統流程圖",
        description="軟體系統運作流程圖。",
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
        group="序列圖",
        name="時序對話圖",
        description="角色/物件之間的請求與回應關係。",
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
        group="類別圖",
        name="物件類別圖",
        description="物件類別及其關聯關係。",
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
        group="狀態圖",
        name="狀態轉移圖",
        description="工作流的狀態轉移關係。",
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
        group="關係圖 (ER)",
        name="資料庫關係圖",
        description="資料庫實體及其關聯關係。",
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
        group="甘特圖",
        name="甘特時程表",
        description="專案進度時程表。",
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
        name="流程圖決策分支",
        source="Decision{Decision?}\nDecision -- Yes --> Next[Next step]\nDecision -- No --> Back[Try again]",
    ),
    MermaidSnippet(
        id="flowchart-subgraph",
        name="流程圖子群組",
        source="subgraph GroupName[Group name]\n    A[Step A] --> B[Step B]\nend",
    ),
    MermaidSnippet(
        id="sequence-note",
        name="序列圖備註",
        source="Note over User,App: Important context",
    ),
    MermaidSnippet(
        id="sequence-alt",
        name="序列圖條件分支 (alt)",
        source="alt Success\n    App-->>User: Done\nelse Failure\n    App-->>User: Error\nend",
    ),
    MermaidSnippet(
        id="state-transition",
        name="狀態圖轉移",
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
