# PDF 內嵌註解（Embedded Annotations）實作進度

分支：`feature/pdf-adobe-annotations`（worktree：`.claude/worktrees/pdf-annotations`）。
環境：Windows 11、Python 3.14.4、PySide6 6.11、pymupdf 1.27.2.3。

目標：唯讀顯示 PDF 內嵌的 Adobe 風格註解（Text/Highlight/FreeText/Underline/
StrikeOut/Squiggly，Popup 併入其 parent），與既有 `pdf_notes`/`pdf_highlights`
（外部 sidecar 系統）完全分離、互不影響，絕不寫回 PDF 檔案。

## 事件紀錄

- 讀完 `pdf_view.py`（`_PdfOutlineTask` 背景讀取模式、generation/path 防陳舊
  結果機制）、`pdf_render_scheduler.py`（目前未設定 `RenderFlag.Annotations`）、
  `pdf_notes.py`/`pdf_highlights.py`/`left_panel.py`/`pdf_highlights_panel.py`/
  `pdf_notes_panel.py`，以及 `window.py` 4540-4601 附近的 refresh 慣例。
- 用 throwaway script 驗證 pymupdf 實際行為：`page.annots()` 本身就會排除
  `Popup`/`Link`/`Widget` 子型別（見 `pymupdf.Page.annots` 原始碼），且 Popup
  的文字一律已經寫在其 parent 註解的 `info['content']`——不需要額外的
  parent-lookup 邏輯即可避免重複列出。
- 新增 `app/pdf_embedded_annotations.py`（純資料層，延遲 import pymupdf，
  規則同 `extract_outline`：加密/損毀/非 PDF/pymupdf 缺失一律回傳空清單、
  不拋例外）。
- 在 `app/pdf_view.py` 加入 `_PdfEmbeddedAnnotationsTask`（鏡射
  `_PdfOutlineTask`）、`embedded_annotations_ready` signal、
  `request_embedded_annotations()`／`_on_embedded_annotations_finished()`，
  沿用同一套 generation + path 防陳舊守衛；使用獨立的
  `_embedded_annotations_pool`（而非共用 `_outline_pool`），因為既有測試
  `test_outline_starts_once_after_first_page_paints` 直接替換 `_outline_pool`
  並斷言只送出 1 個任務，共用會讓該斷言失敗。首頁面繪製完成時
  （`_submit_painted_outline`）同時觸發大綱與內嵌註解兩個背景讀取。
- 新增 `app/pdf_embedded_annotations_panel.py`（唯讀清單，無新增/編輯/刪除，
  因為此功能本質上只讀取既有內容）；掛進 `PdfMarkupPanel`（`螢光`/`頁註`
  之外新增第三個子分頁「內嵌註解」），`left_panel.py` 新增
  `pdf_embedded_annotation_callbacks` 參數與 `pdf_embedded_annotations`
  屬性轉發。
- `window.py`：在 4540-4601 一帶新增 `_on_pdf_embedded_annotations_ready`、
  `_refresh_pdf_embedded_annotations_panel`、
  `_pdf_embedded_annotation_activated`（點擊清單項目時用 `reveal`/
  `jump_to_page` 跳頁），並在 `_open_pdf` 內於載入新檔案時立刻清空舊清單，
  避免切換文件時殘留舊結果。
- `RenderFlag.Annotations` 調查：用 throwaway script 確認 PySide6 6.11 有此
  旗標（`QPdfDocumentRenderOptions.RenderFlag.Annotations`），且目前
  `pdf_render_scheduler.py` 從未設定過任何 render flag（預設 `None_`，
  Acrobat 產生的註解外觀完全不會被畫出來）。確認本 app 自己的螢光標記是
  另一套疊加畫法（`PdfView._paint_overlays`，不寫入 PDF 本體），與 PDF 內建
  註解的 appearance stream 互不衝突，於是在 `request()` 裡加上
  `options.setRenderFlags(RenderFlag.Annotations)`。跑
  `test_pdf_render_scheduler.py`（含既有 tile/multithread 契約測試）與
  `test_pdf_async_rendering.py` 全部通過，無回歸；決定啟用。
- 四個目標測試檔 + `test_pdf_render_scheduler.py` 回歸：44 passed（先前有
  `tmp/pdf-annot-tests` 目錄不存在導致的 setup 錯誤，補建目錄後全綠）。
