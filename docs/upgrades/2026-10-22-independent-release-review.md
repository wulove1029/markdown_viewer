# 2026-10-22 獨立發布驗收

## 目前狀態

閱讀／媒體／來源匯出／PDF lifecycle 獨立驗收通過；補齊大 HTML 背景準備後，鎖版環境整合回歸 73 項通過。
下一步：由主代理整合完整回歸與打包證據；產品檔已交回，獨立生命週期複核無阻擋問題。
限制：不將 offscreen 合成事件宣告為實體 IME／DPI 驗證，不把快取估算宣告為 RSS。

## 驗收流水帳

- 2026-10-22 [CX]｜讀取計畫、既有測試及產品實作｜實際檔案 read-back｜確認來源匯出具獨立快照；PDF 文件與 raster session 有數量及估算容量上限。
- 2026-10-22 [CX]｜檢查測試環境｜`py -3 -X utf8 -c "import sys, PySide6; print(sys.version); print(PySide6.__version__)"`｜Python 3.13.5、PySide6 6.11.0；父程序未設定 QT／RUN_WEBENGINE 環境變數。
- 2026-10-22 [CX]｜新增獨立測試檔｜`tests/test_release_validation.py`｜待實跑：遠處 checkbox 不搶初始位置、圖片 overlay 鍵盤捲動及焦點返回、來源／並排 HTML 匯出異目錄重開、PDF 同 size/mtime 顯式失效後文字與真實 raster 更新。
- 2026-10-22 [CX]｜實跑新增獨立驗收｜下列指令 A｜`7 passed in 4.22s`，exit code 0。首次 fixture 的 PDF 長度差 1 byte，以合法 EOF 後空白補齊測試資料後通過；未因此修改產品。
- 2026-10-22 [CX]｜交叉執行既有真實閱讀與匯出回歸｜下列指令 B｜`17 passed in 11.79s`，exit code 0。

## 指令與驗收結果

指令 A：

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
py -3 -X utf8 -m pytest tests/test_release_validation.py -q --tb=short
```

`7 passed in 4.22s`：

- 實際 `RendererView` 初載有 160 段文字及頁尾 checkbox 的 Markdown：`scrollY == 0`，checkbox 在四個視窗高度之外，未取得焦點。
- 實際 Chromium 圖片 overlay：Qt Enter 開啟、Tab 到圖片內容、PageDown 捲動長圖；Esc 關閉後返回原圖焦點及原本文捲動位置，解除 overflow 鎖定。
- 來源快照 HTML 匯出：捕獲未存文字後再修改 editor，不影響輸出；輸出到另一資料夾並用第二個 WebEngine 重新開啟，文字與本機相對圖片皆正確，磁碟來源不變。來源與並排共用相同 source backend；兩個 UI 模式入口另外驗證。
- 真實 PDF native 文件及 raster：暖返回重用兩個 native 文件；替換同大小／同 mtime 檔案並顯式失效後，文字由 OLD 更新為 NEW，像素由紅色更新為藍色。等待 raster 工作排空後 session 數量各不超過 2，shutdown 清空。
- 真正 `MainWindow` 的 edit／split 模式：完整工具列的 PDF 按鈕可用且可 click；精簡工具列的更多選單匯出可 trigger，兩條路徑派發當前未存本文。此項使用 fake renderer／sidebar 隔離 Qt 主視窗入口，實際匯出則由上述與指令 B 驗證。

指令 B：

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
py -3 -X utf8 -m pytest tests/test_reading_style_webengine.py tests/test_pdf_export_webengine.py tests/test_pdf_native_sessions.py tests/test_renderer_source_navigation.py -q --tb=short
```

`17 passed in 11.79s`：暖色大字／寬內容、長圖 modal、A4／單一長頁／HTML 的未存來源快照與本機圖片、長圖 PDF 僅一頁、PDF 重用／變更／容量與 shutdown，以及真實來源區塊導覽。

## 證據界線與未驗證項目

- PDF native 快取中的 `file_size + page_count * 2048` 是保留成本估算；GUI 文件及 raster 文件各自套用上限，不能解讀為整個程序樹的 32 MiB RSS 限制。在途的舊 generation 會等待完成，數量界線在排空後驗證。
- 本輪未自行重跑效能 benchmark，避免和主代理的全套測試及效能程序互相干擾。效能 P95／多文件工作集／RSS 需引用主代理另行隔離量測的結果。
- 未驗證實體 Microsoft 注音、倉頡／速成候選視窗、實體 100%／125%／150%／200% DPI 與跨螢幕操作、已安裝 EXE、形成性使用者試用。Qt 合成鍵盤與 offscreen Chromium 通過不能取代這些驗收。
- 可照做的實體步驟：以已安裝版本開啟含長文、checkbox、長圖與本機相對圖片的文件；以鍵盤開關圖片、捲動並返回；來源與並排中修改未存文字並匯出 A4／單頁 PDF 與 HTML；檢查尾端文字、圖片、原檔未變，並於不同 DPI 與真實 IME 組字中重做保存及切換。

