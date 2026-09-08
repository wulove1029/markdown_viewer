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

---

## 複查：commit c41b8b8（PDF 內嵌註解泡泡標記／彈出卡片）

1. **(a)(b)(c)(d) 各有對應測試，行為驗證方式正確** — `tests/test_pdf_embedded_annotations.py`：
   - (a) `test_marker_is_drawn_only_for_annotations_that_carry_a_comment`／`test_marker_keeps_a_fixed_pixel_size_and_stays_outside_the_markup`／`test_marker_is_painted_for_a_commented_highlight_but_not_a_bare_one` 驗證固定 16px、不隨縮放、位於 rect 右上外側、純螢光無內容不畫。
   - (b) `test_clicking_an_annotation_opens_a_card_with_its_replies`／`test_escape_and_a_click_elsewhere_close_the_card`／`test_scrolling_away_closes_the_card`／`test_clicking_a_reply_opens_the_card_of_the_annotation_it_answers` 驗證點擊開卡、Esc／點別處／捲動關閉、回覆掛回父卡。
   - (c) `test_popup_open_and_rect_are_extracted`／`test_an_open_popup_shows_its_card_without_a_click`／`test_a_closed_popup_does_not_open_by_itself`／`test_dismissing_an_open_popup_keeps_it_shut_for_the_session`／`test_real_file_marks_the_commented_highlight_and_opens_its_popup`（用真實檔 xref 367/369）驗證自動彈出與「本 session 不再彈」。
   - (d) 僅測到「側欄點擊開卡」（`test_window_activation_jumps_to_page_with_rect_when_available` 等斷言 `("card", xref)`）；**「頁面點擊回寫側欄選取」完全沒有測試**——`window._on_pdf_embedded_annotation_clicked` 與 `PdfEmbeddedAnnotationsPanel.select_annotation` 在 `git diff` 與全文搜尋中均無任何測試引用，屬宣稱做了但未驗證的部分（讀碼判斷邏輯本身合理：`if entry is None or not self._panel.isVisible(): return`）。
   - `popup_rect` 座標轉換：以真實檔 xref 367 算出 `entry.rect=(234.2, 213.2, 127.6, 13.1)`、`popup_rect=(574.9, 213.8, 183.9, 124.4)`（皆為左上原點）——y 值與 highlight 幾乎同高、x 在頁面右側，換算後會落在 viewport 右上而非頁尾，**PASS**，未見座標轉換錯誤。

2. **FAIL** — 卡片高度自適應邏輯有真實 bug。`app/pdf_annotation_card.py::_resize_for()` 在 `setFixedWidth(width)` 之後立刻讀 `self._content.sizeHint()`，但此時 Qt 尚未完成一次以新寬度為準的 layout pass，`sizeHint()` 回傳的值明顯錯誤地過小。用真實檔（xref 367 highlight + 369 回覆，4 行內容）經 `PdfView` 完整流程重現：`card.size()=(300,70)`（卡到了 min-height 70 的下限），但 `card._content.sizeHint()=(24,22)`——與 4 個 label 實際需要的高度（每行 28px＋間距，總計約 140–160px）完全對不上，導致內容被硬壓進 70px 高的視窗，捲軸因而出現。這正是使用者截圖 `pdf-annot-popup.png`（`crop1.png`：70px 高、可見垂直捲軸、內容被切）所顯示的問題。以獨立建構 `PdfAnnotationCard()`（不掛在 `PdfView.viewport()` 下）測試同樣資料卻得到正確的 212px（無捲軸），代表 bug 與卡片被安裝為 viewport 子 widget 後的樣式表/字型/timing 有關，非資料問題。**判定：短內容仍可能被錯�置底捲軸，不符合「應依內容自適應高度，超過最大才捲動」的需求，需修。**

