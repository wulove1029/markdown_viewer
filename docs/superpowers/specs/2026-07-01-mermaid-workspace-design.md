# Mermaid Workspace Design

Date: 2026-07-01
Status: Draft for user review

## Context

Markdown Viewer is already more than a read-only Markdown viewer. It supports
Markdown editing with live preview, PDF reading, annotations, tags, wiki-links,
backlinks, KaTeX math, PDF export, and PPTX export.

Mermaid support also already exists:

- `assets/mermaid.min.js` is bundled for offline rendering.
- `app/md_converter.py` converts fenced `mermaid` code blocks into Mermaid
  render targets.
- `app/renderer.py` renders those blocks in `QWebEngineView` and re-renders
  them when the theme changes.
- `app/fragment_render.py` can render Mermaid/math fragments to PNG for PPTX
  export.

The missing product layer is not basic rendering. The missing layer is a
professional diagram authoring workflow: templates, live validation, export,
and a path back into Markdown source.

## Goal

Add Mermaid as a first-class professional reading and editing capability in
three stages:

1. **Mermaid workspace**: templates, live preview, error feedback, copy Mermaid,
   copy SVG/PNG, and export images.
2. **Markdown deep integration**: detect Mermaid blocks in the current Markdown
   file, let the user choose one to edit, and save changes back into the fenced
   block.
3. **Professional assistant features**: richer templates, snippets,
   formatting, themes, diagram list, and later AI-assisted flowchart creation.

## Non-Goals

- A visual drag-and-drop node editor in the first release.
- Replacing the existing Markdown preview renderer.
- Network-dependent Mermaid rendering.
- A new project or workspace file format.
- AI generation in the first implementation pass.

## Product Shape

The feature should make the app feel like a professional technical reading and
editing tool, not merely a viewer with Mermaid support.

The first release should give users a dedicated diagram workspace:

- Left side: Mermaid source editor.
- Right side: live rendered diagram preview.
- Top toolbar: templates, copy, export, insert/update actions, and status.
- Bottom or inline status area: success state or Mermaid parse/render error.

The workspace can be opened independently from the menu/toolbar, and later can
also be opened from a Mermaid block inside the current Markdown document.

## Architecture Overview

Keep Mermaid behavior in dedicated modules so `app/window.py` only coordinates
actions.

| Module | Responsibility |
| --- | --- |
| `app/mermaid_workspace.py` | Qt widget/dialog for Mermaid editing, preview, toolbar, and status. |
| `app/mermaid_render.py` | Builds self-contained Mermaid preview HTML and JavaScript helpers. |
| `app/mermaid_templates.py` | Built-in templates and snippets. |
| `app/mermaid_blocks.py` | Finds, labels, replaces, and inserts Mermaid fenced blocks in Markdown text. |

Existing modules remain in use:

- `app/md_converter.py` continues to render Mermaid inside normal Markdown
  preview.
- `app/renderer.py` continues to own document preview behavior.
- `app/fragment_render.py` can be reused or adapted for PNG export.
- `app/editor.py` remains the Markdown editor, with optional Mermaid block
  highlighting improvements later.

## Module Interfaces

### Mermaid renderer

`mermaid_render.py` exposes a small pure-Python interface:

- `build_preview_html(source: str, theme: str) -> str`
- `build_export_html(source: str, theme: str) -> str`

The HTML includes the bundled `assets/mermaid.min.js`, initializes Mermaid with
the requested theme, renders one diagram, and records render status in
JavaScript-accessible state.

The QWebEngine-specific work stays in the workspace widget because copy/export
needs access to the rendered page.

### Mermaid blocks

`mermaid_blocks.py` exposes:

- `find_mermaid_blocks(markdown: str) -> list[MermaidBlock]`
- `replace_mermaid_block(markdown: str, block_id: str, source: str) -> str`
- `insert_mermaid_block(markdown: str, source: str, position: int | None) -> str`

`MermaidBlock` contains:

- `id`: stable for the current buffer, based on block order and source span.
- `label`: nearby heading or generated label such as `Diagram 2`.
- `start_line`, `end_line`
- `start_offset`, `end_offset`
- `source`

This keeps Markdown source manipulation testable without Qt.

### Templates

`mermaid_templates.py` exposes grouped templates:

- Flowchart
- Sequence diagram
- Class diagram
- State diagram
- ER diagram
- Gantt
- Mindmap or user journey if the bundled Mermaid version supports it

Each template includes:

- `id`
- `name`
- `description`
- `source`

## Stage 1: Mermaid Workspace

### User Experience

The user opens the workspace from a Tools menu action and optionally from a
toolbar action.

Expected controls:

- Template menu.
- Copy Mermaid source.
- Copy SVG.
- Copy PNG.
- Export SVG.
- Export PNG.
- Reset/new diagram.
- Theme selector or automatic app-theme binding.

Live preview updates with a short debounce, similar to the current Markdown
live preview timer. Rendering errors appear as a clear status message, while
the source remains editable.

### Copy and Export

SVG copy/export:

- Read the rendered `<svg>` from the QWebEngine page with JavaScript.
- Serialize it as SVG text.
- Copy to clipboard or save as `.svg`.

