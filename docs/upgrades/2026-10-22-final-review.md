# 1.36.0 最終新增模組獨立驗收

驗收者：fresh reviewer `/root/independent_core_review`。環境為 Windows、鎖版 `tmp/upgrade-venv/Scripts/python.exe`：Python `3.13.5`、PySide6 `6.11.2`。最初新增模組的驗收者未實作受驗產品；後續主代理另行授權修正 PDF 增量幾何與診斷工具設定隔離，本 reviewer 實作這兩部分，再交另一位 fresh agent 驗收，證據列於下方。沒有修改 `PROGRESS.md` 或其他 reviewer 的報告。效能數字與隔離診斷明確區分，未把功能測試當作 EXE 效能驗收。

## 產品範圍結論

未發現以下新增模組的已確認產品阻擋缺陷。此結論限取消、生命週期、隔離與量測標示；不代表實體桌面或全部產品效能門檻通過。

| 範圍 | 獨立查核結果與限制 |
|---|---|
| [background_jobs.py](../../app/background_jobs.py) | 全域 Qt pool 上限為 4；可見工作優先，key 僅合併尚未執行的工作；已開始的工作保有所有權，後續最多保留同 key 的最新排隊工作。完成回呼必須執行的 TXT／搜尋沒有錯用可丟棄工作協定。上限是並行執行數，不是全程序所有不同 key 的總排隊數上限。 |
| [html_loading.py](../../app/html_loading.py) | 每個 request 有 serial／generation 與取消事件；worker 只持有純 Python request，父物件銷毀時取消。已產生但尚未送達 GUI 的檔案由 request 持有並清理；完成後最多保留兩個 navigation 暫存檔。只移除各 lease 建立的隨機暫存檔。 |
| [renderer.py](../../app/renderer.py) HTML 整合 | 大 HTML 在背景編碼／寫入，小 HTML 取代、deactivate 或新 generation 均拒絕舊結果。暫存失敗顯示失敗頁並停止 loading，沒有把舊畫面算成成功。大型未儲存 Markdown 匯出以呼叫當下快照為準，relative image 保留，清理暫存。 |
| [text_loading.py](../../app/text_loading.py) | 讀取分塊取消並檢查前後檔案版本；一般文件限時分批建立，未完成不送 ready；長單行在 worker 建立一次後 moveToThread 至 GUI。GUI broker 持有尚未接收文件，取消或 loader 已刪除仍在正確執行緒銷毀。UTF-16、Emoji、換行、Undo 與儲存往返案例通過。 |
| [diagnostics.py](../../app/diagnostics.py) | 固定 operation 白名單、純數值欄位、有界 event ring 與固定種類 aggregate；不記錄文件內容、查詢、檔名或路徑，預設僅在記憶體。明確匯出使用同目錄暫存與 replace，失敗保留原匯出檔。跨服務 counters 明示為依序取樣、非原子快照。 |
| 說明選單 | 實際建立 MainWindow，以「說明 → 匯出效能診斷…」QAction 觸發驗證；建窗時不自動匯出，點選後傳目前視窗給 `export_dialog`。 |
| [main.py](../../main.py) `--packaged-benchmark` | 分支位於正常啟動前，動態執行入口並以陷阱阻擋 QApplication／IPC／日誌路徑，確認直接轉交 benchmark；不執行一般 session restore 或單一實例傳送。 |
| [benchmark_file_switching.py](../../tools/benchmark_file_switching.py) | 初版 `QSettings(org, app)` 的隔離失效已確認，不能回溯宣稱早期執行未觸及使用者設定。現已改由共用 [isolated_runtime.py](../../tools/isolated_runtime.py) 在 app import 前轉為顯式暫存 INI，七支工具與五種資料 stores 統一隔離；另一位 fresh reviewer 的真 CLI 與 native/data 唯讀快照均通過。完整事故與修復證據保留於下方 P1。 |
| [benchmark_packaged.py](../../tools/benchmark_packaged.py) | parent 要求新的 window-ready 時戳；陳舊 child report 被拒絕而既有 parent report 保留。child return code 非零即退出 1，即使 child 的 readiness `passed` 為 true 亦不算 runner 成功。讀報告時應同時看 `exit_code`；`passed` 描述 child readiness，不是全部性能 KPI。 |

## 指標語意

- Markdown `ready_ms` 是含正確 generation／文件標記的 DOM 加雙 frame 代理；`full_ready_ms` 另外要求沒有 partial notice。
- PDF 代理要求路徑、generation、目標頁與 request time 都符合的 raster paint；不以空白 placeholder 當成功。
- TXT 代理是完整 buffer 安裝及 event processing，報告明示不是 native paint latency。
- packaged launch 指標包含診斷語料建立及隔離 MainWindow construction/show，排除第一份文件繪製；不是乾淨安裝首啟或冷開機。暖 OS cache 亦明示。
- 程序樹 RSS 是 psutil 可讀取程序的總和；無 psutil 時為 `None`，遇子程序已退出或 AccessDenied 會跳過該子程序。這是取樣資料，不能外推為全生命週期峰值或低階機保證。
- `passed` 只確認全部請求得到正確 ready／full ready，程式沒有以它表示 P95／記憶體產品門檻已達成。

## 實際執行證據

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_background_jobs.py tests/test_diagnostics.py tests/test_html_loading.py tests/test_text_loading.py tests/test_benchmark_file_switching.py -q -p no:cacheprovider
```

輸出：`35 passed, 4 skipped in 3.27s`，exit `0`。四項 WebEngine 案例另以下命令啟用，沒有把 skip 當作通過。

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_html_loading.py -q -p no:cacheprovider
```

輸出：`10 passed in 16.52s`，exit `0`，包含實際 Chromium、PDF／HTML 匯出及暫存檔釋放。

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_final_lifecycle_review.py -q -p no:cacheprovider
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check tests/test_final_lifecycle_review.py
```

新增產品控制流程五案最初輸出：`5 passed in 1.10s`；Ruff：`All checks passed!`。新增 fixture 生命週期案例後，主代理修正 fixture，本 reviewer 再以新六案與相關既有案例合跑；最終結果見下一節。

## 全套測試 native crash 的額外查核

主代理提供的 `tmp/upgrade-final-tests.log` 在約 16% 出現 access violation／Aborted，Python stack 停在 `monkeypatch.undo` 的 `setattr`。從 q 輸出的五行各 72 項推算已完成 360 項；與 verbose 排序比對，附近已進入 `test_editor_input.py` 的純函式測試。這不足以直接判定是剛恢復的 attribute 或哪個 native widget 導致 crash。

本 reviewer 另外實跑：

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_editor_data_safety.py tests/test_editor_input.py -q -p no:cacheprovider
```

輸出：`99 passed in 12.13s`，exit `0`；沒有重現原 native crash。因此沒有把一次重跑通過冒充已定因。

但已獨立確認測試 fixture 的生命週期缺陷：`make_data_safety_window` teardown 僅 `window.close()`，之後 `shiboken6.isValid(window)` 仍為 `True`。新增 `test_data_safety_fixture_releases_qt_window_before_settings_restore` 可確定重現，輸出 `1 failed, 5 deselected`。它讓 Qt 視窗與 Python signal 循環留到後續 GC／其他測試執行，不符合隔離清理要求。此為已確認 QA 缺陷，與原 access violation 的因果關係仍需完整重驗，不能僅憑這個 assertion 宣稱 native 根因已證實。

