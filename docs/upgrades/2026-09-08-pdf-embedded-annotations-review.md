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

## 驗收記錄（f64891f，2026-09-08）

### 逐條結果

1. **PASS** — `app/pdf_render_scheduler.py` 已將 `RenderFlag.Annotations` 改為 `RenderFlag.None_`（見注解說明 PDFium 對 reply icon／NoZoom 縮放的問題）。`app/pdf_annotation_overlay.py` 對 Highlight/Underline/StrikeOut/Squiggly 使用 `entry.quads`（源自 QuadPoints）與 `_color()`/`_alpha()`（源自 `/C`、`/CA`）繪製；`ICON_PX = 18.0` 常數，`icon_rect()` 用 `to_screen(x,y,w,h)` 只取錨點再疊加固定 18px，不隨 `scale` 縮放（程式碼確認，未额外寫 UI 測試驗證縮放但邏輯正確）。`visible_annotations()` 明確排除 `in_reply_to is not None` 的項目，故 IRT 回覆不進 `paint_embedded_annotations()`；程式從未讀取或繪製 Popup。

2. **PASS** — `git diff 8bd93f5..f64891f -- app/pdf_highlights.py` 輸出為空，`pdf_highlights_panel.py` 僅新增匯入共用 `KIND_LABELS`/`build_annotation_threads` 等 helper 與拆分 `parent_label`/`reply_label`，未改動既有繪製或 sidecar 存取邏輯。

3. **PASS** — `build_annotation_threads()` 以 `by_xref` 對應父子，孤兒回覆由 `_drop_orphan_reply_links()` 清除 `in_reply_to` 後仍留在結果中（不遺失）。`_resolve_content()` 依序嘗試 `/Contents` → `/RC`（`_rich_text_to_plain`）→ `/Popup`→`/Contents`。實測 `dm00293821.pdf`：xref 367 Highlight 的 `marked_text` 精確為 `BlueNRG-2 "over-the-air" (OTA)`（彎引號為原文排版），不多不少；xref 369 Text 的 `in_reply_to=367` 正確掛在父下，`build_annotation_threads` 回傳 `(367號highlight, [369號reply])`。

4. **PASS** — `left_panel.set_embedded_annotation_count()` 更新「標註」分頁與「內嵌註解」子分頁文字帶數量；`window._on_pdf_embedded_annotations_ready` 呼叫一次 `statusBar().showMessage(...)`；`_pdf_embedded_annotation_activated` 對回覆會 flash 其父（`target = entry.in_reply_to or entry.xref`）並呼叫 `reveal`/`jump_to_page`。深色主題：overlay 顏色皆來自 annotation 的 `/C`（entry.color）或程式常數 hex，未見讀取 `theme.text` 以外的硬編碼在明暗模式下衝突的顏色；`_paint_free_text` 用固定白底 `QColor(255,255,255,210)`，在深色主題下文字方塊會呈現白底黑框，與周圍深色介面對比明顯但屬刻意設計（模擬紙本便條），非 bug。

5. **PASS（截圖）** — offscreen 建置 `MainWindow`（自訂腳本隔離 QSettings 與 RecoveryStore，仿照 `_isolated_recovery_store`/`_clean_settings` fixture 但使用真實 `PdfView`），開啟 `dm00293821.pdf`，等到 `_pdf_embedded_annotations` 有 2 筆後切到內嵌註解子分頁並 `grab()`。截圖：
   `C:\Users\USER01\AppData\Local\Temp\claude\e--markdown-viewer\81f6123a-460e-4466-86a2-34f99de0b7be\scratchpad\pdf-annot-panel.png`（1500×1000）。
   畫面可見：頁面上橘色 highlight 正確疊在 "BlueNRG-2 "over-the-air" (OTA)" 上、頁面上**沒有**紫色回覆圖示（確認 IRT 不上頁）、左側清單有一個橘色方塊項目（父）與一個縮排的紫色方塊項目（回覆）。中文字型在 offscreen 平台顯示為方框（環境缺字型，非功能問題）。

6. **PASS** — 指定子集：`243 passed`（`test_pdf_embedded_annotations.py test_pdf_view.py test_pdf_highlights.py test_pdf_async_rendering.py test_pdf_render_scheduler.py test_window_integration.py`），exit 0。全套：`1614 passed, 75 skipped`，exit 0。`%APPDATA%\python\markdown-viewer\recovery` 目錄在測試前後皆不存在（本機從未產生過該路徑），未見殘留。

7. **PASS，但有備註** — `PdfView._paint_overlays` 在 `if self._embedded_annotations:` 為真時才呼叫 `paint_embedded_annotations`；文件完全無註解時該分支跳過，零額外工作。但只要文件*任一頁*有註解，`paint_embedded_annotations` 對*每一頁*繪製都會呼叫 `visible_annotations(entries, page)`，其內部是對**全部** entries 做線性掃描＋篩選 `e.page == page`，而非事先依頁分桶。對只有 2 筆註解的文件無感，但屬於「每頁重新掃描全部註解」的 O(pages × annotations) 寫法，註解數量大時（例如某頁上百筆）會在每次重繪時重複掃描，非理想實作，建議未來依頁分桶快取。

### 挑錯清單
- `_paint_overlays`／`paint_embedded_annotations` 對「無註解頁面」確實零成本，但「有註解的文件、翻到無註解頁」仍會對全部 entries 做一次線性掃描（未按頁分桶），效能非最佳但非嚴重問題（見第7點）。
- FreeText／Ink 等有原生 AP 外觀的註解類型，關閉 PDFium Annotations 旗標後改由 `_paint_free_text`/`_paint_shape` 自繪：FreeText 只用固定白底框＋簡單自動換行文字，不解析原生字型/顏色/邊框樣式的 AP stream；Ink 僅用等寬 `_stroke_pen` 直接連點，不還原原生筆刷寬度變化或平滑。對於外觀複雜的 Acrobat 手繪/文字框註解，視覺品質可能不如 PDFium 原生渲染，此點以讀碼判斷，**未實測驗證**（測試檔內無 FreeText/Ink 範例可比對）。
- 深色主題下 FreeText 自繪固定白底（不用 theme.surface），與其餘深色 UI 對比強烈；判斷為刻意的紙本便條視覺效果，非硬編碼顏色 bug，但值得留意是否為預期效果。

### 結論：通過（PASS），有兩項非阻塞性觀察待未來優化（效能分桶、FreeText/Ink 視覺保真度未實測）。
