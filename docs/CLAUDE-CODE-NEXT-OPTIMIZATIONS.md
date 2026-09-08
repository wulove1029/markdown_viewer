# 後續優化實作計畫：交接 Claude Code

日期：2026-09-08。專案：`E:\markdown_viewer`。產品基準：已發布 `v1.31.0`（`54b1850`），發布紀錄提交 `5c1b3d1`。

本文件是下一輪實作規格，不代表下列功能已完成。接手時先核對最新 HEAD、工作樹及專案規則，不要假設上述提交仍是最新版本，也不要覆蓋其他未提交內容。

交接文件驗證：2026-09-08，19個引用路徑檢查通過；不同作者fresh read-back通過，已核對現有函式、測試與發布文件，pytest版本查詢exit 0。此為計畫完整性驗收，不是產品功能或效能驗收。

## 1. 目標與交付順序

| 工作 | 使用者得到的改善 | 建議順序 | 規模 |
| --- | --- | --- | --- |
| 大型 Markdown 基線量測 | 確認實際等在哪個階段，讓效能改善可驗證 | 先建立基線 | 中 |
| 首頁新增入口 | 空白工作台可直接開始寫，不必先找選單或經過兩次位置選擇 | 第一批實作 | 小至中 |
| 更新下載流程 | 下載時仍可閱讀、編輯，能取消、重試、稍後安裝 | 第二批實作 | 中 |
| 大型 Markdown 載入改善 | 快速看到可閱讀內容；切換文件不被舊渲染拖住 | 基線完成後分階段實作 | 大 |

每一批獨立提交，避免把 UI 小改與渲染架構重構混在同一提交。遇到跨模組共用部分，先定義責任與介面再修改。

本輪不納入：未命名文件完整生命週期、雲端同步、PDF 大檔重構、更新斷點續傳、自動靜默安裝。不要順手更換 Markdown parser、編輯器或改變既有資料格式。

## 2. 已確認的現況

- `app/renderer.py`：`_MarkdownRenderWorker` 已在背景轉換，已有載入提示、取消事件、generation 與來源區塊定位。不能把「搬到背景執行」當成新改善。
- `app/md_converter.py`：`render_body()`、`_cached_body()`、`convert()` 有 parser lock 與 body cache。取消檢查不代表長時間 parser 呼叫可即時中止；需實測舊工作是否仍阻塞新工作。
- 舊合成量測約為 1 MB 冷轉換 p50 2.26 秒、5 MB 20.09 秒，來源為 `docs/benchmarks/upgrade-after.json`。這不是 1.31.0 的重新量測，也不含真正 WebEngine 首屏，不能直接當新版本 SLA。
- `app/renderer.py` 首頁目前有開啟、最近、快速開啟；`app/window.py::_home_action()` 尚未處理新增。
- `app/new_note_dialog.py::NewNoteDialog` 已有「建立於」及「瀏覽」；重複步驟在 `MainWindow._new_note()` 無可用文件庫時會先另開資料夾對話框。
- `app/updater.py::download_installer()` 使用一次 `response.read()`，取得完整 bytes 後驗證與写檔；已有 HTTPS／可信來源檢查及可選 SHA-256 驗證，需保留。
- `app/update_flow.py` 已有背景下載 thread、request 身分檢查與延後關窗保護；目前下載進度為 ApplicationModal、無取消，成功後直接啟動安裝程式並要求退出。

上述內容以本次交接讀取的原始碼為準；開始前讀回相應函式，避免套用過時行號。

## 3. 大型 Markdown 首次載入

### A. 先建立可重跑的基線

新增量測工具（建議 `tools/benchmark_markdown_first_load.py`，目前尚不存在），產生固定 seed、固定 UTF-8 bytes 數的 100 KB／1 MB／5 MB／10 MB 測試文件。至少包含：一般長文、多段程式碼、大表格、數學／Mermaid、長單一段落。語法壓力案例另行標示，勿混成單一平均數。

分開測：新程序且 app cache 為空的首次開檔、同程序重開、同程序由大檔切換小檔。前者仍可能命中 OS 檔案快取，報告必須注明，不宣稱真正磁碟冷快取。

記錄以下時間點及環境：