主代理已將該 fixture 改為跳過已失效視窗，並在 settings 還原前使用既有 `_dispose_window` 明確釋放。本 reviewer 讀回修正並獨立重跑：

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_final_lifecycle_review.py tests/test_editor_data_safety.py tests/test_editor_input.py -q -p no:cacheprovider
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check tests/test_final_lifecycle_review.py
```

輸出：`105 passed in 11.38s`、`All checks passed!`，均 exit `0`。其中包含新增六案與原有 99 案。已確認的 fixture 清理缺陷解除；原全套 access violation 尚沒有可重現因果證據，仍不能宣稱 native 根因已證實。該 fixture 產品外修正由主代理完成，本 reviewer 未修改其檔案。

## 文件與未驗範圍

[implementation.md](2026-10-22-implementation.md) 後續已補上十四項實作表、截圖、歷史報告的時間點說明與未驗範圍。本 reviewer 已 fresh read-back；該檔五個本地連結（計畫與四張截圖）全部存在。本報告新增連結亦逐一檢查存在。最終 EXE／installer 數字與產物雜湊仍由主代理在凍結驗收後填入，此時尚未核對。

本輪未執行性能 benchmark、實體 Windows 輸入法／DPI、安裝或更新流程。這些仍需主代理的獨立證據，不由此處的功能測試代替。

## P1：PDF 增量幾何沿用舊 viewport 比例（已修正並獨立再驗）

主代理完整測試在 1,132 案通過後遇到 `test_incremental_mixed_paper_geometry_matches_complete_layout` 失敗；原 assertion 展開 3,000 頁矩陣，pytest 花大量時間在 difflib 比對，並非這次等待本身證明 Qt crash。本 reviewer 以單檔重現 `1 failed, 4 passed in 0.68s`，無需前置完整測試。

第一個差異是 `scale`：完整重排 `2.0476190476190474`、增量更新 `2.080952380952381`，恰為 14 像素垂直捲軸寬度除以固定 420 pt 參考頁寬。捲軸顯示已改變 viewport，但 Qt 尚未送達 resize event；凍結參考頁寬不等於可凍結 viewport-dependent scale。

修正位於 [pdf_view.py](../../app/pdf_view.py) 的 `_apply_geometry_updates`：若 `viewport().width()` 與 `_layout_viewport_width` 不同，即沿用既有完整 `_relayout()` 分支。沒有放寬精確幾何契約。[回歸測試](../../tests/test_pdf_geometry_incremental.py) 新增 deferred resize 期間更新第 1,800 頁，與完整重排精確比較全部欄位；共同 assertion helper 只改成回報首個差異頁，避免整份巨型矩陣 diff。

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_pdf_geometry_incremental.py -q -p no:cacheprovider
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_pdf_geometry_incremental.py tests/test_pdf_switch_smoothness.py tests/test_pdf_native_sessions.py tests/test_pdf_render_scheduler.py tests/test_pdf_reading_tools.py -q -p no:cacheprovider
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_pdf_view.py tests/test_large_source.py -q -p no:cacheprovider
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check app/pdf_view.py tests/test_pdf_geometry_incremental.py
```

本 reviewer 輸出：`6 passed in 0.45s`、`44 passed in 2.68s`、`14 passed in 1.81s`、`All checks passed!`，均 exit `0`。

Fresh agent `/root/reading_design_implementation` 未修改此修正，獨立查核理由與新回歸後執行：

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_pdf_geometry_incremental.py tests/test_pdf_view.py tests/test_pdf_reading_tools.py -q
```

回報 `36 passed in 1.58s`，此 PDF 修正的 fresh 驗收通過。

## 大來源模式額外驗收

讀取 [large_source.py](../../app/large_source.py) 與 window／workspace controls 整合。大檔仍保留完整來源 buffer；選擇 Split 的 UI 會經 `_open_source_editor` 防線回到 EDIT，不能偷偷建立昂貴預覽。新增案例確實發出 mode combo `activated` 訊號，而非只改 selection。另核對 `_load_document`、`_show_empty_state` 及已接受的 `closeEvent` 均取消 TextLoader；loader 以 generation／cancel 防止 stale 結果，window 再核對 path／kind／signature。未完成的部分 buffer 不會開放儲存；既有 UTF-16／CRLF／Emoji 完整保存與取消切換案例均已實跑。

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_final_lifecycle_review.py tests/test_large_source.py -q -p no:cacheprovider
```

輸出：`10 passed in 2.10s`，exit `0`，包括本報告新增七案與三項既有大來源模式測試。

新增 history 清理後，另實跑 WebEngine renderer switching 與七項獨立案例：

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_renderer_switching.py tests/test_final_lifecycle_review.py -q -o faulthandler_timeout=25 -p no:cacheprovider
```

輸出 `22 passed in 8.52s`，exit `0`。包括 warm switch scroll restore、同版本 source anchor 換寬還原、快速切換取消、大片段導航與相對資源。

## 新增圖表流程的 fresh 驗收（早期失敗與修後通過）

已讀取 `assets/diagram_loading.js`、converter／fragment 的 eager policy、renderer `_prepare_diagrams`、source snapshot 匯出等待。讀碼確認原碼獨立保存、錯誤為可閱讀的 terminal state、重新嘗試、generation 改變拒絕未完成匯出。實跑：

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_diagram_loading.py tests/test_diagram_loading_webengine.py -vv -o faulthandler_timeout=25 -p no:cacheprovider
```

輸出 `1 failed, 12 passed in 12.27s`。前 12 案 lazy visibility、theme、超大圖顯式載入、keyboard focus、錯誤保留原碼、PDF／HTML／single-page 匯出全部圖，以及換頁取消 PDF／量測均通過。

新第 13 案 `test_office_fragment_stays_eager_and_invalid_source_finishes_promptly` 的第一個合法 Mermaid `FragmentRenderer.provide(...)` 曾回傳 `None`（line 225）。作者定位為 Qt 6.11.2 未可靠轉換原生 JavaScript bounds array；已改成 JSON 傳輸並驗證四個有限座標。修後圖表、fragment 與 page 輪換合併獨立再驗通過，見下節最終 59 案。較早將圖表與其他模組一起執行的命令在第五案後無進度，已停止該次獨有程序；沒有將這次無診斷 stack 的等待當成已定位的產品 bug。

## EXE 記憶體異常的隔離診斷與產品功能再驗

主代理提供 `tmp/upgrade-memory-debug.json` 與 `tmp/upgrade-memory-clear-history.json`。獨立讀回後確認：清除 history 將 entry count 從 1/11/21/31 限為 1，但 WebEngine 子程序 RSS 仍從 94.8 增至 391.9 MiB（40 次 navigation），不能將 `history.clear()` 宣稱為記憶體問題已解。

Reviewer 建立暫存隔離腳本 `tmp/review_memory_probe.py`，僅使用自行生成的 HTML 與 TemporaryDirectory，沒有操作使用者文件。腳本直接建立 QWebEngineView，排除產品 JS、MainWindow cache 及 worker；數字僅為根因隔離，不是產品 benchmark：

| 41 次載入條件 | WebEngine 起始／最後 RSS | 結論 |
|---|---:|---|
| 純 HTML，2,500 個段落，setHtml + clear history | 76.4／523.5 MiB | 不需產品 JS 即可重現 |
| 同一純 HTML，file URL + clear history | 76.1／509.1 MiB | 不是僅 data URL transport |
| 純 HTML，顯式 `--disable-features=BackForwardCache` | 78.1／525.8 MiB | 此開關無法解除問題 |
| 空 HTML | 63.1／77.3 MiB | 成長與文件內容規模相關 |
| 每十次更換 QWebEnginePage，明確銷毀舊 custom page | 78.6／79.0 MiB | 舊 renderer process 退出；第 10/20/30 次為 78.9/76.3/76.2 MiB |

