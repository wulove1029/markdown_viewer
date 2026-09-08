# 大型 Markdown 首次載入 — 效能基線（2026-09-08）

規格：`docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md` 第 3 節 A/B 段。工具：`tools/benchmark_markdown_first_load.py`（新增，不改 `app/`）。
自我測試：`tests/test_benchmark_markdown_first_load.py`（6 項全過）。
輸出：`docs/benchmarks/first-load-baseline-2026-09-08.json`。

## 環境

Windows 11、Python 3.14.4、PySide6/Qt 6.11.0、Intel Core i9-13900H（20 邏輯核）。
`QT_QPA_PLATFORM=offscreen`；未封裝（PyInstaller 未涉及）。固定 seed `20260908`，5 種內容型態
（`long_prose`／`code_heavy`／`big_table`／`math_mermaid`／`long_paragraph`）× 4 種大小（100KB/1MB/5MB/10MB）。

## 測試範圍與已知限制（自查，未隱藏縮水）

- **完整量測**（cold／warm／switch／parser-lock contention／GUI 心跳／WebEngine／記憶體）只對
  `long_prose`（標準長文，對應驗收 D 段的「標準 5MB 長文」）跑滿：warm 10 次（5MB 降 5 次、10MB 降 3 次，
  10MB 單次 8-11 秒 > 60 秒門檻，依規格允許降次數）。
- 其餘 4 種語法壓力案例（`code_heavy`／`big_table`／`math_mermaid`／`long_paragraph`）因單機時間預算，
  只跑 cold subprocess（各 1 次）與 warm reopen（各 3 次，非 10 次）及 sequential switch，
  **未跑** GUI 心跳／WebEngine／parser-lock contention／記憶體成長測試。原因：`code_heavy` 案例的
  Pygments 逐 code fence 呼叫成本很高（5MB ≈ 36 秒、10MB ≈ 87 秒/次），若比照 long_prose 跑滿會讓
  單次執行超過可控時間；已在 JSON `limitations` 欄位記錄。下一批若要對這些案例做決策，需要
  補跑（工具已支援 `--categories code_heavy --warm-reps N`）。
- Cold subprocess 可能命中本次跑量測時剛寫入的 OS 檔案快取，不是真冷碟。
- `QT_QPA_PLATFORM=offscreen`：WebEngine 數字非真實視窗首繪，且觀察到「引擎冷啟動」偏差（見意外發現）。
- `body_render_ms` 把 markdown-it parsing 與 Pygments/anchor HTML 生成混在一起量測，因為
  `app/md_converter.py` 沒有內部分段時間點（見下方「下一批需要的 hook」）。

## 關鍵數字（long_prose，convert() 總時間，ms）

| 案例 | p50 | p95 | n |
| --- | --- | --- | --- |
| 100KB 冷開（新程序） | 32.4 | — | 1 |
| 1MB 冷開（新程序） | 292.1 | — | 1 |
| 5MB 冷開（新程序） | 2345.7 | — | 1 |
| 10MB 冷開（新程序） | 8097.1 | — | 1 |
| 100KB 同程序重開 | 40.1 | 56.6 | 10 |
| 1MB 同程序重開 | 399.8 | 456.2 | 10 |
| 5MB 同程序重開 | 3337.6 | 3937.7 | 5 |
| 10MB 同程序重開 | 10274.9 | 11224.4 | 3 |
| 5MB→100KB 循序切換（前一份已轉完，僅量小檔） | 70.1 | 86.5 | 3 |
| 10MB→100KB 循序切換 | 50.0 | 62.7 | 3 |
| 100KB 單獨開啟（基準值，無干擾） | 26.1 | 28.1 | 5 |
| **5MB 解析進行中，同時請求 100KB（實際搶佔）** | **3215.2** | 3241.7 | 3 |
| **10MB 解析進行中，同時請求 100KB** | **8783.4** | 11038.3 | 3 |
| GUI 心跳最大間隔（5MB 背景轉換期間，tick=50ms） | 108.3 | 111.5 (max) | 3 |
| GUI 心跳最大間隔（10MB） | 152.9 | 158.0 (max) | 3 |
| 峰值記憶體 RSS（5MB，連續開關 20 次） | 220.7 MB（最大）／成長 4.3 MB | — | 20 |

冷開 5MB 20.09 秒（舊 `upgrade-after.json`，不同合成內容）與本次 2.35 秒（本次 `long_prose` 生成器）
**不可直接比較**——內容結構不同，不是同一份「1.31.0 重新量測」，僅供參考不當退步依據。

## Parser lock 阻塞結論

`app/md_converter.py` 的 `_CONVERT_LOCK`（`threading.RLock`）在 `render_body()` 整個解析＋錨點注入期間持有。
實測：背景執行緒正在轉換 5MB／10MB 文件時，從主執行緒立即請求一份不相關的 100KB 文件，
**3 次重複裡 100% 都要等到大檔完全轉換完才拿到結果**（`small_blocked_until_big_finished_fraction = 1.0`），
延遲從單獨開啟的 26–46ms 暴增到 p50 3.2 秒（5MB）／8.8 秒（10MB）。**結論：現行架構下，執行中的
parser 呼叫會完全阻塞任何新工作，取消事件（`cancel`）只在鎖之外或鎖內起始處被檢查，
無法中止已進入 `md.render()` 的呼叫**——這與規格 3.B 的假設一致，證實非隔離 worker 無法達成
「最新文件優先、不等待舊任務」的驗收目標。

