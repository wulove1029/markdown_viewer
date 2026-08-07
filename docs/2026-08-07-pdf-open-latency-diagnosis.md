# PDF 開啟延遲診斷與修正（2026-08-07）

## 結論

原始問題包含兩種開檔延遲，後續也確認 PDF 縮放有另一條獨立的渲染熱路徑；本次已處理三個確定且低風險的阻塞點：

1. **從檔案總管冷啟動程式再開 PDF：文件庫原本被同步遞迴掃描三次，現在降為一次。**
   診斷量測當時的文件庫 `E:\outputs` 有 4,584 個子目錄、39,054 個檔案，但只有 359 個可開啟文件。修正前完整 `MainWindow` 建構為 5,721.2 ms；修正後實測為 2,277.6 ms，其中唯一一次掃描占 2,107.8 ms。啟動縮短約 60%，但剩下的一次同步全量掃描仍是後續可優化項目。
2. **程式已開啟時開大型 PDF：大綱原本會在 GUI thread 上第二次開檔，現在改成首頁繪製後才於背景擷取。**
   同一份 752 頁、2,914 個書籤的 PDF，修正前 `open_path()` 約 781.7 ms 才返回；修正後為 562.7 ms，第一批可見頁面在 583.3 ms 時已完成繪製，大綱則在 698.7 ms 時背景完成。TOC 不再阻擋首頁，但 `PdfView.load()` 逐頁取得尺寸的同步成本仍存在。
3. **Ctrl+滾輪連續縮放：原本每個輸入 packet 都重排並重新 render，現在最多每 16 ms 合併成一次畫面更新。**
   實際 752 頁 PDF 的 24 個細粒度 packet，逐次精確更新約需 110.0 ms；合併後為 6.06 ms（p90 6.77 ms），約改善 18.2 倍。游標錨點、上下限、切檔與關閉時的最後縮放值均保留。

兩種成本仍會相加。從檔案總管冷啟時，`main.py:148` 會先建完主視窗，直到 `main.py:160` 才處理命令列中的 PDF；因此現階段仍須先等待一次文件庫掃描，再進入 PDF 載入。

## 冷啟動瓶頸

修正前三次掃描的呼叫鏈：

1. `app/file_browser.py:418-419`：`FileBrowserView.__init__()` 呼叫 `refresh_libraries()`。
2. `app/left_panel.py:141,196-200`：`LeftPanel.__init__()` 再套一次 theme；舊版 `FileBrowserView.apply_theme()` 在 `_built=True` 時呼叫 `refresh_libraries()`。
3. `app/window.py:469,690-736`：`MainWindow.__init__()` 尾端再呼叫 `_apply_theme()`，第三次抵達 `refresh_libraries()`。

每次 `refresh_libraries()` 都會經 `app/file_browser.py:494-496` 進入 `_refresh_list()`，再由 `app/file_browser.py:601-760` 對文件庫完整遞迴 `iterdir()`。

修正前同一量測快照：

| 項目 | 結果 |
|---|---:|
| `E:\outputs` 子目錄 | 4,584 |
| 全部檔案 | 39,054 |
| 支援的 Markdown/PDF | 359 |
| 三次 `_refresh_list()` | 1,852.6 / 1,874.8 / 1,804.2 ms |
| 完整 `MainWindow` 建構 | 5,721.2 ms |
| 將掃描替換為 no-op 後建構 | 306.0 ms |

正式程式 log 也吻合。2026-07-28 之後共 29 次可配對的啟動紀錄，從 `starting` 到 IPC server ready：中位 2.625 秒、平均 3.358 秒、nearest-rank p90 7.662 秒、最慢 9.970 秒。例：

- `markdown-viewer.log:193-194`：2026-08-03 啟動耗時 9.970 秒。
- `markdown-viewer.log:251-252`：2026-08-07 啟動耗時 2.866 秒。

回歸來源為 commit `def0e33`（2026-07-13，隨 v1.18.0 發布）。這次為了讓 theme 切換能重新著色檔案圖示，在 `FileBrowserView.apply_theme()` 新增了完整 `refresh_libraries()`；原本已存在的兩層重複套 theme 因而各自變成磁碟重掃，啟動由一次掃描增加成三次。

修正後，`app/file_browser.py:421-467` 的 theme 套用只遍歷既有 `QTreeWidgetItem`，原地替換 icon 與 placeholder/missing-source 的固定文字 brush，不再重建樹或讀取磁碟；選取與展開狀態也不會因換色而遺失。

| 修正後項目 | 結果 |
|---|---:|
| `MainWindow` 建構期間 `_refresh_list()` 呼叫數 | 1 |
| 唯一一次 `_refresh_list()` | 2,107.8 ms |
| 完整 `MainWindow` 建構 | 2,277.6 ms |
| 相對修正前 5,721.2 ms | 約縮短 60% |

