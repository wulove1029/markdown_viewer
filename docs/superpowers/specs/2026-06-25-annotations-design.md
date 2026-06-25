# Markdown Viewer Annotations Design

Date: 2026-06-25
Status: Draft for user review

## Context

Markdown Viewer is a Windows desktop Markdown previewer built with PyQt6 and
PyQt6-WebEngine. Documents are converted to HTML (`app/md_converter.py`) and
rendered read-only in a `QWebEngineView` (`app/renderer.py`). An edit mode lets
the user edit the raw Markdown text. The left side has a ribbon
(`app/ribbon.py`) and a panel (`app/left_panel.py`) with three tabs: file
browser, recent files, and table of contents. Preferences (theme, geometry,
recent files, PDF options) persist via `QSettings`.

The primary user is an engineer reading technical reference material (e.g.
datasheet study notes) who wants to mark up documents while reading.

## Goal

Add a complete annotation layer over the rendered document:

- **Highlights** — select text and highlight it in a chosen color.
- **Notes** — attach a plain-text note to a highlighted selection.
- **Per-selection tags** — tag a highlighted selection (e.g. `重要`, `疑問`).
- **Document tags** — tag a whole file (e.g. `PD協定`, `待讀`) for organization.
- **Cross-file tag filtering** — find files by tag in the recent list, and view
  annotations across recent files filtered by tag.

Annotations must not modify the user's Markdown source and should travel with
the file.

## Non-Goals (YAGNI for v1)

- Rich-text notes (notes are plain text in v1).
- Nested/hierarchical tags.
- Cloud sync or multi-user.
- Cross-file aggregation beyond the recent-files set (no full-disk scan).
- Annotating inside edit mode (annotations apply to the rendered preview only).

## Architecture Overview

Data and persistence are separated from Qt UI. Three new modules plus changes to
existing files and one new JS asset.

| Module | Responsibility |
| --- | --- |
| `app/annotations.py` | Annotation data model + sidecar load/save (pure Python). |
| `app/tag_index.py` | Central tag cache in AppData for cross-file filtering. |
| `app/annotation_bridge.py` | `QWebChannel` `QObject` bridging JS ↔ Python. |
| `assets/annotations.js` | In-page selection toolbar, anchoring, mark rendering. |

Changed files: `app/renderer.py` (web channel + inject JS + render marks),
`app/left_panel.py` and `app/ribbon.py` (new "標註" tab + panel),
`app/recent_files.py` (tag filtering), `app/window.py` (wiring),
`assets/obsidian-light.css` (`mark.annot` styling),
`app/theme.py` (one new ribbon icon).

## Data Model and Storage

### Sidecar file

One sidecar per document, written next to the Markdown file:

- Path: `<md_path>` + `.notes.json` (e.g. `foo.md` → `foo.md.notes.json`).
- Written atomically (temp file + `os.replace`).

Schema:

```json
{
  "schema": 1,
  "doc_tags": ["PD協定", "待讀"],
  "annotations": [
    {
      "id": "uuid4-hex",
      "exact": "the selected text",
      "prefix": "up to 32 chars before",
      "suffix": "up to 32 chars after",
      "textPosition": 1234,
      "color": "#ffd54f",
      "note": "my note text",
      "tags": ["重要"],
      "created": "2026-06-25T10:00:00",
      "updated": "2026-06-25T10:00:00"
    }
  ]
}
```

`annotations.py` defines:

- `Annotation` dataclass mirroring the fields above.
- `DocumentAnnotations` holding `doc_tags: list[str]` and
  `annotations: list[Annotation]`.
- `AnnotationStore` with `sidecar_path(md_path)`, `load(md_path) ->
  DocumentAnnotations`, and `save(md_path, data)`.

### Tag index

`tag_index.json` in the app data location (same area as `QSettings`):

- Maps absolute file path → `{doc_tags, annot_tags, count, mtime}`.
- Updated whenever a sidecar is saved.
- `rebuild()` rescans known files (recent-files list); prunes paths whose
  Markdown file or sidecar no longer exists.
- Query helpers: `all_tags()`, `files_with_tag(tag)`.