3. **部分 FAIL** — 生命週期：卡片是 `PdfView.viewport()` 的子 widget（非頂層），`reset()`／`set_embedded_annotations([])` 會呼叫 `close_annotation_card()`+`_sync_auto_cards()` 清掉手動卡與所有自動卡（`deleteLater()`），切換文件正確清空；視窗關閉靠 Qt 父子關係自動釋放，無特殊處理但足夠。`_relayout()`（縮放/resize 觸發）只呼叫 `_sync_auto_cards()`，**未對手動點開的 `self._annotation_card` 做任何跟隨或關閉**：實測開啟手動卡後 `set_zoom_factor(1.0→1.8)`，卡片 `pos()` 完全不變（`QPoint(681,406)` 前後相同）且仍 `isVisible()==True`，而底下的頁面內容/標記已經按新縮放重排，造成卡片與標註在縮放後視覺上脫節——不符合「relayout／縮放後位置是否跟隨」的預期，且沒有測試涵蓋這個情境。自動彈出卡（`/Open=true`）在捲動時會被 `scrollContentsBy()` 內的 `close_annotation_card()`——**注意此函式只關閉 `_annotation_card`（手動卡），不影響 `_auto_cards`**，自動卡走的是 `_sync_auto_cards()` 的 `_popup_dismissed` 集合機制；`test_dismissing_an_open_popup_keeps_it_shut_for_the_session` 證實捲動/縮放/relayout 都不會使已手動 dismiss 的自動卡復活，也不會使未 dismiss 的自動卡消失（`_sync_auto_cards` 依 `popup_open`／`_popup_dismissed`／頁碼有效性決定去留，捲動不在條件內），此部分符合「使用者主動關閉才不再彈，捲動不算主動關閉」的需求。**結論：自動卡生命週期符合需求（PASS）；手動卡在縮放時的位置跟隨/關閉是缺口（FAIL）。**

4. **PASS** — `_paint_popup_leaders()` 只在 `self._auto_cards` 非空且 `card.isVisible()` 為真時才畫每條引線；顏色取 `entry.color`（無則 `#ffb300`）、`pen.setWidthF(1.0)` 確為 1px；終點用 `card.geometry()` 現在的實際座標（`geometry.left(), geometry.center().y()`），卡片被貼齊右側後 `show_pinned()` 已把 `card` 移到 clamp 後的位置，`_paint_popup_leaders` 之後讀到的是新位置，引線終點仍正確指向卡片左緣。

5. **PASS** — `PdfAnnotationCard.apply_theme()` 全用 `theme.surface`／`theme.border`／`theme.text`／`theme.text_muted`，無硬編碼白底黑字；`_paint_free_text`（PDF 頁面上的 FreeText 註解自繪，非本次卡片元件）仍用固定白底 `QColor(255,255,255,210)`，題目已明確排除此例外。深色截圖（見第 7 點）卡片背景深色、文字白色，清晰可讀。

6. **PASS** — 子集：`251 passed`，exit 0（`test_pdf_embedded_annotations.py test_pdf_view.py test_pdf_highlights.py test_pdf_async_rendering.py test_window_integration.py`，`--basetemp tmp/pdf-annot-review3`）。全套：`1627 passed, 75 skipped`，exit 0（`--basetemp tmp/pdf-annot-review3-full`）。

7. **完成（有時序瑕疵）** — 截圖 `pdf-annot-popup-dark.png`（1100×900，offscreen + 真實檔、獨立 `PdfView` 進程，未觸碰使用者 QSettings/RecoveryStore）。畫面可見：深色底、highlight 疊色、橘色泡泡標記、1px 橘色引線、深色卡片背景＋白字（可讀）。**瑕疵**：頁面本身的 PDF 點陣圖未渲染出來（背景仍是白色矩形而非文件內容），推測是本腳本 `processEvents()` 次數不足以讓非同步渲染排程器跑完，屬本次驗收腳本的時序問題，不代表 commit 本身有渲染缺陷（第 6 點的完整測試套件並未發現任何渲染相關失敗）。

### 挑錯清單（本輪新增）
- 卡片高度計算在真實使用路徑下會錯誤地卡在 70px 下限並跑出捲軸，即使內容不算長（第 2 點，已用真實檔重現，非臆測）。
- 手動點開的卡片在使用者縮放頁面時既不重新定位也不關閉，會與標註脫節；且完全沒有測試涵蓋這條路徑（第 3 點）。
- 需求 (d) 的「頁面點擊回寫側欄選取」邏輯已寫但零測試覆蓋，屬於「做了但沒驗證」（第 1 點）。
- 深色截圖因驗收腳本本身的非同步渲染等待不足，頁面內容未顯示（腳本瑕疵，非 commit 缺陷，已於第 7 點註明）。

### 結論：不通過（FAIL）。需修：(1) `pdf_annotation_card.py::_resize_for` 的高度估算 bug（短內容仍跑出捲軸）；(2) 手動卡在縮放後的跟隨或關閉；(3) 補上「頁面點擊回寫側欄」的測試。其餘各點（自動彈出生命週期、引線、深色主題、座標轉換、既有測試套件）驗證通過。

---