每次額外 idle 一秒的 21 次載入仍為 78.9→303.9 MiB。診斷模式暴露 V8 GC，GC 將 used JS heap 約 9 MiB 降至 0.45 MiB，但不能釋放相應 RSS，故不能將產品 JS heap 當成已確認根因。只有 page lifetime 的隔離回收確實釋放子程序；仍需主代理的產品整合、取消／還原測試與重新打包實測後才能宣稱交付門檻達成。

執行指令範例：

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tmp/review_memory_probe.py plain
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tmp/review_memory_probe.py plain file
$env:REVIEW_EXTRA_FLAGS='--disable-features=BackForwardCache'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tmp/review_memory_probe.py plain
# In a fresh shell, without the prior diagnostic flag:
$env:REVIEW_RECYCLE='1'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tmp/review_memory_probe.py plain
```

產品後續由另一位作者實作每十次實際 navigation 輪換 page，在新 generation 提交前觸發；同 generation partial→full 不輪換。保留 settings/font/scripts/profile/channel，retired callback 拒絕舊結果；實際 PDF printing 期間保留原 DOM、延後最新 navigation 並拒絕重入。本 reviewer 未實作此部分，已查核並實跑。

另獨立發現原 `wikilink:` 在首次載入、尚未輪換時即被 Chromium 轉成 `about:blank#blocked`，故不能歸因於輪換。作者改用已保留的 internal HTTPS host，navigation handler 以 FullEncoded path 解碼一次後送出訊號，不發網路請求；字面 `%2F`、中文、空白、`#` 與 slash 測試涵蓋。原 scheme 相容處理保留。

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_renderer_page_rotation.py tests/test_diagram_loading.py tests/test_diagram_loading_webengine.py tests/test_renderer_switching.py tests/test_html_loading.py tests/test_renderer_source_navigation.py -q -o faulthandler_timeout=30 -p no:cacheprovider
```

本 reviewer 最終獨立輸出 `59 passed in 46.92s`，exit `0`。包含首次與輪換後原生 wiki click、註記/task bridge、舊 page 銷毀、source/progress、實際 PDF 與圖表/fragment 的完整測試。本範圍沒有遺留已確認阻擋缺陷。

主代理另提供 `tmp/upgrade-rotation-mainwindow.json`；本 reviewer 讀回 source MainWindow 10 文件、100 次 warm switching：283.57→285.99 MiB（+0.85%）、取樣 peak 363.77 MiB、全關 259.64 MiB，程序數穩定。這是在設定隔離修復前取得的 source 根因資料，不能宣稱其執行未碰個人設定，也不能當作新 EXE 的最終效能證據。新 EXE 交付數字由主代理重建後另驗。

## 搬移復原 QA：Windows venv 啟動器退出不等於 worker 退出

主代理全套測試在 1,393 案通過後，`test_recovery_can_resume_after_a_second_forced_process_exit` 得到 active lock。獨跑模組第一次 `17 passed, 1 skipped in 12.84s`，不足以排除 race。

獨立隔離腳本 `tmp/review_relocation_lock_probe.py` 重複此真實強制退出案例 20 次，記錄 `Popen.pid` 與其實際 Python 子程序；20 次中 2 次重現原 `另一個文件搬移或復原工作正在執行`。證據顯示 Windows venv launcher 的 `wait()` 已返回，而真正 Python worker 仍 `is_running()`；例如 launcher 48292、worker 38856 對應第 9 次失敗。第一層原 helper 有 pipe EOF 同步，第二層復原測試僅 wait launcher，OS file lock 仍可能正在釋放。

僅修改 [test_relocation_journal.py](../../tests/test_relocation_journal.py)：`kill_worker_and_wait` 在 kill 前捕捉此工作程序的 descendants，kill／communicate launcher 後，等待每個實際 worker 結束，再進行復原。使用正式依賴 psutil；沒有殺其他工作程序、沒有睡眠猜測、沒有產品 retry，也沒有放寬 `rolled_back`／完整原檔內容 assertion。

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_relocation_journal.py -q -p no:cacheprovider
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check tests/test_relocation_journal.py
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tmp/review_relocation_lock_probe.py
```

輸出：`17 passed, 1 skipped in 10.60s`、`All checks passed!`、20 次均 `PASS`／`failures 0`。一項 skip 是本機建立 directory symlink 的權限限制；沒有把它算成通過。此為測試程序生命週期修正，產品 journal 鎖保持不變；已交主代理執行最終獨立再驗。

## 閱讀狀態與搜尋 benchmark 定義補驗

閱讀狀態透過 workspace sync 隨文件 reset，在寫作時隱藏；章節與比例 signal 不直接改動文件。右側閱讀工具與屬性 tabify 的四案獨立重跑：

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_drafts_reading_tools.py -q -p no:cacheprovider
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m py_compile tools/benchmark_library_search.py
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tools/benchmark_library_search.py --help
```

輸出 `4 passed in 1.56s`；compile／help 均 exit `0`。

[benchmark_library_search.py](../../tools/benchmark_library_search.py) 讀碼確認生成獨立語料、每種 query（英文、繁中一字／二字）各 30 次以上，分立即派送與 300ms debounce；首 row 的 end-to-end／after-dispatch 與 complete 分別統計，不把初次建立 index 混入暖樣本。每次檢查完整搜尋檔數、結果檔數與 row 數，另量 UI batch duration。viewport repaint 明示為 offscreen 代理而非實體螢幕／EXE。`passed` 反映完整結果 assertion 後的 decode 次數條件，不是性能 KPI 通過。設定隔離初次判斷不足已在下節更正並修復。本 reviewer 僅查核定義與可執行入口，效能實跑留給主代理在閒置環境執行。

## 貼上預覽合跑 QA：初始化游標復原覆寫測試選取

本節由貼上功能實作者 `/root/writing_core_implementation` 補記。主代理 `/root` 的 fresh 合跑先發現 `1 failed, 21 passed in 20.70s`（`tmp/upgrade-office-commands-final.log`）；以下定因與修後三次執行由實作者完成，交主代理作最終獨立驗收，不屬於本報告原 reviewer 的執行成果。

測試只等 Markdown 值就緒，隨即用 JavaScript 設定選取；各案例仍共用未命名文件的 session key，獨立 QSettings 並未隔離 WebEngine 的 localStorage。暫時加入選取呼叫堆疊與 DOM／Undo 記錄後，合跑再次重現：530.2 ms 時選取 `selected`，532.3 ms 時 `restoreDocumentSession` 的 `applySession`（`vditor_glue.js:1531`）復原舊游標，呼叫 `removeAllRanges`；貼上事件發生時已是 `(2, 2)` 空選取，初始 `undoStack.length` 仍為 `0`。該次沒有 DOM 全文替換或 Undo 寫入事件，證據在 `tmp/paste-combined-diagnostic2.log`；不是貼上之後才把儲存的 Range 毀損。

唯一修正位於 [test_paste_preview_webengine.py](../../tests/test_paste_preview_webengine.py) 的 fixture：載入前設定 `tmp_path / "note.md"` 作為獨立文件工作階段，並等待初始 `undoStack.length >= 1` 後才允許測試設定選取。沒有增加固定睡眠、移除斷言或更動產品程式；暫時診斷 instrumentation 已移除。原有 `pendingPaste.range.toString() == "selected"`、取消恢復選取、原位置插入與單次 Undo 均保留。產品資源與打包內容不受這次測試修正影響。

以下為鎖版環境實際連續執行的命令；`tests/conftest.py` 設定 `QT_QPA_PLATFORM=offscreen`：

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_office_commands_webengine.py tests/test_paste_preview_webengine.py -q --tb=short > tmp/paste-combined-stable1.log 2>&1
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_office_commands_webengine.py tests/test_paste_preview_webengine.py -q --tb=short > tmp/paste-combined-stable2.log 2>&1
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_office_commands_webengine.py tests/test_paste_preview_webengine.py -q --tb=short > tmp/paste-combined-stable3.log 2>&1
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check tests/test_paste_preview_webengine.py
```