## Text Anchoring (TextQuoteSelector)

On **create**, `annotations.js` derives from the current Selection/Range:

- `exact` — the selected text.
- `prefix` / `suffix` — up to 32 characters of document text on each side.
- `textPosition` — character offset of the selection start within the document's
  full `textContent`.

On **load/resolve**, for each annotation:

1. Walk text nodes (TreeWalker) building the full text plus a node/offset map.
2. Find occurrences of `exact`; choose the candidate whose surrounding text best
   matches `prefix`/`suffix`, preferring the occurrence nearest `textPosition`.
3. Build a `Range` and wrap each intersecting text-node segment in
   `<mark class="annot" data-id="..." style="background:<color>">`.
4. If no acceptable match: report the id back to Python as an **orphan** (do not
   render in the document).

Marks do not affect heading-based scroll-spy, the table of contents, or
in-page find (mark elements keep their text). Theme toggle does not reload the
page, so existing marks persist; an explicit reload re-resolves all anchors.

## Interaction Flow

1. **Open file** → `window` loads the sidecar via `AnnotationStore`, populates
   the "標註" panel, and passes annotations to the renderer. After the page
   loads, the renderer injects the JS and pushes the annotation list; JS renders
   marks and reports orphan ids back.
2. **Create** → user selects text; an in-page floating toolbar shows color
   swatches, "備註…", and "標籤…". On action, JS computes the anchor and calls
   `bridge.add(payload)`. Python assigns an id, saves the sidecar, updates the
   index, refreshes the panel, and tells JS to render the mark with that id.
3. **Click a mark** → `bridge.annotationClicked(id)` → window highlights the
   matching card in the panel and shows the note.
4. **Edit / delete** happens in the "標註" panel (Qt UI). Changing color uses
   `QColorDialog` (any custom color allowed). Python updates the model, saves the
   sidecar, updates the index, and tells the renderer to update/remove the mark.
5. **Document tags** are edited as chips at the top of the panel. The **recent**
   list gains a tag filter (backed by the tag index) to find files by tag.
6. **Cross-file view** — the "標註" panel has a "此檔 / 全部(最近)" toggle that
   lists annotations across recent files filtered by tag.

## Bridge API (`annotation_bridge.py`)

`AnnotationBridge(QObject)` exposes `@pyqtSlot`s callable from JS:

- `add(payload_json: str) -> str` (new id returned to JS asynchronously via the
  QWebChannel callback)
- `update(id: str, fields_json: str)`
- `remove(id: str)`
- `reportOrphans(ids_json: str)`
- `annotationClicked(id: str)`

It emits Qt signals (`added`, `changed`, `removed`, `clicked`, `orphansReported`)
that `window.py` connects to update the panel and persist via `AnnotationStore`
and `tag_index`.

## Colors and Theme

A fixed palette of five highlighter colors (yellow, green, blue, pink, purple)
plus a custom color option. Backgrounds use translucent colors so highlights
stay legible in both light and dark themes. The chosen color is stored as a hex
string and applied as the mark's inline background.

## Error Handling

- Sidecar write failure → warn the user; keep the in-memory model.
- Corrupt sidecar JSON → back up to `.notes.json.bak`, start fresh, warn.
- File moved/renamed → the sidecar must move with it (documented in README);
  index rebuild prunes missing paths.
- Editing the Markdown then saving may orphan anchors; reload re-resolves and
  orphans surface in the panel.
- Anchoring search is bounded; acceptable for typical note-sized documents.

## Testing

- `annotations.py` / `tag_index.py` — pytest unit tests: load/save round-trip,
  sidecar path derivation, atomic write, index queries, corrupt-file recovery.
- Anchoring and bridge — headless `QWebEngine` (offscreen): create an annotation
  on known text, reload, assert the mark re-applies; assert the orphan case; and
  assert add/update/remove round-trips write the sidecar.

## Rollout

Ships as a single feature version bump (next patch after 1.2.5), following the
tag-triggered release flow in `DEVELOPMENT.md`. README gains a short
"Annotations" section, including the note that the sidecar `.notes.json` must
travel with the Markdown file.