- 寫好 `tests/test_pdf_embedded_annotations.py`（20 案例：資料層抽取、Popup
  不重複、多頁頁碼、加密/損毀/非 PDF/pymupdf 缺失皆回空清單、背景任務
  generation/path 防陳舊、panel 顯示與點擊跳頁、window 層防陳舊守衛）後跑
  全套 `tests`，發現 `test_window_integration.py` 大量失敗：`_FakePdfView`
  測試替身缺少新的 `embedded_annotations_ready` signal、`_FakePanel` 缺少
  `pdf_embedded_annotations` 屬性——補上兩處後恢復正常。
- 全套 `tests` 仍有 1 個既有測試失敗：
  `test_pdf_password.py::test_plain_to_encrypted_does_not_consume_first_paint_during_prompt`
  （非本次新增的測試）。用獨立 repro script 追出根因，分兩層：
  1. `_PdfEmbeddedAnnotationsSignals`（無 parent 的 QObject）在背景執行緒
     `run()` 結尾 `emit()` 時偶發 `RuntimeError: Signal source has been
     deleted`——追到是 `QRunnable` 預設 `autoDelete=True`，C++ 端可能在
     Python 仍持有參照時嘗試回收，改成任務自建構時
     `self.setAutoDelete(False)`（生命週期完全交給
     `PdfView._embedded_annotations_tasks` 這個 dict 管理）解決。
  2. 即使不再崩潰，仍偶發「密碼提示完全沒被呼叫、`load()` 卻回傳
     True」。用最小 repro 證實：**這不是本功能專屬的 bug**——即使只用既有、
     完全未改動的 `_PdfOutlineTask` 在真實（非 mock）`QThreadPool` 上跑，
     同時呼叫一個加密 PDF 的 `load()`，一樣會重現。根因判斷：PyMuPDF 的
     C 擴充在解析 PDF 時很可能整段不釋放 GIL，若此時主執行緒剛好需要透過
     PySide6 呼叫 Qt（`QPdfDocument` 的密碼/狀態判斷疑似依賴內部非同步/
     執行緒協調），會被餓住，導致密碼判斷邏輯的同步回傳值不可靠。這是
     PySide6 + PyMuPDF 背景執行緒疊加 Qt PDF 載入的既有潛在問題，並非本次
     新增程式碼的邏輯錯誤；只是本功能新增了「文件開啟後立刻在背景跑一段
     PyMuPDF 解析」這個之前沒有真實（非 mock）執行過的路徑，才第一次讓它在
     測試裡現形。
  修法：不去動 Qt/GIL 那層（風險高、範圍外），而是讓內嵌註解的背景派工比
  大綱的派工再晚一個事件迴圈——新增
  `_embedded_annotations_submit_timer`（獨立於 `_outline_submit_timer` 的
  singleShot(0) 計時器），`request_embedded_annotations()` 一律透過它啟動。
  這讓本功能的背景執行緒「不可能」與呼叫端在同一個同步呼叫堆疊裡搶
  CPU／GIL（需要多一次事件迴圈輪轉才會真的啟動），實際使用者操作流程
  （點擊開檔）本來就一定會經過事件迴圈，所以功能行為不變；用同一支
  repro script 連續跑 6 次全部穩定重現密碼提示被正確呼叫，全套 `tests`
  連跑兩次皆 1517 passed／75 skipped、exit 0。
  另外把內嵌註解背景任務改用專屬 `QThreadPool(self)`（`setMaxThreadCount(1)`）
  而非 `QThreadPool.globalInstance()`，避免與大綱任務或 Qt 內部可能共用的
  全域執行緒池互相搶執行緒槽。
- Fresh review 回饋修正（追加 commit）：(1) `_embedded_annotations_pool` 改回
  `QThreadPool.globalInstance()`（同 outline 做法）——view 專屬的
  `QThreadPool(self)` 在析構時會 `waitForDone` 等待進行中的掃描，關窗可能卡頓；
  過期結果仍由 generation/path 守衛丟棄。新增
  `test_closing_view_does_not_wait_for_in_flight_scan`（1.5 s 慢掃描進行中
  關閉 view，需 < 0.5 s）。(2) 面板不再把註解顏色當文字前景色（深色主題對比
  差），改為 12 px 小色塊 icon（邊框用主題 border 色），文字維持主題前景色；
  `apply_theme` 會重畫色塊。新增 `test_panel_shows_colour_as_swatch_not_text_foreground`。
  指定三檔：43 passed；全套 1519 passed／75 skipped，exit 0。