| 連續合跑 | 實際輸出 | 程序結果 |
|---|---|---|
| 第一次 | `22 passed in 21.46s` | exit `0` |
| 第二次 | `22 passed in 21.12s` | exit `0` |
| 第三次 | `22 passed in 21.47s` | exit `0` |

Ruff 輸出 `All checks passed!`，exit `0`。此處是測試工作階段與初始化契約的修正證據，不外推為實體輸入法、所有游標復原時序或全部 Office 效能均已驗證。

## P1：量測工具的 QSettings 隔離實際未生效（已修復並獨立再驗）

本 reviewer 先前從 `setDefaultFormat(IniFormat)`／`setPath` 讀碼判定隔離，該結論不成立。後續讀回 `tmp/upgrade-rotation-mainwindow.json` 發現 `settings` 真實值為 `\\HKEY_CURRENT_USER\\Software\\markdown-viewer\\MarkdownViewer`；此處記錄修正，不隱藏先前驗收不足。

使用全新、非產品的 fake organization，僅建立 QSettings 物件讀取 format／fileName，**沒有寫入任何值**，獨立實測：

```python
QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, temp_folder)
settings = QSettings("ReviewDisposableIdentity", "NoWrites")
print(QSettings.defaultFormat(), settings.format(), settings.fileName())
```

鎖版輸出：`Format.IniFormat Format.NativeFormat \\HKEY_CURRENT_USER\\Software\\ReviewDisposableIdentity\\NoWrites`。顯式 `QSettings(IniFormat, UserScope, org, app)` 才落在指定暫存 INI。

因此切換 benchmark 的 `settings.setValue("update_check_enabled", False)`、MainWindow 的設定讀寫以及 PDF 位置／close session 等可能觸及真實產品設定；同型態其他 benchmark 亦需檢查。已立即通知主代理停跑新 benchmark、修成工具子程序內真正隔離的 QSettings factory，並要求在任何設定寫入前驗證路徑。自動生成的測試文件與其他已顯式隔離的 stores 不因此變成個人文件；但不能宣稱個人設定未受影響。未知原值不得憑猜測還原。

修復由本 reviewer 實作 [isolated_runtime.py](../../tools/isolated_runtime.py)。每一種合法 QSettings constructor 都轉為工具 root 內明確的 INI filename；User/System scope、organization/application 各自分離，關閉 fallback。filename overload 若本來指向 root 外部，也映射為 root 內檔案，禁止讀寫該外部檔案。QObject parent、共享 org/app identity 與 scope metadata 保留。安裝先替換 QtCore class，再重綁已載入 app 模組的 QSettings aliases；最後才載入並設定 library、recovery、tag colors、tag index、relocation journal 五種 default stores。歷史 baseline 缺少某 store 模組時可略過，但模組內缺依賴仍明確失敗。

以下七支工具現在於 app imports 前安裝共用隔離；需要 WebEngine 的工具於 QApplication 建立後、任何頁面建立前設定隔離 storage/cache：`benchmark_file_switching.py`、`preview_reading_workspace.py`、`benchmark_library_search.py`、`benchmark_editor_writing.py`、`benchmark_text_loading.py`、`preview_pdf_reading_tools.py`、`verify_session_restart.py`。Session 工具的五個 child 使用同一 owned root 與 org/app 映射，保留跨程序重開契約；parent 僅唯讀既有設定作前後比較。主代理另將 main 的 packaged benchmark 分支提前至任何 PySide/app import 前。

[test_isolated_runtime.py](../../tests/test_isolated_runtime.py) 在全新子程序覆蓋 18 種 constructor 組合（含 PySide 合法的 `objectName` 屬性 kwargs）、外部 INI sentinel 完全不變、pre-import alias、scope/org/app 分離、五種 stores、重新 install、baseline 缺模組與缺依賴區別，以及七支工具在任何 app import 前必須抵達隔離邊界。其他屬性 kwargs 交原生顯式 INI constructor 驗證；即使傳入 `fallbacksEnabled=True`，隔離層仍強制關閉 fallback。

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_isolated_runtime.py tests/test_final_lifecycle_review.py -q -p no:cacheprovider
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m mypy tools/isolated_runtime.py tools/benchmark_file_switching.py tools/benchmark_library_search.py tools/benchmark_editor_writing.py tools/benchmark_text_loading.py tools/preview_reading_workspace.py tools/preview_pdf_reading_tools.py tools/verify_session_restart.py
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check tools/isolated_runtime.py tools/benchmark_file_switching.py tools/preview_reading_workspace.py tools/benchmark_library_search.py tools/benchmark_editor_writing.py tools/benchmark_text_loading.py tools/preview_pdf_reading_tools.py tools/verify_session_restart.py tests/test_isolated_runtime.py tests/test_final_lifecycle_review.py
```

輸出：`17 passed in 4.15s`、`Success: no issues found in 8 source files`、`All checks passed!`。將 QtCore class 的直接指定改成等價 `setattr` 以符合 mypy 後，另外重跑 helper 全部十案：`10 passed in 3.92s`。最後補上 PySide property kwargs 相容性後，重跑最終合併兩檔：`17 passed in 4.53s`，helper mypy／Ruff 再次通過。本輪所改工具與測試 `git diff --check` exit `0`，僅 Windows CRLF 提示。

Fresh agent `/root/reading_design_implementation` 未修改這批工具，獨立讀碼與重跑 constructor/store 測試，再以 `tmp/review_native_settings_guard.py` 包住真 CLI。Guard 對 HKCU/HKLM、32/64 位元 Registry view 的 `Software/markdown-viewer` 完整 prefix 作唯讀 digest，亦對既有 canonical/legacy 持久 JSON 與 recovery 檔案作 hash；不寫 sentinel、不列印設定內容。

| 獨立執行範圍 | 結果與證據 |
|---|---|
| `tmp/review_tool_isolation.py` | 12 個已載入 app aliases、六類 constructors、parent、scope/org/app 分離及五 stores contained，通過。 |
| `main.py --packaged-benchmark`，2 文件、4 warm repeats、10 KB | exit `0`；`tmp/review-isolated-main-switching.json` 為 `passed: true`，實際 `settings` 指向工具 temp/settings/user/hash/hash.ini。本 reviewer 已讀回。 |
| `preview_reading_workspace.py --mode office --theme dark` | exit `0`；`tmp/review-isolated-office/workspace-dark.png` 已由 fresh agent 實際檢視，畫面正常。 |
| `preview_pdf_reading_tools.py` | exit `0`；`tmp/review-isolated-pdf-tools/pdf-tools-smoke.json` 為 `status: passed`、來源 PDF 不變；雙主題、快照與搜尋可見。 |
| `verify_session_restart.py` | exit `0`；`tmp/review-isolated-session/session-restart-smoke.json` 為 `status: passed`、五個程序、來源 PDFs 與 native session 不變，含 abrupt/after-abrupt。 |
| 真正 render worker 子程序 tripwire | `tmp/review_worker_subprocess_guard.py` 透過 `main.py --render-worker`，將 QSettings constructor 換成直接拋錯；render_text/render_file/metadata_text/metadata_file 四種 remote requests 均成功、零 fallback。證明目前 worker 路線沒有讀寫設定，不依賴父程序 monkeypatch 繼承。 |

上述四個真 CLI 與 worker 的 native digest 前後均一致（`d62e29…19123`），persistent data hashes 亦相同，fresh 驗收通過。這只證明修後這些執行未更動既有資料；不抹除早期工具曾經觸及設定的事實。早期 Registry 備份、可識別測試路徑清理與無法推知的原偏好，由主代理另保留事故證據與說明。本 reviewer 未修改使用者 Registry。

主代理將 guard 正式納入 [verify_diagnostic_isolation.py](../../tools/verify_diagnostic_isolation.py) 後，本 reviewer 獨立讀取與實跑：

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tools/verify_diagnostic_isolation.py --output tmp/review-isolation-guard-noop.json -- .\tmp\upgrade-venv\Scripts\python.exe -X utf8 -c "pass"
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tools/verify_diagnostic_isolation.py --output tmp/review-isolation-guard-failure.json -- .\tmp\upgrade-venv\Scripts\python.exe -X utf8 -c "raise SystemExit(7)"
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check tools/verify_diagnostic_isolation.py
```