## 後續補實作：大 HTML 背景準備

完成上述獨立驗收後，主代理另指派補齊計畫中的大 HTML 主執行緒 I/O 缺口。以下是這位驗收代理後續親自實作的內容，與前段獨立驗收區分；交付前由另一位代理檢視這部分。

- 新增 `app/html_loading.py`：worker 以 64 KiB 字元區塊編碼 UTF-8 及寫入暫存檔，GUI controller 接收完成訊號。真實執行緒測試確認準備不在 GUI thread，完成回呼在 GUI thread。
- `RendererView` 保留小頁 `setHtml` 路徑，依每字元最多 4 個 UTF-8 byte、每 byte 最多 3 個 URL byte 的保守上界決策，避免為判斷大小先完整編碼。較大的 HTML 由背景準備後導覽至檔案。
- 每次提交以 serial 防同一 generation 的 partial／full 完成順序反轉；新 generation、small-page 替換與 deactivate 取消舊準備。QObject 銷毀時，純 Python lifetime owner 取消工作並釋放已發布、尚未送達 GUI 的結果，以及已保留檔案；不在 worker 建立或刪除 GUI QObject。
- 保持 `_html_files[].fileName()` 介面，最多保留兩個已完成導覽檔。相對本機資源保留來源 base URL。接上 `background_jobs` 共用 pool、VISIBLE 優先級及 HTML 佇列合併。
- 故障顯示繁中錯誤頁及 render-error marker，不把失敗預覽當成可匯出內容。

實跑指令：

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
py -3 -X utf8 -m pytest tests/test_html_loading.py tests/test_renderer_switching.py tests/test_release_validation.py tests/test_pdf_export_webengine.py tests/test_renderer_async.py tests/test_fast_preview.py -q --tb=short
```

關鍵輸出：`62 passed in 34.01s`，exit code 0。包含原大型文件相對圖片／暖切換／兩份暫存保留，以及新增 900 KB 未存來源的 PDF、HTML 真實匯出：啟動後的編輯不污染輸出，首尾文字與本機圖片保留，完成後暫存檔實際不存在。

```powershell
py -3 -X utf8 -c "import app.html_loading, app.renderer; print('imports OK')"
git diff --check -- app/renderer.py app/html_loading.py tests/test_html_loading.py tests/test_release_validation.py docs/upgrades/2026-10-22-independent-release-review.md
```

匯入輸出 `imports OK`；`git diff --check` exit code 0（僅 Windows LF→CRLF 提示）。測試的初輪一項失敗是既有 `_StubRenderer` 不含新 controller 欄位；small-path cancellation 改採可選 controller 後，既有 seam 與真實 renderer 皆通過。

最終鎖版環境回歸（PySide6 6.11.2，另加入 TXT 長行 handoff 的 fresh review 與既有 11 項測試）：

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_html_loading.py tests/test_renderer_switching.py tests/test_release_validation.py tests/test_pdf_export_webengine.py tests/test_renderer_async.py tests/test_fast_preview.py tests/test_text_loading.py -q --tb=short
.\tmp\upgrade-venv\Scripts\python.exe -m ruff check app/html_loading.py app/renderer.py tests/test_html_loading.py tests/test_release_validation.py
```

輸出：`73 passed in 35.08s`；`All checks passed!`，兩者 exit code 0。73 項為上述 62 項加 TXT 11 項，不能重複相加當成更多獨立測試。

另一位 `plan_delivery_audit` 代理 fresh read HTML lease／controller 與 renderer 整合，並獨立執行 UTF-8、取消、銷毀、過期回覆、thread 與故障測試：`6 passed, 1 skipped, 3 deselected`（該次未啟用 WebEngine），未發現阻擋生命週期問題。本代理另 fresh read `app/text_loading.py`：確認 broker 先在 GUI 接管移交文件，取消／loader 刪除後由 broker 釋放未領取文件；worker 取消時在原 owner thread 釋放文件。對應 11 項 locked 測試通過，未修改這部分實作。

這些結果證明背景執行與內容／生命週期行為，未量化大 HTML 改動的 P95、RSS 或實體桌面耗時；TXT 的單次 Qt 操作及 attach／paint 亦未宣稱達成 8 ms。