1. 接收開檔意圖、載入提示出現。
2. 讀取／解碼結束、parser 結束、HTML 產生完成。
3. WebEngine 導航完成、首段內容實際可見、文件可捲動與可使用的範圍。
4. 全文就緒，以及 Mermaid／數學等延後內容就緒時間。
5. GUI 心跳最大延遲、峰值記憶體、由大檔切小檔的完成延遲。

`loadFinished` 或空白容器出現不能單獨當作可閱讀首屏。可用 generation 對應的 DOM ready marker 加上可見區塊檢查和截圖；最終仍需實體 Windows 操作確認。固定硬體、Python／Qt 版本、封裝類型、測試樣本與次數，先暖機，再對每案例至少 10 次，報 p50／p95／最差值；少量樣本的 p95 僅視為初步指標。

### B. 先處理取消與工作排程

- 採「最新文件優先」；過期排隊任務不開始昂貴轉換，過期結果不能改寫 DOM、目錄、狀態列或來源定位。
- 量測 parser lock 的實際阻塞。如果執行中 parser 無法協作取消，明確決定使用隔離 worker process 或其他可中止方案；不要用 `QThread.terminate()` 強制終止共用狀態。
- 若選 subprocess，補齊 Windows／PyInstaller 啟動、`freeze_support`（若使用 multiprocessing）、IPC、超時、崩潰與退出清理測試，不可只在原始碼模式通過。
- 不讓一份已切走的 5 MB 文件持續占用共享 parser，迫使後開的短文件等待完整解析。單純丟棄最後 callback 不算達成此要求。

### C. 再改善首次可閱讀內容

- 依量測結果選擇瓶頸最大的改動；先改善 parser／HTML 熱點與重複工作，再考慮完整漸進渲染。
- 大檔門檻須由基線決定並集中定義。若完整預覽仍慢，提供明確的「快速文字閱讀」與「載入完整預覽」選擇；部分內容需說明範圍，不能假裝全文已載入。
- 快速模式只能讀原文件，不得把截斷內容當作完整草稿儲存、匯出或更新復原快照。切換到編輯必須載入完整內容並保留既有草稿保護。
- 若採分段渲染，以語法 token／區塊邊界處理，不能按固定行數切開 Markdown。考慮跨段 reference links、footnotes、frontmatter、fences、表格、重複標題 anchors、清單、callouts、數學與 Mermaid。
- 單一巨大 paragraph／table／code fence 必須有退路；避免所有 DOM 已插入，只用 CSS 隱藏而宣稱減少渲染成本。
- 全文搜尋、TOC、來源行定位、捲動恢復在部分載入時應導航到對應區塊或明確要求完整預覽，不可定位到錯誤位置。

### D. 驗收

下列為初始目標，不是已達成數據；先把基線與最終採用門檻落檔。若無法達成，交付實測差距與原因，不能默默降低標準。

- 標準 5 MB 長文：載入回饋目標 200 ms 內；可閱讀首屏 p50 目標 2 秒內，或較同機基線降低至少 50%。若靠快速文字模式達成，另列完整預覽時間，不混報。
- 大檔解析中切到 100 KB 文件：目標 1 秒內可閱讀，且不等待舊任務完成；記錄 GUI 心跳，目標無超過 200 ms 的長阻塞。
- 100 KB 普通文件 p50 不退步超過 10%（考量量測噪音）；峰值記憶體不得無界成長，連續開关至少 20 次後檢查 worker、DOM 與 cache 是否回收。
- 語法壓力案例輸出與既有完整渲染一致；取消、關閉、切頁、變更主題、磁碟內容改變都沒有過期回寫。
- 保留 1.31.0 搜尋來源區塊定位及 pending recovery 狀態，不得將復原提示頁誤當首頁。

## 4. 首頁新增入口

### 使用流程

首頁增加「新增筆記」主要動作，附 `Ctrl+N` 提示；原有開啟、最近與快速開啟保留。首頁按鈕、選單、快捷鍵與檔案樹新增共用 `_new_note()` 及既有建立後開啟流程。

預設 Markdown 與使用者已設定的編輯方式；TXT 隱藏或停用 Markdown 編輯器選項。不要把 Office 強制改為所有人的預設，也不要另造一套檔案儲存邏輯。

### 實作要求