## 真實 Acrobat 檔案修正（2026-09-08 追加）

實測檔 `dm00293821.pdf`（22 頁，第 1 頁兩個註解）暴露了合併版本的兩個問題：
頁面上出現一個隨縮放放大的紫色大方塊、側欄找不到註解清單。

### 檔案裡到底有什麼

- xref 367 `/Highlight`：`C=[1 .384308 0]`、`CA=.399994`、有 `QuadPoints`、
  `/Contents` 空、無 `/RC`、`/Subj=螢光標示`、`Popup=368`。
- xref 369 `/Text`：`Name=/Comment`、`C=[.588 .263 .988]`、`F=28`
  （Print+NoZoom+NoRotate）、**`IRT=367`**（它是 highlight 的回覆）、
  `/Contents=測試用`、`/RC` 為 XHTML、`Popup=370`。

Acrobat 面板顯示的「文字反白」**不存在於檔案任何欄位**：那是 Acrobat 繁中
介面對 Highlight 類型的顯示名稱（註解無文字內容時顯示類型名）。檔案裡對應
的欄位是 `/Subj`（值為「螢光標示」），所以 `kind_label()` 優先用 `/Subj`，
沒有才退回內建對照表。

### 改動

1. **頁面自繪 overlay**（`app/pdf_render_scheduler.py:123-131`、
   新檔 `app/pdf_annotation_overlay.py`、`app/pdf_view.py`）：
   `RenderFlag.Annotations` 改回 `RenderFlag.None_`，raster 不再帶 PDFium 的
   註解層；改由 `PdfView._paint_overlays()` 用既有
   `_page_rect_to_screen()` 投影自繪。Highlight 用 QuadPoints + `/C` + `/CA`
   以 Multiply 混色（字仍可讀）；Underline/StrikeOut/Squiggly 畫線；
   Square/Circle/Line/Polygon/PolyLine/Ink 依 rect／vertices／InkList 描邊；
   FreeText 畫框加文字；Text 便利貼畫 **固定 18 px**（不隨縮放放大）的小對話框
   圖示。**IRT 回覆完全不畫在頁面上**，Popup 也不畫。
2. **資料層補欄位**（`app/pdf_embedded_annotations.py`）：`xref`、`in_reply_to`
   （解析 `/IRT`）、`opacity`（`/CA`）、`subject`（`/Subj`）、`marked_text`
   （用 QuadPoints 逐格 `page.get_textbox()`，並垂直內縮避免夾到下一行）、
   `icon`（`/Name`）、`vertices`、`ink`。註解文字依序找
   `/Contents` → `/RC`（去 HTML + unescape）→ Popup 的 `/Contents`。
   新增 `build_annotation_threads()` 與展示用 `kind_label()`／`summary_text()`
   ／`tooltip_text()`（純字串、無 Qt）。父註解不在擷取結果中的孤兒回覆會被
   提升為頂層，不會憑空消失。
3. **面板串成討論串**（`app/pdf_embedded_annotations_panel.py`）：父註解顯示
   `p.N [類型] 「被標的文字」 註解文字 — 作者`，回覆以 `　　↳ ` 縮排列在其下。
4. **可發現性**：`LeftPanel.set_embedded_annotation_count()` 讓「標註」分頁
   標題變成「標註 (2)」、子分頁變成「內嵌註解 (2)」；PDF 載入後狀態列顯示
   「此 PDF 含 N 個 Acrobat 註解（工作面板 → 標註 → 內嵌註解）」6 秒。
   點清單項目仍跳頁 + `reveal()`，並額外呼叫
   `PdfView.flash_embedded_annotation()` 在頁面上閃 1.6 秒藍框；點回覆時閃的
   是它的父註解（回覆自己沒有頁面標記）。滑鼠停在註解上顯示 tooltip
   （作者／時間／被標文字／註解文字／回覆），走 `viewportEvent()` 的
   `QEvent.ToolTip`。

### 回覆圖示的決定

**回覆完全不畫圖示**。理由：(1) Acrobat 本身就不畫——回覆只出現在註解面板；
(2) 這個回覆的 `Rect` 就疊在 highlight 左端，畫了會蓋住被標的文字；
(3) 回覆內容已經在父註解的 tooltip 與面板討論串裡看得到，資訊沒有遺失。
非回覆的獨立便利貼仍會畫固定 18 px 小圖示（`test_sticky_icon_keeps_a_fixed_pixel_size_at_any_zoom`）。