## PDF 本身的同步成本

修正前，開檔路徑全部在 GUI thread 串行執行：

1. `app/window.py:1806-1832` → `app/pdf_view.py:208,280-291`：`PdfView.load()` 最終呼叫 `QPdfDocument.load()`。
2. `app/pdf_view.py:308-315`：Ready 後逐頁呼叫 `pagePointSize()`。
3. `app/pdf_view.py:333-366`：為全部頁面建立連續捲動版面。
4. 舊版 `_open_pdf()` 隨即呼叫同步 `outline()`，由 PyMuPDF 再開同一檔並 `get_toc()`。
5. `app/pdf_view.py:491-527`：第一個 paint event 同步 render 可見頁。

實際 752 頁 PDF 的重複量測：

| 階段 | 結果 |
|---|---:|
| `PdfView.load()` 5 次 | 567.5 / 587.0 / 586.5 / 604.8 / 604.2 ms |
| `PdfView.load()` 中位 | 587.0 ms |
| PyMuPDF outline 中位 | 95.3 ms |
| 書籤數 | 2,914 |
| 完整 `open_path` | 約 0.78 秒 |

合成 PDF 基準顯示成本與頁數近似線性。3,000 頁 PDF 的 `load` 中，原始 Qt load 約 10.67 ms、逐頁取得尺寸約 113.25 ms、Python 排版約 4.64 ms。書籤會另外增加 outline 成本；首頁是大型圖片或複雜向量時，render 成本則另外增加。

`app/window.py:344-365,1763-1774` 使用所有分頁共用的一個 PDF view，因此切回已開過的 PDF 仍會重新載入，以上成本會再次發生。

修正後的順序改為：

1. `app/window.py:1853-1883` 先切到 PDF view、清空上一份 TOC，再執行 `PdfView.load()`；正式路徑不再呼叫同步 `outline()`。
2. `app/pdf_view.py:491-527` 在文件確實為 `Ready` 且完成至少一個可見頁面的 paint pass 後，以 0 ms one-shot timer 排入 `_PdfOutlineTask`，讓 paint event 先返回。`Ready` guard 可避免加密 PDF 的密碼 prompt 巢狀 event loop 用舊版面過早消耗新 generation。
3. worker 透過 `QThreadPool` 執行 PyMuPDF；`app/pdf_view.py:878-903` 以 load generation 與 path 丟棄過期結果。
4. `app/window.py:1197-1205` 再驗證目前仍是同一份 PDF，才更新 TOC。這可防止快速 A→B→A、同檔 reload，以及 PDF→Markdown 後的晚到結果覆蓋目前側欄。

同一份 6.45 MiB、752 頁、2,914 個書籤 PDF 的修正後驗收：

| 項目 | 結果 |
|---|---:|
| `open_path()` 返回 | 562.7 ms |
| 返回時 page cache | 0（尚未進 event loop） |
| 第一個 event turn 完成 | 583.3 ms |
| 第一個 event turn 後 cache | 2 頁 |
| 第一個 event turn 後 outline timer | 已排程 |
| outline 背景完成 | 698.7 ms |
| outline entries | 2,914 |

另外直接重複五次 `PdfView.load()` 為 591.8 / 577.4 / 571.9 / 569.6 / 711.4 ms，中位 577.4 ms，與修正前 587.0 ms 同一量級。這符合預期：本次把大綱移出阻塞路徑，但尚未改動逐頁尺寸初始化。

## Ctrl+滾輪縮放順暢度

縮放的延遲不是 QSettings 寫入造成，主要成本是每個高解析度滾輪／觸控板 packet 都清除 PDF pixmap cache、對所有頁面重新排版，接著同步 render 可見頁。實際 752 頁 PDF 的方法級量測如下：

| 路徑 | 中位 | p90 |
|---|---:|---:|
| wheel packet 累積 handler | 0.0038 ms | 0.0047 ms |
| 一次 anchored `set_zoom_factor()` | 0.490 ms | 0.549 ms |
| 其中 `_relayout()` | 0.470 ms | 0.519 ms |
| 可見頁 cache-miss render | 3.03 ms | 3.47 ms |
| 24 packets 逐次更新 | 110.0 ms | 133.7 ms |
| 24 packets 合併成一次更新 | 6.06 ms | 6.77 ms |

`app/pdf_view.py:389-449` 現在先累積原始 delta 與最新游標錨點，以 16 ms `PreciseTimer` 每幀最多套用一次精確倍率。未 clamp 的中間倍率會保留到該幀結束，因此接近上下限時的反向 packet 仍能精確抵消；到達上下限的 outward gesture 會被接受，但不留下 pending timer 或誤觸一般頁面捲動。

