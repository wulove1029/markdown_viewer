# Review: PDF embedded (Adobe-style) annotations panel

Target: worktree `E:\markdown_viewer\.claude\worktrees\pdf-annotations`, branch
`feature/pdf-adobe-annotations`, commit `e11abc4` (parent `26d17e0`). All checks
run in the worktree; no files modified; main tree untouched.

## Per-item findings

1. **Extraction logic (`app/pdf_embedded_annotations.py`) — PASS.**
   - Popup excluded via reliance on pymupdf's `page.annots()` skipping
     Popup/Link/Widget (comment at pdf_embedded_annotations.py:41-52); parent
     content read from `info['content']` (line 134). Verified via
     `test_popup_is_not_double_listed`.
   - Empty-content highlight/underline/strikeout/squiggly falls back to
     `page.get_textbox(annot.rect)` (lines 152-156); covered by
     `test_highlight_with_no_note_pulls_underlying_text`.
   - Encrypted/wrong-password, corrupted, non-PDF, missing file, and
     no-pymupdf all return `[]` without raising (lines 170-199, `except
     Exception` wrapping the whole open+iterate block); 6 matching tests all
     pass.
   - Document opened via `with mupdf.open(...) as doc:` (line 182) — closed
     on every path, no handle leak.

2. **Background task identity — PASS.**
   `PdfView._on_embedded_annotations_finished` (pdf_view.py:1660-1663) checks
   `generation != self._load_generation or Path(path) != self._path` before
   emitting. `test_stale_embedded_annotations_result_is_discarded_after_switching_docs`
   (tests/test_pdf_embedded_annotations.py:254) genuinely calls the internal
   finish handler with a stale generation after switching documents and
   asserts nothing is emitted, then confirms a fresh request for the new doc
   still arrives — this checks behavior, not just a string comparison.
   Window-level guard mirrored in `window.py:_on_pdf_embedded_annotations_ready`.
   **Minor finding**: the new `_embedded_annotations_pool = QThreadPool(self)`
   (pdf_view.py:241) is a dedicated per-view pool, unlike the outline's shared
   `QThreadPool.globalInstance()`. `~QThreadPool()` blocks until running tasks
   finish, so destroying/closing a `PdfView` while an extraction task is
   in-flight can stall (not crash) the GUI thread briefly. Not covered by any
   test; in practice extraction is fast so impact is low, but it's a new risk
   the outline path doesn't have.

3. **`RenderFlag.Annotations` — PASS (by inspection).**
   `pdf_render_scheduler.py:124` sets
   `options.setRenderFlags(QPdfDocumentRenderOptions.RenderFlag.Annotations)`.
   The app's own highlight overlay is a separate paint pass
   (`PdfView._paint_overlays`), never written as a PDF annotation, so the two
   can't collide. `tests/test_pdf_render_scheduler.py` and
   `test_pdf_async_rendering.py` pass unchanged. Visual-diff-on-annotation-free-PDF
   claim is plausible but **not independently verified** (no rendered-pixel
   comparison was run) — flagged as unconfirmed per the task's own caveat.

4. **Panel — PASS.** Empty state message (pdf_embedded_annotations_panel.py:58-61),
   page/kind/author/content-snippet display (58-83), click → `activated`
   callback → `reveal` when rect is non-zero else `jump_to_page`
   (window.py:4706-4711), confirmed by
   `test_window_activation_jumps_to_page_with_rect_when_available` and the
   `..._falls_back_to_jump...` test. Theme: uses `theme.surface` and
   `collection_stylesheet(theme, ...)`, no hardcoded chrome colors. One
   annotation's own PDF color is applied via `item.setForeground(QColor(entry.color))`
   (line 82) — this is data-driven, not hardcoded, but could render poorly
   against a dark background for a dark annotation color; minor cosmetic risk.

5. **Password-race mitigation — judged a real but honestly-scoped mitigation,
   not a fix.** `docs/upgrades/2026-09-08-pdf-embedded-annotations.md`
   explicitly says the root cause (PyMuPDF's C extension likely not releasing
   the GIL during parse, starving Qt's password/status handling) is
   pre-existing and reproducible with the unmodified outline task alone — the
   one-extra-event-loop-tick deferral only prevents the new code from ever
   starting in the same synchronous call stack as the outline dispatch; it
   does not fix the underlying GIL contention, which could in principle still
   surface if some other background task starts in the same tick as the
   outline dispatch in the future. Ran `tests/test_pdf_password.py` 5
   consecutive times: 14 passed each time (no flakiness observed).

6. **Test runs — PASS.**
   - Targeted set (embedded_annotations, pdf_view, pdf_highlights,
     pdf_async_rendering, pdf_render_scheduler, pdf_password): 78 passed.
   - Full suite: `1517 passed, 75 skipped`, exit 0.

7. **Real-PDF scan — inconclusive, honestly reported.** Scanned (read-only,
   ≤200 each) `E:\markdown_viewer\tmp\pdfs` (1 PDF total), `C:\Users\USER01\Desktop`
   (200+ PDFs, capped at 200), `E:\Puritygo\docs` (0 PDFs). **Zero PDFs with
   any non-Link annotation were found** in any of the three roots, so no
   Text/Highlight/FreeText/Underline content could be exercised against real
   Adobe-authored annotations. Ran `extract_embedded_annotations()` against
   the one PDF in `tmp/pdfs` as a smoke test — completed without error,
   correctly returned `[]` (that file has no annotations). This item cannot
   be marked PASS on real data; only the synthetic pymupdf-generated fixtures
   in the test suite were exercised end-to-end.

8. **Commit scope — PASS.** `git show --stat e11abc4`: 10 files, all directly
   related (extraction module, task/panel wiring, render-flag change, one
   upgrade doc, new test file, two test-double additions in
   `test_window_integration.py` for the new signal/attribute). Nothing out of
   scope.

## Nitpicks / gaps found

- Dedicated `QThreadPool(self)` in `PdfView` can block on view/window close
  while an extraction task is running (item 2) — not tested, not a crash but
  a possible stall.
- Item 3 (no visual regression on annotation-free PDFs) is asserted in the
  upgrade doc but not verified with an actual pixel/image diff — taken on
  faith from `RenderFlag.Annotations` semantics and the separation of paint
  paths.
- Item 7 could not be validated against real Adobe-authored annotations —
  no such PDF existed in any of the three required scan locations.
- Annotation's own color applied directly as list-item foreground with no
  dark-theme contrast check (minor cosmetic).

## Verdict

No evidence of claimed-but-undone work; the one test that specifically
targets the stale-result guard verifies actual behavior, not string matching.
All runnable checks (78-test targeted run, 1517-test full suite, 5x password
test) pass cleanly. The unresolved items are the real-PDF validation gap
(no suitable file available in the required search paths) and two
minor/low-risk gaps (pool-blocks-on-close, unverified visual-diff claim).

**Recommendation: mergeable**, with a note to re-run item 7 against an actual
Acrobat-annotated PDF before considering the feature fully validated in
production use, and to consider using the shared/global thread pool (or an
explicit `waitForDone` with timeout in a close handler) to avoid the
close-time stall risk.