## 複驗：commit d934b0e（針對三項 FAIL 的修正）

1. **PASS** — 卡片高度：真實檔 xref367+369 掛在 `PdfView.viewport()` 下重現先前流程，`card.size()=(300,210)`（不再卡 70px 下限），`verticalScrollBar().maximum()==0`；連續 `show_annotation_card()` 3 次高度皆為 `(300,210)`，一致無漂移。根因（`_add()` 立即 `widget.show()`＋`content_height_for()` 於固定寬度下 `layout.activate()` 再讀 `sizeHint()`）修法合理，`test_rebuilding_a_card_measures_its_new_rows`／`test_real_file_card_fits_its_content_without_a_scrollbar` 覆蓋此情境。
2. **PASS** — 手動卡縮放跟隨：實測 `set_zoom_factor(1.0→1.8)` 後 `pos()` 由 `(681,406)` 變為 `(775,469)`，隨 anchor 移動且仍 `isVisible()`；`_reposition_annotation_card()` 已接到 `_relayout()`，並在標註所在頁消失時 `dismiss()`。`test_zooming_keeps_the_card_attached_to_its_mark` 驗證同一行為。
3. **PASS** — `_on_pdf_embedded_annotation_clicked`／`select_annotation` 新增測試驗的是行為：面板開啟時呼叫 `show_pdf_embedded_annotations()`+`select_annotation(entry)`；面板隱藏時兩者皆不呼叫；`entry=None` 時不動面板；`PdfEmbeddedAnnotationsPanel.select_annotation` 用真實 thread 資料按 xref 匹配並回傳 True/False。

全套：`py -3 -X utf8 -m pytest tests -q -p no:cacheprovider --basetemp tmp/pdf-annot-review4` → **1634 passed, 75 skipped**，exit 0。

### 結論：通過（PASS）。三項先前 FAIL 均已修正並有對應測試覆蓋，全套測試綠燈。


---

## 獨立驗收 #5 — commit 3f9122f（寫回 PDF 註解），2026-09-08

驗收者未參與實作，僅依條件查證，未修改程式碼。真實檔 `C:\Users\USER01\Desktop\dm00293821.pdf`
驗收前後 size=1031891、mtime_ns=1788842411806000200 完全一致（實測皆用 tmp 複本）。

### 1. 只用增量儲存 — PASS
`app/pdf_annotation_writer.py:122-135` `_save_incremental()` 是唯一寫入點，
`incremental=True` + `encryption=PDF_ENCRYPT_KEEP`；`add_reply`/`edit_annotation`/
`delete_annotation` 三條路徑都經過它。全 app 目錄下 `doc.save(` 僅此一處
（`pdf_view.py:1866` 是 `painter.save()`）。無 `saveIncr`、無寫暫存再 `os.replace`。
fixture 實測：寫入後原 1132 bytes 前綴 byte-for-byte 相同，僅尾端追加 946 bytes。

### 2. 失敗處理 — PASS
- 唯讀（`os.chmod 0o444`）：`writable_reason` 回「檔案為唯讀，無法寫入註解」，
  `add_reply` 拋 `AnnotationWriteError`，檔案 bytes 不變。
- 獨占 handle（`CreateFileW` dwShareMode=0）：拋 `AnnotationWriteError`
  （訊息為「無法讀取此 PDF…」，語意略偏但型別正確），bytes 不變。
- 加密 PDF（AES-256 有密碼）：`writable_reason` 回「此 PDF 已加密，無法寫入註解」，
  `pdf_annotation_card.set_write_blocked()` 停用輸入框與送出鈕並把原因放進 placeholder/tooltip。
- UI 端 `window.py:_perform_pdf_annotation_write` 先寫入成功才 re-read + 重開卡片；
  `pdf_annotation_card._submit_reply` 明確不先回顯文字，失敗時卡片與側欄都不會出現該回覆。

### 3. 備份 — PASS（有 2 個命名瑕疵）
`session_backup_dir()` = `tempfile.mkdtemp(prefix="mdviewer-pdf-annot-")`；
`cleanup_session_backups()` 只 `rmtree` 自己那個目錄，於 `window.closeEvent` 呼叫。
`backup()` 在 `add_reply`/`edit_annotation`/`delete_annotation` 每次寫入前都呼叫（非只第一次，實測第二次寫入後備份內容有更新）。
瑕疵：檔名 `{stem}-{pid}{suffix}` 會被同 session 後續寫入覆蓋（只保留最後一次寫入前狀態），
且不同目錄同名 PDF 會互相覆蓋。