第一案 guard exit `0`、兩種 unchanged 均 true；第二案 child exit `7`、guard exit `1`，兩種 unchanged 仍 true，不因快照相同而掩蓋 child 失敗；Ruff 通過。另用 Python 建立 owned TemporaryDirectory，僅將該子程序的 `APPDATA` 指向此目錄，讓 child 改寫其中 synthetic `markdown-viewer/MarkdownViewer/sample.json`；guard 正確 exit `1`、`persistent_data_unchanged: false`、`native_unchanged: true`，證據為 `tmp/review-isolation-guard-detect.json`。此測試沒有修改真正 APPDATA。

Guard 可證明指定 Registry prefix 與 canonical/legacy JSON/recovery 的前後一致，不代表它追蹤任意外部使用者文件、WebEngine cache 或中途寫入又還原；隔離實作、worker tripwire 與前後快照是相互補充的證據。

## 左側欄寬度記憶補齊與 fresh 驗收

主代理最後範圍核對發現原計畫要求的側欄寬度記憶尚未接線，另授權本 reviewer 實作。[session_state.py](../../app/session_state.py) 新增 `SidebarWidthState`，以 `sidebar_width` 保留使用者實際拖曳的展開寬度；沒有既有設定或設定無效時使用原 280 px。顯示時依 LeftPanel 最小／最大尺寸與可用閱讀空間夾限，窄窗只影響當下顯示，不改寫使用者偏好。收合、專注隱藏、還原與重啟都沿用偏好，拖曳到零也不將零存為展開寬度。Detached 視窗不寫入主視窗的設定。

window 僅增加安裝與展開時套用兩個 hook；原閱讀／寫作／專注模式機制未更動。Layout timer 由 state QObject 擁有，state 由 MainWindow 擁有；Qt 銷毀視窗時一併清理。關窗只保存 preferred，不把 resize 暫時夾限的尺寸當成新的使用者設定。

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_sidebar_width_state.py tests/test_workspace_controls.py tests/test_session_restore.py -q -p no:cacheprovider
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check app/session_state.py app/window.py tests/test_sidebar_width_state.py
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m mypy
```

輸出 `30 passed in 8.99s`、`All checks passed!`、`Success: no issues found in 10 source files`。最後輸入型別收窄後另跑新增單檔：`11 passed in 2.16s`。mypy 數字限專案原設定的十模組，不擴稱整個 repository 都已靜態型別檢查。

Fresh reviewer `/root/writing_core_implementation` 未改這批程式，獨立讀取 SidebarWidthState／window hooks、Qt ownership 與設定隔離最新 property kwargs 修正，再執行：

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_sidebar_width_state.py tests/test_isolated_runtime.py -q --tb=short
```

輸出 `21 passed in 5.39s`，exit `0`，未發現已確認阻擋缺陷；側欄寬度與診斷隔離最新版本均通過獨立驗收。本 reviewer 此後停止 CPU 測試，保留主代理的閒置效能量測時段。

## 收尾獨立驗收：失效註記、面板釘選與減少動態

驗收者：`/root/plan_delivery_audit`，未實作這批產品改動。查核 `window.py`、`renderer.py` 的 orphan 查詢／導覽 callback、`annotations_panel.py`、`reading_tools.py` 及 `assets/annotations.js`。孤立註記訊號不直接作為當前結果，而是重新查詢當前 DOM、驗證 generation／page epoch，與當前 sidecar IDs 取交集。舊文件與舊 generation 回覆不更新新文件狀態。找不到原文時兩個列表明示狀態、原摘錄與筆記保留，導覽失敗顯示可理解訊息；減少動態偏好改用 `auto` 捲動。