`app/window.py:1207-1234` 收到畫面更新後，只立即更新目前倍率與狀態列；兩個隱藏 Markdown renderer 與 QSettings 延後到手勢停止 120 ms 後才同步一次。idle commit 不會把舊倍率送回 `PdfView`，所以不會取消剛到的新一幀。PDF→PDF、PDF→Markdown、reload、empty、detach 與 close 均先 flush 最後一幀再改變文件狀態，避免最後一格縮放遺失。

## 已排除或目前不是主因

- 正式程式實際使用的 `C:\Users\USER01\AppData\Roaming\markdown-viewer\Markdown Viewer\markdown-viewer\tag_index.json` 為 1,732 bytes、10 個 entry，規模仍遠小於足以解釋數秒延遲的程度。不過 `_index_doc_tags()` 每次開 PDF 仍會無條件重寫整份索引，是日後資料量放大時的風險。
- PDF notes sidecar 目前在開檔流程會被讀取/解析三次，highlights 再讀一次；本機小 sidecar 實測成本很小，但雲端或網路磁碟會放大重複 I/O。
- 兩個 `RendererView` 的建構在分段計時中約 70.7 ms 與 3.6 ms，不是這次 5.7 秒建構時間的主體。
- 小型本機 PDF 沒有固定的數秒延遲；數秒現象與冷啟動的文件庫重掃最吻合。

## 回歸保護與驗證

原有測試抓不到此問題，因為 PDF 樣本多為 1-3 頁，而且 window integration 會替換真 `PdfView` 與 `LeftPanel`。本次新增以下不依賴時間門檻的回歸保護：

- `tests/test_file_browser.py`：鎖定建構加上 DARK/LIGHT theme 切換只掃描一次，並確認 icon 與固定文字 brush 原地更新、item identity、選取與展開狀態不變。
- `tests/test_pdf_view.py`：確認首頁建立 page cache 後才送出一次 outline worker，以及相同 path 的舊 generation 結果不會送出。
- `tests/test_pdf_password.py`：以 prompt 內強制 paint 模擬真實 modal nested event loop，確認 plain→encrypted 的 Error 狀態不會消耗 first-paint generation，且解鎖後 worker 捕捉已接受的密碼。
- `tests/test_window_integration.py`：確認正式開檔與 reload 都不呼叫同步 `outline()`，並覆蓋 A→B、同檔 reload、錯誤 path、PDF→Markdown 等 stale result。
- `tests/test_pdf_view.py` 與 `tests/test_window_integration.py`：確認 wheel packet 合併、游標錨點、angle/pixel delta、上下限、同幀反向抵消，以及 switch/reload/empty/detach/close 的最後倍率同步；測試直接驅動 frame/idle seam，不依賴不穩定的 wall-clock 上限。

完整測試以 Qt offscreen 模式實跑：`513 passed, 9 skipped`。另做 worker lifetime smoke：在 worker 執行中刪除 `PdfView` 後再讓工作完成，thread pool 正常結束，且沒有 signal 送到已銷毀的 view。

## 已實作與後續順序

1. **已實作：theme 更新不再重掃磁碟。** 初始建構只掃一次；切換 theme 原地更新 icon。
2. **已實作：先顯示 PDF，再背景載入大綱。** worker 與視窗兩層 generation/path guard 已加入。
3. **已實作：PDF wheel zoom 以畫面幀合併。** 16 ms 精確更新、120 ms idle 同步，並保留游標錨點與切檔前最後一幀。
4. **下一步 P1：文件庫改成 lazy/background scan。** 只建立 library root 與展開中的目錄，避免剩餘一次對 39,054 個檔案的同步掃描阻擋主視窗。
5. **下一步 P1：延後或快取 PDF 頁面尺寸。** `QPdfDocument.load()` 後的逐頁 `pagePointSize()` 仍是大型頁數 PDF 的主要阻塞成本。
6. **下一步 P2：快取 PDF 大綱／每分頁 viewer state。** 可避免切回既有 PDF 時重做 PyMuPDF 擷取，並減少已開分頁的重載。
7. **下一步 P2：合併 sidecar 與 tag index I/O。** notes/tags/highlights 仍有重複讀取，tag index 也會在每次開啟時重寫；本機成本小，但遠端或雲端檔案可能放大。

目前兩個已實作項目均保留既有 PDF 密碼、標註、搜尋與 reload 行為；PyMuPDF `get_toc()` 本身無法取消，因此快速切換文件時舊 worker 仍可能跑完，但結果會被 generation/path guard 丟棄。