### 4. 作者與權限 — PASS（刪除有連鎖行為需知會使用者）
`_owned_by()` 比對 `/T` 與目前作者名；非本人編輯／刪除皆被拒且 bytes 不變（實測）。
卡片端 `_ReplyRow(editable=...)`＋`view.set_annotation_author()` 換名後舊列 `begin_edit()` 回 False。
刪除用 `page.delete_annot(annot)`。**實測：刪除父註解時 MuPDF 連帶刪掉所有 /IRT 指向它的回覆**
（xref 5,8,11,13 → 只剩無關的 8,11），不會產生孤兒回覆；但別人寫的回覆也會被一併刪除，
且刪除沒有確認對話框。

### 5. /IRT 正確性 — PASS
fixture：新 xref 8，`IRT=5 0 R`、`T=Alice`、`Contents`、`M=D:2026...`、`Subtype=/Text`、
`Rect=[72 754 88 770]`（與父 rect 左上同點）。`RT` 未寫出（PDF 預設即 /R，Acrobat 相容）。
真實檔 tmp 複本：父 xref 367（USER01 的 Highlight），新回覆 xref 428，
`IRT=367 0 R`、`T=USER01`、`Contents=review reply`、`M=D:20260908141109`、
`Rect=[234.217 612.65 250.217 628.65]`（父 rect x0=234.217，同位置）；
重新 extract 後 thread 為 367 →〔369, 428〕。

### 6. 與 QPdfDocument 共存 — PASS
offscreen `QPdfDocument.load()` 真實檔複本（22 頁, Status.Ready）保持載入，
同時 `add_reply` 成功（+1790 bytes，前綴不變），事後 `doc.status()` 仍 Ready、頁數不變。
`window.py:4812` 在寫入成功後立刻 `self._loaded_signature = self._file_signature(path)`，
而 `_on_file_changed`（window.py:5786）比對 `(st_mtime_ns, st_size)` 相同即 return，
故自家儲存不會觸發外部修改提示。overlay 為自繪，不需重載 QPdfDocument。

### 7. 卡片 — PASS
拖曳限制：`_on_drag()` → `clamp()` 以 parent（viewport）rect 夾住，
測試 `test_a_drag_is_clamped_inside_the_viewport`。位置以 page-point offset 記憶
（`pdf_view._remember_card_offset/_dragged_position`，除以 `_scale`），
測試 `test_dragging_a_card_is_remembered_across_a_zoom`；`load` 時 `_card_offsets.clear()`（僅本 session）。
Esc／點別處關閉：`test_escape_and_a_click_elsewhere_close_the_card`。
配色全部走 `Theme` token，唯一硬編碼是陰影 `QColor(0,0,0,70)`（alpha 陰影，兩主題皆可）；
`apply_theme` 也套用到 auto cards。

### 8. 測試 — PASS
`tests/test_pdf_embedded_annotations.py tests/test_pdf_view.py tests/test_window_integration.py`：
247 passed，exit 0。全套 `tests`：1652 passed, 75 skipped，exit 0。

### 挑錯清單（依嚴重度）
1. 中：刪除自己的父註解會靜默連帶刪除其他人寫的 /IRT 回覆，且無確認對話框。建議加確認並提示會刪幾則回覆。
2. 低：備份檔名 `{stem}-{pid}.pdf` 會被同一 session 的後續寫入／同名不同目錄的檔案覆蓋，只保留最後一份。
3. 低：`add_reply`/`edit_annotation` 中 `_locate`、`add_text_annot`、`xref_set_key` 未包在
   `AnnotationWriteError` 轉換內，罕見的 pymupdf 例外會以原始型別逸出，`window._perform_pdf_annotation_write`
   只攔 `AnnotationWriteError`，可能整個崩掉（不會毀檔）。
4. 低：獨占鎖住的檔案錯誤訊息是「無法讀取此 PDF」而非「被其他程式鎖住」，使用者較難理解。
5. 低：權限僅比對作者字串，同名使用者可互改；`writable_reason` 只在開檔時算一次（寫入時仍會再驗，無風險）。

### 結論
**通過**。沒有找到可毀損使用者 PDF 的路徑（唯一寫入為增量 append，前綴不變，寫前有備份，
失敗不改 UI 狀態）；commit message 的宣稱（增量、備份、事前拒絕、作者限制、
QPdfDocument 共存、signature 戳記）逐條實測皆屬實。建議修第 1 項後再對外宣稱刪除功能完備。