右側釘選持久化設定，同時限制閱讀工具與屬性 dock 的拖動／浮出，保留關閉能力；取消釘選恢復操作。獨立讀碼與下列實跑未發現此範圍 blocker：

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_annotation_orphans.py tests/test_drafts_reading_tools.py -q -p no:cacheprovider
# 8 passed in 1.70s; exit 0
$env:RUN_WEBENGINE_TESTS='1'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_annotation_bridge.py -q -p no:cacheprovider -o faulthandler_timeout=25
# 11 passed in 11.73s; exit 0
```

第二組沒有 skip，包含真 Chromium 的可定位／無原文回覆及減少動態捲動。兩組不是整套產品回歸，也沒有驗證實體 Windows 動畫設定或讀屏。

## 收尾獨立驗收：來源選取字數

驗收者：`/root/plan_delivery_audit`，未實作選取計數。讀回 `EditorStatus.set_selection`／`clear_selection`／`_count_selection_slice` 及 MainWindow 的 `selectionChanged`／`textChanged` 掛接。來源選取使用既有中英混合單位契約，Markdown／code 原碼照字面計數，tooltip 明示語意；不與全文可見 Markdown 統計混稱。每片最多 4096 UTF-16 單位，避開內部 surrogate pair 邊界，保留兩字元處理跨片英文詞／連接符；舊 revision、已銷毀文件、Office 及 IME 組字狀態不發布隱藏來源的數量。

新增 [test_selection_count_review.py](../../tests/test_selection_count_review.py) 作獨立驗收：16 組 4094–4097 邊界、非零 UTF-16 起點、補充漢字／Emoji／單雙連字號，及 1 案實際 MainWindow 在長選取未完成時換文件。檢查選取文字、原文件未改與新文件計數，不只是對照內部旗標。

```powershell
$env:QT_QPA_PLATFORM='offscreen'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_selection_count_review.py tests/test_editor_status.py -k selection -q -p no:cacheprovider
# 21 passed, 30 deselected in 1.37s; exit 0
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check tests/test_selection_count_review.py
# All checks passed!; exit 0
```

未發現本範圍 blocker；本功能限定來源編輯，沒有宣稱 Office 原生選取數已提供，也不把上述功能測試或作者單次 1 MB probe 當作輸入 P95 達標。

## 收尾載入提示的驗證角色

`/root/plan_delivery_audit` 實作 Markdown renderer 的 140 ms 輕提示與 1000 ms 說明／開啟其他文件入口，主代理負責 MainWindow 開檔接線。開發者執行真 WebEngine `tests/test_renderer_switching.py` 得 `16 passed in 7.29s`，Ruff `All checks passed!`；測試涵蓋過渡顯示、動作 signal、切換重設、deactivate／完成清理及靜默 reload 不顯示。

另由 `/root/independent_core_review` 唯讀檢查 `arm` 重設、兩個 timer 啟停、generation guard、完成後不復活及實際開檔接線，未發現 blocker。為保留閒置效能環境，該 reviewer 未重跑 16 案，不能稱為第二次獨立測試通過。此變更不概括為 PDF／TXT／Office 都新增相同提示。

## 初始打包後的性能追查：空白頁預熱實驗（已撤回）

**目前狀態：此節與下方預熱候選的性能、局部測試及產物雜湊只保留為實驗歷史，預熱未納入最終交付。** 後續整組與獨立窄重現出現原生 AV，已撤回；詳見本報告最後的撤回紀錄。下列局部通過不抵銷該失敗。

初始候選的 100 KB／60 次暖切換資料中，每第 10 次導航的輪換都建立新 Chromium PID。候選的 6 次輪換首屏中位數 239.216 ms，其餘 54 次為 155.776 ms；同步開檔本身分別約 40.553／41.059 ms。退步主要發生在同步呼叫返回後，不能歸咎於隱藏屬性面板或 parser cache miss。清 history 並不能釋放原先逐導航成長的 Chromium 記憶體，因此沒有減少輪換頻率來掩蓋 P95。

主代理授權本 reviewer 先做 [獨立 tmp 探針](../../tmp/review_page_prewarm_probe.py)，保留原工具、100 KB 文件、2 文件／60 次暖切換、readiness 與第 10 次輪換，只在第 8 次導航後建立最多一個空白頁。兩組均 62／62 成功、全關分頁成功；外層 guard 的 native／persistent data 雜湊前後不變。

| 單次診斷組 | 首屏 P50／P95（ms） | RSS 峰值／全關後（MiB） |
|---|---:|---:|
| 同期未預熱對照 | 154.688／224.116 | 355.99／248.29 |
| tmp 預熱探針 | 146.611／187.284 | 409.88／250.65 |
| 正式 source 整合、原工具無 probe hook | 156.646／189.397 | 411.45／250.21 |

探針 6／6 預熱頁在下一次導航時 ready，PID 與當時的 active page 不同；6／6 promotion 後實際文件沿用預熱 PID，而舊 PID 已不在該輪 process snapshot。輪換本身的首屏中位數由 232.356 降到 185.459 ms，代價約 54 MiB 峰值記憶體。這是一次 A/B 診斷，不是多輪可信區間，也未達 150 ms 目標。原工具整合量測再確認每輪預熱時 2 個、輪換後 1 個 Chromium process；60 次暖切換的 RSS 首／尾為 288.23／295.49 MiB。以上 source 數字不冒充舊 EXE 包含此變更。

實作位於 [renderer.py](../../app/renderer.py)：空白頁沒有 channel／文件 scripts，在 promotion 才複製當下設定、字體、scripts 與 channel；仍每 10 次新 generation 導航輪換。未 ready 時直接走原建立路徑，不等待預熱。隱藏、關閉、真正 deactivate、空白／復原頁、非 Markdown 與列印開始均釋放 spare；同事件回合的 deactivate→新 Markdown 則保留這個不含文件內容的 spare。timer 與 page 都歸 renderer QObject 所有，late callback 檢查物件有效性與 spare 身分。

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_renderer_page_rotation.py tests/test_renderer_switching.py tests/test_renderer_source_navigation.py -q --tb=short
# 49 passed in 53.79s; exit 0
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m ruff check app/renderer.py tests/test_renderer_page_rotation.py tests/test_renderer_switching.py tests/test_renderer_source_navigation.py
# All checks passed!; exit 0
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tools/verify_diagnostic_isolation.py --output tmp/review-prewarm-product-guard.json -- .\tmp\upgrade-venv\Scripts\python.exe -X utf8 tools/benchmark_file_switching.py --output tmp/review-prewarm-product.json --size 100000 --repeats 30 --working-set 2 --label integrated-prewarm
# exit 0; passed/tabs_closed/native_unchanged/persistent_data_unchanged = true
```

證據保留於 `tmp/review-prewarm-{control,prewarm,product}.json`、探針兩組的 `-events.json`、三組 `-guard.json`，以及 `tmp/review-prewarm-tests.log`。另一組 renderer_async／html_loading／fast_preview／diagram_loading／diagram_loading_webengine／annotation_bridge 合跑為 `1 failed, 59 passed in 73.92s`：`test_stored_annotation_renders_mark` 的 JS 查詢 4 秒沒有回覆，得到 `None`，不是 annotation 數量錯誤。該隱藏 view 僅 2 次導航，預熱不會啟動；仍交 fresh reviewer 單案與整合定位，不先以 flaky 名義忽略或放寬斷言。此時產品已凍結待該項獨立驗收與後續重新打包。

### 預熱及搜尋輸入的追加獨立驗收

驗收者 `/root/plan_delivery_audit` 未實作本輪預熱或搜尋輸入修改。唯讀檢查 `_can_prepare_page_spare`／prepare／discard／rotation、same-turn deactivate 計時、hide／show／close／empty／recovery／non-Markdown、列印與 late callback。最多一個空白 spare、ready 才 promotion、未 ready 不等待、原 10 次界線、QObject parent 及失效身分防線一致；沒有發現已確認 blocker。

同時讀回 `global_search.py` 的 150 ms debounce 與 InputMethod event filter。組字時停止等待中的搜尋，Return 與方向鍵留給 Qt；commit 由 textChanged 重新排程最新文字，取消／FocusOut 恢復尚待搜尋的已提交文字；關閉清除組字狀態。`tests/test_search_input_composition.py` 涵蓋 commit、取消、最新 query、Enter 與清空，但不等於真實 Windows 候選窗驗證。