### 驗證

- 用本程式 `PdfView` offscreen 對該檔第 1 頁截圖：100% 與 200% 兩張皆為橘色
  高亮在正確位置、字仍可讀、**沒有紫色方塊、沒有任何回覆圖示**；高亮隨縮放
  正確放大。
- 測試：新增 fixture（`/IRT` 回覆、shapes、`/RC`-only、Popup-only、孤兒回覆）
  與真實檔測試（`pytest.mark.skipif` 保護，檔案未複製進 repo）。
  指定 6 檔 243 passed；全套 `pytest tests` 1614 passed／75 skipped，exit 0。

### 意外發現

`page.get_textbox()` 會把「與矩形相交」的整行都吐出來，而 Acrobat 的
QuadPoints 通常比字行高出零點幾 pt，因此原本擷取到的「被標文字」多帶了下一
行的開頭。改成先把 quad 垂直內縮 `min(h*0.2, 2pt)` 才取字，結果才等於使用者
真正反白的那一行。

## 「備註的文字呢？」— 頁面標記與註解卡片（2026-09-08 再追加）

上一輪把回覆圖示拿掉後，頁面上只剩橘色螢光，完全看不出它帶有回覆「測試用」。
本輪補上三層可見性：頁面標記 → 註解卡片 → 側欄同步。

### 1. 頁面標記（`app/pdf_annotation_overlay.py`）

`wants_marker()`：Highlight／Underline／StrikeOut／Squiggly／Square／Circle／
Ink／Line／Polygon／PolyLine／FreeText 只要**有自己的註解文字或有回覆**，就在
rect 右上角外側（`marker_rect()`，`right+2`、`top-55%`）畫一個固定 **16 px**
的小對話泡泡，用註解的 `/C` 顏色、不套 `/CA`（40% 螢光上的標記才看得清）、
不隨縮放放大、不蓋住被標文字。純螢光（無內容、無回覆）**不畫**標記。獨立
便利貼維持原本 18 px 圖示。標記也計入 hit-test，所以點得到。

### 2. 註解卡片（新檔 `app/pdf_annotation_card.py`）

非模態 `QFrame`，疊在 `PdfView` viewport 上（不是 tooltip 也不是 dialog，
可以邊看被標文字邊看註解）。內容：類型／作者／時間（PDF 日期格式化成
`2026-09-08 12:39`）、註解文字（沒有就顯示「被標文字」加引號）、縮排列出
每則回覆的作者／時間／內容。超長內容用 `QScrollArea`，樣式全走主題 token
（深色可讀）。

觸發與關閉：點圖示或註解本體開啟（點回覆會開它父註解的卡片）；Esc、點別處、
捲動、切頁都關閉；同一時間只有一張手動卡片。

### 3. Acrobat 的 `/Popup`：自動打開的卡片

實測檔 xref 368 是 `Open=true` 的 Popup，Acrobat 開檔就把卡片畫在頁面右側。
資料層補 `popup_open`／`popup_rect`（`/Popup` 的 `/Rect` 乘上
`page.transformation_matrix` 轉成左上角原點）。`PdfView._sync_auto_cards()`
在載入、捲動、縮放、重排時建立／定位／回收這些卡片；位置用 popup_rect 投影，
超出 viewport 就夾到右側邊界。`_paint_popup_leaders()` 從註解 rect 右上畫一條
1 px 註解色引線連到卡片左緣中點。使用者關掉後 xref 記進 `_popup_dismissed`，
本次 session（同一份檔案）不再自動彈出，捲動／切頁／縮放都不會復活；換檔
（`load()`）才清空。多張自動卡片可並存，與那一張手動卡片互不干擾。

### 4. 側欄同步（`app/window.py`、`app/pdf_embedded_annotations_panel.py`）

頁面點擊 → `PdfView.embedded_annotation_selected` → 若側欄**已展開**才切到
「標註 → 內嵌註解」並用 `select_annotation()`（比對 xref）選取該列；不會強制
展開被使用者收起的側欄。反向：側欄點擊除了跳頁與 flash，也開同一張卡片。

### 驗證

