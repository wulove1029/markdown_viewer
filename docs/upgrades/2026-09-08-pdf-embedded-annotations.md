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