主代理明確解除暫停 CPU 測試的限制後，使用同一鎖版環境與上述三個 Qt／WebEngine 環境變數執行：

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_annotation_bridge.py::test_stored_annotation_renders_mark -q -p no:cacheprovider --tb=short
# 1 passed in 4.75s; exit 0
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 -m pytest tests/test_annotation_bridge.py tests/test_renderer_page_rotation.py tests/test_pdf_export_webengine.py tests/test_search_input_composition.py -q -p no:cacheprovider --tb=short
# 45 passed in 58.02s; exit 0
```

原始輸出為 `tmp/review-prewarm-annotation-single.log` 與 `tmp/review-prewarm-fresh.log`；沒有 skip、加大 timeout、修改測試或產品。45 案包含真 Chromium 的獨立 PID／原輪換界線、晚到回覆、刪除／隱藏／停用、列印不換頁、bridge／設定保留與 PDF 實際內容，另有搜尋組字三案。部分測試時間與 PyInstaller 建置重疊，因此不引用為性能量測。

先前單次 `None` 逾時在獨立單案及此組未重現，**根因尚未確認**；保留原 `1 failed, 59 passed`，不改稱已修復或已證實 flaky。主代理後續須完成最終普通／renderer 組及重建後性能，本次局部通過不替代它們。

## 預熱候選產物的獨立雜湊驗收（候選已撤回）

本 reviewer 未執行這次建置，依主代理要求重新讀取磁碟內容計算 SHA-256，沒有沿用主代理算出的值。當時驗收 [prewarm-build-manifest.json](evidence-20261022/prewarm-build-manifest.json) 所列全部 **617／617** 來源檔均相符，重新列出的 app／assets／tools 及六個頂層建置來源檔也沒有新增或遺漏。manifest 在該次驗收前後未變。下列兩個當時產物的大小與雜湊均吻合；明細保留每個來源 expected／actual hash，見 [prewarm-final-artifact-audit.json](evidence-20261022/prewarm-final-artifact-audit.json)。這項雜湊一致只證明候選身分，不代表產品可靠性，已被後續 native AV 阻擋交付。

| 產物 | Bytes | 獨立重算 SHA-256 |
|---|---:|---|
| `dist/upgrade-20261022/MarkdownViewer/MarkdownViewer.exe` | 9,756,789 | `075cf30922ddb0b396e3289cee011834e0ff42bdc8fad11067a10b0fc413adaa` |
| `installer_output/upgrade-20261022/MarkdownViewer_Setup_v1.36.0.exe` | 166,159,331 | `b8384e1afde75b833edd54a5f97e5489d5e03c640ac384742be6865c98891241` |

讀回 [prewarm-pyinstaller.log](evidence-20261022/prewarm-pyinstaller.log) 末尾包含 `COLLECT ... completed successfully` 與 `Build complete!`；[prewarm-installer.log](evidence-20261022/prewarm-installer.log) 包含 `Successful compile (215.641 sec)` 及同一安裝檔路徑。主代理的 terminal session `52609` 回報 driver exit `0`，stdout 明列 PyInstaller／Inno Setup 各 exit `0`；該 stdout 未另外落檔，因此 audit 將 exit code 標為 **root terminal result 的回報**，不冒稱 reviewer 自行重跑。另讀回 `tmp/build_upgrade_delivery.py`，確認任何子程序非零就停止，只有兩者成功且建置前後 source hash 相符才寫 manifest。

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tmp/review_final_artifact_audit.py
# exit 0
# passed=true; source_count=617; source_matches=617
# source_inventory_matches=true; artifact_matches=[true,true]
# build_log_success=[true,true]
```

這項驗收當時確認 **1.36.0 未提交、未發布預熱候選**的產物與凍結來源一致，沒有執行安裝程式。該候選已撤回，表中兩個雜湊不可用作撤回後重建的交付檔案身分；初始候選的 manifest 與 log 另存於 `initial-*`。

## 空白頁預熱的阻擋缺陷與精確撤回

主代理的 12 檔真 Chromium 組在 138 個完成標記後原生 access violation，堆疊指向 `test_unready_spare_never_delays_navigation` 的 `_evaluate → QEventLoop.exec()`，見 [原始整組日誌](evidence-20261022/prewarm-native-crash.log)。本 reviewer 只選此測試，以同一 process 要求重複 30 次；在第一案完成前就重現相同 native stack，子程序退出 **3221225477（0xC0000005）**，見 [最小重現日誌](evidence-20261022/prewarm-native-crash-minimal.log) 與 [退出碼紀錄](evidence-20261022/prewarm-native-crash-minimal.json)。

```powershell
$env:RUN_WEBENGINE_TESTS='1'
$env:QT_QPA_PLATFORM='offscreen'
$env:QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu'
# Execute the same selected node 30 times in one pytest process:
# python -X utf8 -m pytest --keep-duplicates -q --tb=short <node> ... <node>
# node = tests/test_renderer_page_rotation.py::test_unready_spare_never_delays_navigation
# Child process exit 3221225477; no repetition completed.
```

可確認的是「未完成空白 spare 的 Stop／deleteLater、建立 replacement、舊 active page 退役、立即導航並處理事件」這條路徑可觸發 native 崩潰；Python stack 無法證明底層 Chromium 的確切 use-after-free 位置。因此不疊加等待、skip 或另一層原生生命週期 workaround。主代理選擇撤回這個未達 150 ms、僅改善部分尾延遲的實驗。

`app/renderer.py` 已從預熱前逐位元副本恢復，SHA-256 **`0e42380d638a07ec66bba3d6e6f42a8fb7b6a6fa205b364fac3f2c90a0b44b9d`** 與 `initial-build-manifest.json` 完全相符；保留原每 10 次新 generation 導航輪換、設定／字體／scripts／channel 重綁、列印排隊、loading shield、wiki route 及先前完整功能。預熱 timer、spare 與相關 hooks 全數撤回；沒有回退 root 後續的搜尋輸入改善。

原有 15 個 page rotation 測試及斷言恢復；預熱專屬案例隨功能撤回，不是 skip 失敗，原版產品／案例另存 `tmp/review-rejected-prewarm-renderer.py` 與 `tmp/review-rejected-prewarm-tests.py` 供重現。新增一個無 spare 的 `test_immediate_navigation_after_rotation_remains_usable`，連續 10 次退役舊頁後立即導航／JS 查詢並確認舊頁已刪除；兩個 switching／source-navigation fixture 也撤回預熱欄位。此刻已重新凍結，該新增案與整組結果由主代理後續回歸確認，不能沿用撤回前的 49／45 passed 作最終證據。

## 撤回後最終產物的獨立驗收

本 reviewer 於 `2026-10-22T16:53:01Z` 再次逐檔讀取並計算 SHA-256，確認 [正式 build-manifest.json](evidence-20261022/build-manifest.json) 的 **617／617** 來源全部相符、完整來源清單沒有新增或遺漏、兩個實際產物大小與 hash 全部一致。Renderer hash 仍為上節的 `0e42380d…0b44b9d`，確認這次來源已撤回空白頁預熱。正式 [final-artifact-audit.json](evidence-20261022/final-artifact-audit.json) 已更新為此輪；預熱候選的 audit 保留於 `prewarm-final-artifact-audit.json`，兩者沒有混用。

| 撤回後最終產物 | Bytes | 獨立重算 SHA-256 |
|---|---:|---|
| `dist/upgrade-20261022/MarkdownViewer/MarkdownViewer.exe` | 9,754,561 | `244e834c5734194d68682290560dcaeaafb9c3c7f05c672852b7d88be0b9fef6` |
| `installer_output/upgrade-20261022/MarkdownViewer_Setup_v1.36.0.exe` | 166,129,564 | `51325e5c1959c0d401e28c7526ff706494b8758d3d131f6ccaf611a0937339d1` |

```powershell
.\tmp\upgrade-venv\Scripts\python.exe -X utf8 tmp/review_final_artifact_audit.py
# exit 0; passed=true
# source_count=617; source_matches=617; source_inventory_matches=true
# artifact_matches=[true,true]; build_log_success=[true,true]
```

本次 manifest SHA-256 為 `984f7eb4628df715c0ff95282340cd9c2a5333d6fdc29b95fbebf073117a860d`，建置時間 `2026-10-22T16:52:09Z`，在驗收期間未變。[PyInstaller 正式 log](evidence-20261022/pyinstaller.log) 有 `Build complete!`，[Inno Setup 正式 log](evidence-20261022/installer.log) 有 `Successful compile (195.000 sec)`。主代理 terminal session `78918` 的 driver、PyInstaller、Inno Setup 均回報 exit `0`；audit 如實將此標記為 root terminal 結果，不宣稱 reviewer 重跑建置。