- 測試新增 15 個（標記只對有內容／有回覆出現、標記尺寸 0.5x/1x/4x 不變、
  標記實際畫出像素、點擊開卡片且含回覆、Esc／點別處／捲動關閉、點回覆開父卡片、
  卡片 header/body 退回被標文字、`Open=true` 自動顯示、`Open=false` 不顯示、
  關閉後捲動＋縮放不復活、真實檔 xref 367 有標記且自動卡片含「測試用」）。
  指定 5 檔 251 passed；全套 1627 passed／75 skipped，exit 0。
- 截圖（offscreen、真實檔、100%）：`pdf-annot-popup.png` 為開檔未點擊狀態，
  可見自動打開的卡片、引線與橘色標記；`pdf-annot-marker.png` 為關掉卡片後，
  只剩螢光與右上角的小泡泡標記。中文在 offscreen 顯示為方框，屬字型問題。

### 意外發現

`add_highlight_annot()` 預設不建 `/Popup`，要 `annot.set_popup(rect)` 才會有
`popup_xref`；`Open` 只能用 `xref_set_key` 直接寫。另外 Popup 的 `/Rect` 是
PDF 底左原點，若不乘 `page.transformation_matrix`，卡片會垂直鏡射到頁尾。

## Fresh review 三項修正（2026-09-08 第三輪）

1. **卡片高度卡在 70 px 並出捲軸**（`app/pdf_annotation_card.py`）：根因不是
   `setFixedWidth` 後讀 `sizeHint()` 太早，而是 `_rebuild()` 重建的 QLabel 在
   加入 layout 後要等下一輪事件迴圈才會被 show，而**隱藏的 widget 對 layout
   的尺寸貢獻為 0**，所以第二次以後的重建一律量到「空的」。修法：`_add()` 加入
   後立刻 `widget.show()`，`_clear()` 先 `setParent(None)` 再 `deleteLater()`；
   高度改由新的 `content_height_for(width)`（固定寬度後 `layout().activate()`
   ＋`adjustSize()`）算出，上限為 `max_height_for()`＝`min(320, viewport 高 60%)`，
   超過才捲動，並在會捲動時扣掉捲軸寬度重新換行。
2. **手動卡片不跟隨縮放**：新增 `PdfView._reposition_annotation_card()`，與
   自動卡片同一條 `_relayout()` 路徑，縮放／resize 後依 anchor 重新定位（頁碼
   失效才關閉）。
3. **側欄回寫沒有測試**：補 `_on_pdf_embedded_annotation_clicked` 的測試（側欄
   展開時切分頁並選取；未展開時完全不動側欄；entry 為 None 也不動），以及
   `PdfEmbeddedAnnotationsPanel.select_annotation()` 以 xref 比對的測試。

驗證：指定 3 檔 229 passed；全套 1634 passed／75 skipped，exit 0。重截的
`pdf-annot-popup.png` 卡片高 210 px（內容需 208）、捲軸 `maximum()==0`、引線清楚。

## Acrobat 風格卡片＋回覆寫回 PDF（2026-09-08 第四輪）

### 外觀（`app/pdf_annotation_card.py` 重寫）

圓角 8 px、1 px 主題邊框、`QGraphicsDropShadowEffect`（blur 18、offset 0/3）。
版面：標題列（作者粗體＋時間＋右側「×」）／內容區／回覆列表（每則作者粗體＋
時間＋內容，不再用「↳ 註解」）／底部「新增回覆…」`QLineEdit` 加「送出」鈕。
移除預設 `QFrame` 框線（`Shape.NoFrame`），全部顏色走主題 token，深色可讀。
引線保留。

### 拖動與位置記憶

標題列 `_CardHeader` 可拖動，`clamp()` 限制在 viewport 內。放開後
`PdfView._remember_card_offset()` 以**頁點**（除以 scale）記下相對註解 rect
左上角的位移，存進 `_card_offsets`；之後不論捲動、切頁或縮放，
`_dragged_position()` 都以同一相對位置重新定位。換檔（`load()`）才清空。
不寫回 PDF 的 `/Popup /Rect`。

### 寫回 PDF（新檔 `app/pdf_annotation_writer.py`）

