# PDF 手形拖曳與閱讀右鍵選單

日期：2026-09-16。本批程式碼與閱讀功能已驗收，已隨 v1.33.0 正式發布，詳見 `2026-09-16-release-1.33.0.md`。

## 需求與範圍

- 使用者希望放大電路圖後，能像 Adobe 一樣用手形工具拖曳。
- 保留文字選取、複製、翻譯、螢光標記及既有內嵌註解功能。
- 補上常用閱讀選單：手形／選取切換、區域快照、縮放／符合寬度、頁面註記、搜尋、文件資訊。
- 修改 PDF 本文、插入圖片、簽名及旋轉視圖不屬於現有閱讀器能力；本批不以無作用選單假裝支援。已詢問是否需要後續編輯功能。

## 驗收條件

1. 手形模式左鍵、空白鍵＋左鍵、中鍵均可水平／垂直拖動；放開與失焦不會卡住。
2. 拖曳不誤選文字或新增螢光標記；文字模式與既有 H 螢光快捷鍵仍可用。
3. 右鍵即使沒有選取文字也有可用操作；選單顯示正確工具狀態。
4. 快照可框選可見區域並複製圖片；Esc 可取消且還原游標。
5. 縮放同步既有狀態；頁面註記使用右鍵所在頁；搜尋接上主視窗；文件資訊可讀。
6. 實跑相關 pytest、offscreen UI smoke，並由 fresh agent 獨立驗收。

## 進度

- 已檢查：`app/pdf_view.py` 自製 QAbstractScrollArea，具備精確座標轉換、非同步 raster／tile 快取，尚無平移手勢。
- 已檢查：右鍵只有文字／螢光功能；註記與搜尋入口可重用主視窗現有能力。
- 已實作：手形／文字工具、Space／中鍵暫時平移、游標與失焦／切檔清理、手形點擊註解。
- 已實作：可見區域快照、符合寬度／縮放、點擊頁面註記、主視窗搜尋、PDF metadata 文件資訊、快捷鍵說明。
- 第一輪驗證：`py -3 -X utf8 -m pytest tests/test_pdf_reading_tools.py tests/test_pdf_highlights.py tests/test_pdf_word_selection.py tests/test_pdf_view.py tests/test_window_integration.py -q -p no:cacheprovider -o faulthandler_timeout=20` → **236 passed，14.12 秒，exit 0**。
- 測試過程修正了測試端的頁面座標參數、固定尺寸註解圖示點擊位置，並改用真正的 QMenu 事件迴圈驗證，避免 Qt 類別 monkeypatch 未生效造成等待。其後上述測試已全部通過。
- 完整回歸：`py -3 -X utf8 -m pytest tests -q -p no:cacheprovider` → **1682 passed、77 skipped，45.24 秒，exit 0**。跳過測試不視為已通過，原始輸出見 `evidence-2026-09-16/pytest-full.txt`。
- 主視窗實跑：`py -3 -X utf8 tools/preview_pdf_reading_tools.py --output docs/upgrades/evidence-2026-09-16` → **exit 0**。真正 MainWindow／PdfView／QMenu，以 QTest 點擊快照操作；亮暗主題均確認選單退出後可框選並複製圖片、手形游標還原、搜尋可見、原 PDF SHA256 不變。
- 已檢視亮／暗選單、完整 PDF 主視窗、區域快照 PNG；文字未裁切，快照無選取框殘留。WebEngine offscreen 有 GPU context 訊息，但 PDF 與選單正確呈現；不等同實體螢幕或高 DPI 驗證。
- Smoke 工具初跑遇到測試暫存 PDF 尚被 raster session 持有；已在工具結束時停止排程並等待 session 釋放，重跑正常清理暫存檔。未為此修改正式渲染管線。
- Fresh agent 獨立驗證：333 項相關測試通過，另以實際 Qt 選單／焦點切換驗證快照像素、H 螢光標記、平移與縮放。完整報告見 `2026-09-16-pdf-hand-tools-review.md`，原始證據另保存於 `evidence-2026-09-16/independent-tests.txt`、`independent-probe.txt`。

## 操作方式與限制

- PDF 任意位置按右鍵 →「手形工具（拖曳移動）」→ 按住左鍵拖曳。回到「文字選取工具」即可拖曳選字。
- 不切工具也可按住空白鍵＋左鍵，或按住滑鼠中鍵拖曳。Space 僅在 PDF 閱讀區取得焦點時接管；不攔截搜尋／註記輸入框的空白。
- 「拍攝快照」框選的是目前可見區域，依目前畫面解析度複製到剪貼簿，可貼至其他程式；不是整頁高解析度 PDF 匯出。
- 「新增頁面註記」沿用應用程式的 `.notes.json` 側錄檔，並非新增 Adobe 內嵌註解。既有內嵌註解的檢視、回覆與編輯維持原功能。
- 本批未加入 PDF 本文／圖片編輯、簽名、旋轉及列印；沒有宣稱等同完整 Acrobat 功能。
- 程式碼可用 `py -3 main.py` 試用；先正常關閉既有 Markdown Viewer，避免單一實例開檔轉交舊版本。正式安裝檔已發布，尚未代替使用者更新目前的安裝版。

## 本機試用執行檔

- 以下是升版前建立的本機功能試用包；正式 v1.33.0 安裝檔請以發布紀錄中的 GitHub Release 為準。
- 建置命令：`py -3 -X utf8 -m PyInstaller --distpath dist/pdf-hand-tools --workpath build/pdf-hand-tools markdown_viewer.spec`。
- 建置結果：**exit 0**，EXE 與 COLLECT 均成功，耗時約 133 秒；紀錄見 `evidence-2026-09-16/build-pdf-tools.txt`。
- 入口：`E:\markdown_viewer\dist\pdf-hand-tools\MarkdownViewer\MarkdownViewer.exe`。需保留整個 `MarkdownViewer` 資料夾及 `_internal` 相依檔案。
- 建置後已核對 EXE 存在；封裝版啟動 **尚未驗證**。偵測到使用者安裝版 `C:\Program Files\Markdown Viewer\MarkdownViewer.exe` 仍執行中，未關閉使用者程式或將測試檔轉交舊版。
- 試用步驟：正常關閉舊版 → 開上述試用執行檔 → 開 PDF 並放大 → 右鍵選「手形工具」→ 測兩軸拖曳、Space／中鍵、快照貼上、切回文字選取。程式碼的相同 UI 路徑已於隔離 MainWindow offscreen 實跑通過。