獨立 hash 工作約 1.35 秒，之後停止 hash／CPU 測試與性能工作，只讀回文件，留給主代理最終整組與閒置性能驗證。這項產物身分驗收未執行安裝程式，不等於宣告全部性能目標達成或已發布版本。

## 撤回後整組結果與來源範圍的追加讀回

驗收者 `/root/plan_delivery_audit` 未修改撤回程式，獨立唯讀核對 renderer／rotation／switching／source-navigation：spare 及對應 timer hooks 均消失，原每十次輪換、設定／scripts／channel 重綁、列印延後導航、慢載提示、orphan 與 HTML 背景準備保留。renderer 的實際 SHA-256 與初始 manifest 相同，已在撤回時重算確認；新十次立即導覽 regression 沒有 spare，未以 skip 方式留下失敗實驗。

主代理隨後執行完整回歸；本 reviewer 讀回 [pytest.log](evidence-20261022/pytest.log) 的 `2160 passed, 143 skipped, 2 warnings in 121.86s`，及 [renderer.log](evidence-20261022/renderer.log) 的 `131 passed in 259.77s`。命令與 exit `0` 來源為 [final-execution-summary.json](evidence-20261022/final-execution-summary.json)：普通組 `python -X utf8 -m pytest tests/ -x -q --tb=short`，renderer 使用原 12 檔清單與 `-q --tb=short`、`RUN_WEBENGINE_TESTS=1`；鎖版 Python 3.13.5／PySide6 6.11.2、offscreen／`--disable-gpu`。143 skips 不算成功；兩 warning 是外來暫存檔保留的預期保護。這是主代理實跑、本 reviewer 原始 log 讀回，沒有宣稱 reviewer 再跑整套。

最終 131 案包含無 spare 的十次立即導覽，且沒有 native crash；撤回與自動回歸範圍完成。先前預熱版的 access violation 和較早註記 JS 逾時仍保留，沒有回填成通過，也不將已移除實驗路徑描述為目前仍存在的產品缺陷。

另直接解析 [initial-build-manifest.json](evidence-20261022/initial-build-manifest.json) 與 [build-manifest.json](evidence-20261022/build-manifest.json)：來源各 617 項，新增／刪除皆空，只有 `app/global_search.py` 與 `tools/benchmark_library_search.py` 雜湊不同，其餘 615 項完全一致。初始 MD／PDF／編輯器／TXT 量測的相關來源未改，保留其原時間與工作負載證據；搜尋 150 ms、重建 EXE 工作集與 IPC 另重測，不冒稱所有量測都在最終 build 重跑。測試檔不在產物 manifest，故測試版本不能只靠這份來源差異推論，而以最終 pytest 結果另記。

本 reviewer 已讀回上節 core reviewer 的 617／617 及兩產物獨立 hash 稽核、PyInstaller／Inno 成功 marker 與 [final-static-checks.log](evidence-20261022/final-static-checks.log) 的 Ruff／mypy 十模組／pip check／Node harness 各 exit `0`。為保留閒置性能條件，本次不再重算產物或執行測試。實體 IME、DPI、實際安裝及五人任務仍不在自動回歸證明範圍。

### 最後三組診斷的原始結果讀回

`/root/plan_delivery_audit` 另直接解析 [final-benchmark-commands.json](evidence-20261022/final-benchmark-commands.json) 與三份 `final-*` 原始結果。主代理於建置／測試停止後依序執行，三命令 exit `0`；三份 `final-*-guard.json` 均為 native／persistent data unchanged。本 reviewer 沒有重跑 benchmark。

- [final-search-10000.json](evidence-20261022/final-search-10000.json)：萬檔 4,923,400 bytes，英文／繁中單字／雙字各 30 次。150 ms debounce 後端到端首列 P95 173.341／173.083／171.775 ms，均符合 200 ms；立即派送 P95 31.697／19.852／24.288 ms。含 debounce 的完整結果 P95 631.022／561.013／535.290 ms，不與首列混稱。1,236 個 GUI batch P95 2.469 ms，但最大 **8.195 ms**，不能聲稱每批都 ≤8 ms。
- [final-packaged-100-switches.json](evidence-20261022/final-packaged-100-switches.json)：固定十份 101,168 bytes MD、暖身後 100 次切換並在最後全關。ready P95 **226.375 ms**，仍超過 150 ms；程序樹 RSS 首末 −1.295%、首尾十次中位數 +2.788%，指定工作集符合 ≤10%。單次 launch 4,177.729 ms 含語料建立且不含首文件畫面，不是已安裝版暖／冷啟動 P95。
- [final-packaged-ipc.json](evidence-20261022/final-packaged-ipc.json)：第二實例轉送 305.256 ms、路徑一致、正常 exit `0`；不包含實際安裝／系統檔案關聯或主視窗首屏。

其餘初始性能來源與最終 manifest 一致，原 source 100 KB 233.871 ms、1 MB 首屏 662.361 ms、PDF gap 60.026 ms 的未達標仍保留；TXT 百萬字元繁中單行最大 UI gap 約 3.864 s。預熱撤回、整組回歸與產物身分已結案，不能據此改稱全部性能或實體驗收達成。

## 最後交付文件的 fresh 核對結論

驗收者 `/root/plan_delivery_audit` 在產品凍結後完整讀取[實作與驗收報告](2026-10-22-implementation.md)，與[逐項核對](2026-10-22-plan-acceptance.md)、原始 log／JSON、兩份 manifest 及獨立 artifact audit 交叉核對。最終 2160／131／Office 27／命令貼上 22 的範圍與環境、三組診斷數字、兩產物大小及 SHA-256 均一致；沒有把重疊測試加總，也沒有把流程 exit 0 當作全部 KPI 通過。

受主代理授權，只修實作報告四處文案：診斷「成功執行」與 KPI 分開、GPU mailbox 訊息明確屬初始 130 案、頁面輪換描述符合導航計數／新渲染條件，以及「Qt 掛接文件」用詞。沒有修改產品或測試。全部相對檔案連結逐一檢查存在，含最終 EXE／installer；兩檔實際大小與 manifest 相符。實際完整 hash 已由 core reviewer 執行，本輪未冒稱再次重算或安裝。

可交付範圍是 **1.36.0 本機試用候選**：功能與指定自動回歸、來源／產物一致性、搜尋首列及指定 RSS 工作集有證據。原計畫仍未全面達標：MD／1 MB／PDF 的性能門檻、最大搜尋 batch 8.195 ms、極長文字停頓及實體 IME／DPI／安裝升級／五人任務保留明確限制。早期設定事故與未知原偏好無法保證還原、已撤回預熱 native 崩潰、未定因註記逾時均如實保留；沒有將撤回方案當成最終版仍存在的路徑。未發現交付報告與此批證據相矛盾的範圍聲稱，候選尚未提交或發布。

主代理隨後完成撤回後回歸，本 reviewer 直接讀回正式原始 log：[普通組](evidence-20261022/pytest.log) 為 `2160 passed, 143 skipped, 2 warnings in 121.86s`；[12 檔 Chromium 組](evidence-20261022/renderer.log) 為 `131 passed in 259.77s`，包含新增無 spare 的連續立即導航案，沒有原生崩潰。兩個 exit `0` 的 terminal 來源列於 [final-execution-summary.json](evidence-20261022/final-execution-summary.json)，普通組結果不是本 reviewer 另跑一次；兩則 warning 為測試故意保留外來搬移暫存的預期警告。已核對 implementation 表中的測試數、時長、產物大小／hash 與這些原始證據一致，性能結論仍等待主代理的最後閒置量測。