`add_reply()`／`edit_annotation()`／`delete_annotation()`，一律
`doc.save(path, incremental=True, encryption=PDF_ENCRYPT_KEEP)`，**絕不整檔重寫**。
回覆是 `page.add_text_annot()` 加 `/IRT`、`/T`、`/Contents`、`/M`，顏色沿用父註解。
寫入前 `writable_reason()` 檢查：不存在／唯讀（`os.access(W_OK)`）／加密
（`needs_pass` 或 `is_encrypted`）／`can_save_incrementally()` 為否，任一成立就
回傳中文原因，UI 用它停用輸入框並顯示在 placeholder。每次寫入前先把原檔
複製到 `mdviewer-pdf-annot-*` 暫存目錄（`closeEvent` 清除）。
`PermissionError` 等失敗一律轉成 `AnnotationWriteError`，由
`MainWindow._perform_pdf_annotation_write()` 以 `QMessageBox.warning` ＋狀態列
呈現；**失敗時不更新任何 UI 狀態**（卡片不會出現那則回覆，
`_loaded_signature` 也不動）。成功後才重新擷取註解、更新 overlay／側欄／卡片，
並把 `_loaded_signature` 設成新簽章，避免檔案監看器跳「已被外部修改」。
編輯／刪除以 `/T` 比對作者，只能動自己建立的。作者名走新設定
`pdf_annotation_author`（設定選單「PDF 註解作者…」），預設 `USERNAME`／`USER`
環境變數或 `os.getlogin()`。

### QPdfDocument 與 sharing violation 實測

在本機（Windows 11、PySide6 6.11）實測：`QPdfDocument` 已載入同一份 PDF 時，
PyMuPDF 的 `doc.save(incremental=True)` **可以成功**（Qt 以共享寫入開檔）。
因此**不需要**先 `QPdfDocument.close()` 再重載，也就不會有頁碼／捲動／縮放
被重置的問題。頁面內容本來就沒變，只有註解變，而註解是本程式自繪的，所以
不必重新 raster。

### 驗證

新增 21 個測試（增量儲存的 bytes 前綴不變與多一個 `%%EOF`、IRT 正確、備份
存在、唯讀／加密／不存在拒絕、只能改自己的、卡片有 × 與輸入框、blocked 時
Enter 不送出、送出不先回填 UI、只有自己的回覆可編輯、拖動記憶跨縮放、拖動
夾在 viewport 內、寫入失敗不改 UI、成功後重讀並重開卡片、刪除回覆聚焦父卡片、
真實檔先複製到 tmp 再寫且驗證 Desktop 原檔 bytes 不變）。
指定 3 檔 229 passed；全套 1652 passed／75 skipped，exit 0。

## Review 後的四個小修（2026-09-08 第五輪）

1. **刪除會連帶砍掉回覆**：MuPDF 刪一個註解時會把整條 `/IRT` 鏈一起刪掉，包括
   別人寫的回覆。`MainWindow._pdf_annotation_delete()` 改為先算
   `_annotation_replies_to()`，有回覆就用 `_confirm_annotation_delete()` 跳
   「將一併刪除 N 則回覆（其中 M 則為他人）。確定要刪除嗎？」，取消就不刪。
   葉節點（沒有回覆）維持不詢問。
2. **備份檔互相覆蓋**：`backup()` 檔名從 `{stem}-{pid}.pdf` 改為
   `{stem}-{pid}-{HHMMSS}-{序號}.pdf`，每次寫入各留一份。
3. **pymupdf 例外逸出**：新增 `_wrapped(what)` context manager，把
   `add_reply`／`edit_annotation`／`delete_annotation` 內的 `_locate`、
   `add_text_annot`、`xref_set_key`、`delete_annot` 等任何例外統一轉成
   `AnnotationWriteError`（訊息為「建立回覆失敗／編輯註解失敗／刪除註解失敗：…」），
   window 端只需處理一種例外。
4. **鎖住 vs 解析失敗**：新增 `_is_locked()`（`PermissionError` 或
   `OSError.winerror in (32, 33)`）與 `LOCKED_MESSAGE`；被獨占鎖住時
   `writable_reason()` 與儲存失敗都回報「檔案正被其他程式使用，無法寫入」，
   真正的解析失敗才說「無法讀取此 PDF」。

驗證：新增 7 個測試（取消刪除不動檔案／確認後才刪、葉節點不詢問、他人回覆
計數、每次寫入各留一份備份且內容不同、三個寫入函式的 RuntimeError 都被包起來、
鎖住與解析失敗的訊息區分、儲存時 PermissionError 的訊息）。
指定 2 檔 243 passed；全套 1659 passed／75 skipped，exit 0。