- `_home_action("new")` 只接受真正首頁狀態；保留內部網址攔截，文件正文中的同網址與復原狀態頁不能意外建立文件。
- 合併為一個新增對話框：檔名、位置、類型、編輯方式。`NewNoteDialog` 已具備位置瀏覽能力，優先擴充它，而非新增另一個同功能視窗。
- 位置優先序：明確傳入的檔案樹資料夾 → 目前選取資料夾 → 上次成功新增位置 → 第一個可用文件庫。全無可用位置時對話框顯示「請選擇資料夾」，由使用者在同一視窗瀏覽；不可默存程式目錄。
- 上次位置只在建立成功後記住；已不存在或離線時略過，讓使用者看見最終位置，勿在每次文字輸入時探測網路磁碟。
- 保留原有檔名正規化、Windows 保留字與重名檢查；真正寫入仍需獨占建立，處理預檢後另一程式搶先建立的情形。
- 建立成功後焦點進入正確編輯器，refresh／reveal／recent 各執行一次；取消或錯誤不留空檔、不改最近文件。
- 預設焦點檔名，Tab 順序合理，Enter 建立、Esc 取消；亮暗與 540 像素高度可操作。

### 驗收案例

1. 全新設定、沒有文件庫：從首頁一次打開新增對話框，選位置後成功建立。
2. 有文件庫／檔案樹選取／明確右鍵資料夾：位置優先序正確；離線上次位置可回退。
3. MD 原始碼、MD Office、TXT：建立後進入預期模式，立即輸入、儲存、重開內容一致。
4. 取消、空白名稱、重名、保留字、只讀資料夾、建立競爭：錯誤可理解，來源與其他文件不變。
5. 真首頁可操作；有文件、待復原頁及過期 WebEngine 導航不會觸發首頁新增。

## 5. 更新下載流程

### 使用流程與狀態

「發現更新 → 下載 → 驗證 → 已準備安裝 → 使用者選擇立即安裝／稍後」。下載成功不再自動啟動安裝；提示文案也從「下載並安裝」改為準確描述目前動作。

建議狀態：idle、checking、available、downloading、verifying、ready、cancelled、error、launching。由單一 controller 維護，UI 根據狀態更新；request ID／worker 身分防止舊進度及結果污染重試。狀態名称是設計建議，可沿用既有常數擴充。

### 下載與驗證

- 將 `download_installer()` 改為固定大小 chunk 串流至本次工作專用暫存目錄內的 `.part`，一邊更新 SHA-256，不把整個安裝檔放進記憶體。
- 加入可選 progress callback 與取消事件，盡量維持舊呼叫相容。callback 由 worker 發 signal，GUI 元件只能在主執行緒更新，進度節流至約每 100–250 ms。
- 可信的總長度存在時顯示百分比、已下載／總量；無長度時顯示已下載量與不定進度。傳輸未完成或尚未驗證不能顯示「完成」。
- 成功且驗證通過才把 `.part` 升為可安裝檔。若 server 提供長度，截斷必須失敗；空檔、讀取錯誤、磁碟滿與 hash mismatch 都不啟動安裝。
- 保留 HTTPS、URL／redirect 可信來源與安全檔名檢查。明確處理缺失／畸形 digest：不要標成「雜湊驗證通過」；本輪建議此時提供官方Release連結改由使用者手動下載，不進入 app 內立即安裝。
- 取消後清除本次不完整檔；只能清本次擁有的路徑，不可清整個系統 temp 或其他版本的已下載安裝檔。
- 取消須涵蓋「尚未建立連線、等待讀取、下載中、驗證中、完成競爭」。不可只在下一個 chunk 到達才永久等待；為連線／read 設定有界逾時，記錄最差取消時間。目標 UI 立即回應、worker 5 秒內收尾；若現行 transport 不能達成，調整 transport 並寫明理由。
- 重試建立新工作與暫存檔；本輪不做 HTTP Range／斷點續傳，避免引入版本與 ETag 混合風險。

### UI 與安裝生命週期