PNG copy/export:

- Prefer rendering the SVG to a transparent or white-background PNG through Qt
  image APIs if practical.
- Reuse the existing `FragmentRenderer` pipeline when it gives more reliable
  output.
- Fall back to screenshotting the rendered SVG bounding box only if needed.

The implementation should choose the most reliable path during development, but
the user-facing behavior is fixed: copy PNG and export PNG should produce a
usable image.

## Stage 2: Markdown Deep Integration

### Detecting Diagrams

When a Markdown file is open, the app can scan the current buffer for Mermaid
fenced blocks:

````markdown
```mermaid
graph TD
  A --> B
```
````

The scanner should support backtick and tilde fences, preserve surrounding text,
and avoid changing indentation or non-Mermaid code blocks.

### Editing an Existing Block

From edit mode, the user can choose an action such as "Edit Mermaid Diagram".

Flow:

1. Scan the current editor buffer.
2. If there is one Mermaid block, open it directly.
3. If there are multiple blocks, show a small chooser with labels and line
   numbers.
4. Open the selected block in the Mermaid workspace.
5. On save/update, replace only that fenced block's source.
6. Mark the Markdown editor modified and refresh the live preview.

### Inserting a New Block

If no Mermaid block exists, or if the user chooses "New Mermaid Diagram", the
workspace can insert a fenced block at the editor cursor position or append it
to the end of the file.

The first implementation can use cursor insertion. Later versions can offer a
choice.

## Stage 3: Professional Assistant Features

This stage builds on the workspace and Markdown integration.

Planned capabilities:

- More templates with practical business/software examples.
- Snippet insertion for common Mermaid constructs.
- Mermaid source formatter for indentation and spacing.
- Diagram theme choices that map cleanly to app light/dark mode.
- Diagram list panel for the current Markdown document.
- Diagram outline/search by label, type, and nearby heading.
- Optional AI-assisted generation from a short user prompt, added only after
  the local workflow is solid.

AI-assisted generation should be designed as an optional adapter later. The
core Mermaid workspace must remain useful without AI or network access.

## Data Flow

### Independent Workspace

1. User opens workspace.
2. Workspace loads default template.
3. Source editor emits text changes.
4. Debounced preview update calls `build_preview_html`.
5. QWebEngine renders Mermaid.
6. Workspace asks the page for render status and displays success/error.
7. Copy/export actions read SVG or generate PNG from the rendered result.

### Markdown Block Editing

1. Main window reads current editor text.
2. `mermaid_blocks.find_mermaid_blocks` returns candidates.
3. User selects one candidate if needed.
4. Workspace opens with that source and a mode of `update_existing`.
5. User confirms update.
6. `replace_mermaid_block` returns the new Markdown text.
7. Editor content is updated, marked modified, and live preview refreshes.

## Error Handling

- Missing `assets/mermaid.min.js`: show a clear error in the workspace and
  disable render/export actions that depend on it.
- Mermaid parse/render error: keep editing enabled, show the Mermaid error text,
  and leave copy/export disabled until a valid SVG exists.
- Empty source: show an empty workspace state rather than an exception.
- Export failure: show a warning with the target path and leave source intact.
- Block replacement conflict: if the editor text changed and the original span
  is no longer valid, rescan and ask the user to choose the diagram again.
- Unsupported Mermaid diagram type in the bundled version: template should not
  be offered until verified against the local Mermaid bundle.

## Testing

Pure Python tests:

- `mermaid_blocks.find_mermaid_blocks` handles single, multiple, tilde, and
  malformed fences.
- `replace_mermaid_block` replaces only the chosen block.
- `insert_mermaid_block` preserves existing text and inserts valid fences.
- `mermaid_templates` has unique ids and non-empty source.
- `mermaid_render.build_preview_html` includes the Mermaid asset only when
  needed and escapes source safely.

Qt/WebEngine tests where practical:

- Workspace constructs without crashing.
- Preview update loads HTML.
- A known valid diagram produces an SVG.
- An invalid diagram produces an error state.

Manual verification:

- Open workspace from menu.
- Choose each template.
- Edit source and see preview update.
- Copy Mermaid, SVG, and PNG.
- Export SVG and PNG.
- Open a Markdown file, edit an existing Mermaid block, save back, and verify
  normal Markdown preview updates.

## Rollout Plan

### Phase 1

Ship the independent Mermaid workspace with templates, live preview, error
feedback, copy source, copy/export SVG, and copy/export PNG.

### Phase 2

Add Markdown integration: detect blocks, choose a block, edit in workspace,
write back to fenced block, and insert new diagram at cursor.

### Phase 3

Add professional assistant features: snippets, formatting, theme choices,
diagram list, richer templates, and optional AI generation.

## Acceptance Criteria

- Users can create a Mermaid flowchart in a dedicated workspace without using
  the Markdown document preview.
- Users can choose a template and immediately see it rendered.
- Invalid Mermaid source shows an understandable error and does not crash.
- Users can copy Mermaid source, copy/export SVG, and copy/export PNG.
- Users can open an existing Mermaid fenced block from a Markdown file, edit it,
  and save it back without changing surrounding Markdown.
- The feature works offline with bundled assets.