## 建議下一批架構

單一 `threading.RLock` + `QThreadPool` 協作式取消對「大檔切小檔」場景不可行；建議把 Markdown 轉換移到
**獨立 worker process**（而非僅背景 thread），用可強制終止的 subprocess 取代 in-process 鎖，
配合 IPC 與逾時，才能讓新請求不等舊解析釋放。

## 意外發現

1. WebEngine `load_finished`/`dom_visible` 數字出現「反直覺」現象：100KB p50 386.9ms／1MB p50 1528.4ms
   反而比 5MB（45.8ms）、10MB（91.5ms）慢很多。判斷是量測順序造成的 Chromium render process
   冷啟動一次性成本被算進第一個跑到的案例（100KB 最先跑），而非真實隨檔案大小變化——
   **這批 WebEngine 數字不可信任，需要各案例獨立新程序量測後才能用**（已記錄於 JSON limitations）。
2. GUI 心跳最大間隔在 5MB／10MB 背景轉換下分別只有 108ms／153ms（心跳 tick=50ms），
   都在規格 D 段「無超過 200ms 長阻塞」門檻內——現行 `QThreadPool` + GIL 對 GUI 回應性的影響
   比預期小；真正的問題是 parser lock 讓「新工作等待」，不是 GUI 卡頓本身。
3. `code_heavy` 生成器一度產生 10 萬+ 個獨立 code fence 造成 5MB 案例耗時遠超所有其他案例，
   已調整生成器改用少量、內容更長的 fence（見程式內註解），10MB 仍要 ~87 秒/次——真實世界
   極端多 code fence 文件的 Pygments 逐塊呼叫開銷本身就是量測到的真實現象，不是量測工具的 bug。

## 下一批需要的 hook（供批次 3 參考）

- `render_body()` 目前把 markdown-it parsing 與 Pygments/anchor 注入合在一次呼叫；建議拆成
  `parse()` 與 `finalize_html()` 兩段，才能精確量測「首次可讀」與「完整就緒」的差距。
- 需要一個可從外部訂閱的「解析開始／取消生效」訊號，而非只有 `cancel.is_set()` 輪詢，
  才能驗證新架構是否真正提早中止舊工作。

## 指令與證據

```powershell
py -3 -X utf8 tools/benchmark_markdown_first_load.py --help   # exit 0
py -3 -X utf8 tools/benchmark_markdown_first_load.py --quick --output tmp/bench/quick-test.json  # exit 0, ~17s
py -3 -X utf8 -m pytest tests/test_benchmark_markdown_first_load.py -q -p no:cacheprovider --basetemp tmp/bench-tool-tests  # 6 passed
py -3 -X utf8 tools/benchmark_markdown_first_load.py --categories long_prose --sizes 100kb,1mb,5mb,10mb \
  --warm-reps 10 --switch-reps 5 --contention-reps 3 --gui-reps 3 --webengine-reps 3 --memory-iterations 20 \
  --cold-timeout-s 60 --output docs/benchmarks/first-load-baseline-2026-09-08.json  # exit 0
py -3 -X utf8 tools/benchmark_markdown_first_load.py --categories code_heavy,big_table,math_mermaid,long_paragraph \
  --sizes 100kb,1mb,5mb,10mb --warm-reps 3 --skip-webengine --skip-gui --skip-memory --cold-timeout-s 150 \
  --output tmp/bench/stress-categories.json  # exit 0, 合併進最終 JSON 的 cold_subprocess/warm_reopen/sequential_switch
```

`git status`：本批只新增/修改 `tools/benchmark_markdown_first_load.py`、
`tests/test_benchmark_markdown_first_load.py`、`docs/benchmarks/first-load-baseline-2026-09-08.json`、
本文件；未動 `app/` 任何檔案（主樹另有其他批次未 commit 的 `app/` 改動，與本批無關，未觸碰）。

## Fresh review 後修正（2026-09-08）

- 預設 `--output` 改為時間戳檔名，裸執行不再覆蓋已提交的基線 JSON。
- `summarize()` 加 `p95_note`：n < 20 時最近秩 p95 等於 max，只作初步指標。
- review 指出 `small_blocked_until_big_finished_fraction` 幾乎恆為 1.0（量測在 join 之後讀取），不具區分力；定性結論仍由「小檔延遲 ≈ 大檔全程」支持。`warm_reopen_once` 未丟棄第一次暖機樣本，可能混入 Pygments 延遲載入成本。兩者留待下一輪量測工具修正。
- 獨立重跑：solo 100 KB 32.3 ms；5 MB 進行中請求 100 KB 2513 ms（≈ 5 MB 全程 2509 ms）。報告：`2026-09-08-next-baseline-review.md`。