- 下載使用非模態對話框或狀態區，可隱藏後重新查看；閱讀、編輯、切頁持續可用。取消與重試是明確動作。
- 「稍後」保留已驗證檔案供本次工作階段再次安裝；若要跨重開保留，需另做持久記錄與重驗證，不能只信任舊路徑。
- 「立即安裝」先走既有未儲存文件與 Office 最終 snapshot 保護。使用者取消儲存／關閉時，不啟動安裝、不退出。
- 只有檔案仍存在且通過驗證、儲存流程完成後才 `QProcess.startDetached`；啟動失敗保持 app 可用並允許重試。不要把 successful startDetached 當作安裝完成或UAC一定獲准。
- 關閉 app 時取消工作並有界收尾；保留 thread 存活安全，不能在執行中銷毀 QThread，也不能讓隱藏視窗因慢網路永久殘留。關閉中的 callback 不得突然啟動安裝。
- 本輪沿用現有檢查頻率與使用者停用自動檢查的偏好；不新增強制更新。

### 驗收案例

以可控假傳輸／本機fixture測正常分塊、未知長度、極慢回應、連線逾時、中途斷線、長度不符、空檔、錯誤hash、缺失／畸形digest、磁碟滿、取消競爭與重試。測試不得為了fixture放寬正式可信來源檢查。

另測下載中編輯、關窗、重複按下載、過期進度、稍後、未儲存取消安裝、Office最後一次输入、啟動失敗。安裝啟動器以 fake 記錄參數與次數，不能在測試機意外安裝或退出使用者主程序。最後做一次官方 Release 真下載與hash比對，實際安裝留作獨立手動驗收。

## 6. 回歸、證據與交付

先讀既有測試並按行為補案例，避免只比對實作字串或為湊數複製測試。

可用的既有測試入口：

- 大檔：`tests/test_renderer_async.py`、`tests/test_renderer_source_navigation.py`、`tests/test_upgrade_performance.py`。
- 首頁／新增：`tests/test_window_integration.py`、`tests/test_editor_workspace_integration.py`、`tests/test_file_ops.py`。
- 更新：`tests/test_updater.py`、`tests/test_update_flow.py`。
- 資料保護：`tests/test_editor_data_safety.py`、`tests/test_recovery_startup_integration.py`、`tests/test_relocation_workspace.py`。

Windows PowerShell：

```powershell
py -3 -X utf8 -m pytest tests -q -p no:cacheprovider --basetemp tmp/next-upgrade-tests
$env:RUN_WEBENGINE_TESTS = '1'
py -3 -X utf8 -m pytest tests/test_renderer_async.py tests/test_renderer_source_navigation.py -q -p no:cacheprovider --basetemp tmp/next-upgrade-webengine
Remove-Item Env:RUN_WEBENGINE_TESTS
```

`--basetemp` 只能指定本次可拋棄的測試目錄，pytest會清理該目錄，不可指向文件庫。測試隔離 QSettings、recent、恢復儲存、標籤及文件庫索引，不能污染個人設定。

每階段在 `docs/upgrades/` 新增日期進度與驗收紀錄，保存測試命令、exit code、before／after JSON、截圖、限制與取消／故障案例。1.31.0 既有 1,457 passed／75 skipped 是舊版證據，不可直接沿用當新版驗收。

渲染與更新涉及程序／thread生命週期，須有不同作者的 fresh review，並跑封裝版。offscreen通過不代表實體高DPI或完整安裝流程通過。未驗項目附明確可照做的步驟，不宣稱所有工作完成。

版本建議：功能形成可發布批次後再依實際HEAD與既有tag決定下一版；不要硬編碼1.32.0。使用 `DEVELOPMENT.md` 流程同步version／installer／CHANGELOG／1–3條RELEASE_NOTES。本次交接授權是實作與本機驗證；前一次1.31.0發布授權不自動延伸至下一版公開發布。

## 7. 可直接貼給 Claude Code 的指令

> 請讀取 `E:\markdown_viewer\docs\CLAUDE-CODE-NEXT-OPTIMIZATIONS.md`，依計畫實作大型 Markdown 首次載入、首頁新增入口、更新下載流程。先核對工作樹及現有規則，建立大型文件效能基線，再分批完成首頁、更新與渲染改善。保留1.31.0草稿復原、搬移安全、搜尋定位等行為。每步落檔、每批實跑測試，由不同作者覆核；最後交付變更摘要、效能前後比較、測試與封裝證據、尚未驗證項目。這次先完成實作和本機驗收，不自行推送下一版tag或公開發布。
