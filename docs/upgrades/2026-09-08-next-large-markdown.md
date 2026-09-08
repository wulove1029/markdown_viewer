# 大型 Markdown：取消／工作排程與首次可閱讀內容（2026-09-08）

規格：`docs/CLAUDE-CODE-NEXT-OPTIMIZATIONS.md` 第 3 節 B／C／D。
基線：`docs/upgrades/2026-09-08-next-baseline.md`、`docs/benchmarks/first-load-baseline-2026-09-08.json`。
分支 `feature/large-markdown-render`，工作樹 `.claude/worktrees/batch3-large-markdown`（自 `main` = 26d17e0）。
本批未改首頁 HTML／`app/window.py`，以免與另一批未合併分支衝突。

## 1. 架構選擇

**大檔解析移到可強制終止的獨立 worker process**（`app/render_service.py` + `app/render_worker.py`），
小檔仍留在行內解析。理由：基線證實 `_CONVERT_LOCK` 在整段 `md.render()` 期間持有，
執行中的 parser 無法協作取消，5 MB 解析時開 100 KB 要等 p50 3.2 秒。

- IPC：loopback TCP + 32 bytes 隨機 token 握手 + 長度前綴 pickle frame。
  **不用 multiprocessing**：封裝版是 `console=False` 的 PyInstaller windowed 應用，
  spawn 會在子程序重新 import `main`，且 stdio pipe 在 windowed 模式不可靠；socket 可靠。
- 啟動方式：原始碼 `py -3 -X utf8 -m app.render_worker <port> <token>`；
  封裝 `MarkdownViewer.exe --render-worker <port> <token>`（`main.py` 於 `__main__` 最前面攔截，早於任何 Qt 工作）。
- 取消＝`proc.kill()`：真正停止 CPU 與 parser，不是丟棄 callback，也沒有用 `QThread.terminate()`。
- 池策略：最多保留 1 個閒置 worker；每 12 個請求回收重生（背景 prewarm），崩潰／逾時自動換新並退回行內解析。
- `atexit` 與 `RenderService.shutdown()` 收尾；子程序環境變數帶 `MDV_DISABLE_RENDER_SUBPROCESS=1`，不會產生孫程序。

**首次可閱讀內容＝「快速文字閱讀」前綴**：`FAST_PREVIEW_MIN_BYTES` 以上的文件先渲染一段
以區塊邊界切出的前綴（預設 512 KB），頁面頂端以 sticky 橫幅明講範圍（百分比、KB／MB、
「目錄／搜尋／行號定位只涵蓋已載入區塊」），完整預覽在 worker process 完成後自動接上。

門檻集中定義在 `app/md_converter.py`（`MAX_PREVIEW_BYTES`／`SUBPROCESS_MIN_BYTES` 256 KB／
`FAST_PREVIEW_MIN_BYTES` 2 MB／`FAST_PREVIEW_PREFIX_BYTES` 512 KB／`FAST_PREVIEW_MAX_SCAN_BYTES`），
數值直接來自基線（100 KB 32 ms、1 MB 292 ms 可留行內；5 MB 2.35 s 不可）。

## 2. 改了哪些檔

| 檔案 | 內容 |
| --- | --- |
| `app/render_service.py`（新增，約 300 行） | worker 池、握手、frame 收發、取消殺程序、逾時、崩潰換新、`prewarm()`／`shutdown()` |
| `app/render_worker.py`（新增） | 子程序服務迴圈；只 import markdown-it／Pygments，不 import Qt |
| `app/md_converter.py` | 門檻常數、`fast_preview_split()`、`partial_preview_notice_html()`、`render_partial_body()`、`wrap_body()`、`has_cached_body()`、`_remote_body()`；`_cached_body()` 大檔改走子程序；`convert()`／`convert_text()` 移除多餘的 `_CONVERT_LOCK` 包覆（`_wrap` 只讀模組字串） |
| `app/renderer.py` | `_RenderSignals.partial_ready`、`_MarkdownRenderWorker._emit_fast_preview()`、`RendererView._on_file_partial_ready()`／`_stale_render()`／`is_partial_preview()`、完整版接上時還原被部分頁吃掉的 pending find／scroll、部分預覽期間延後 PDF 匯出 |
| `main.py` | `--render-worker` argv 入口（封裝版路徑） |
| `markdown_viewer.spec` | `hiddenimports` 加 `app.render_service`／`app.render_worker` |
| `tools/benchmark_markdown_first_load.py` | **只新增**：`first_readable_once()`、`cancel_switch_once()`、`--first-readable-reps`／`--cancel-switch-reps`、JSON `first_readable`／`cancel_switch`；既有量測邏輯未動 |
| `tests/test_fast_preview.py`（新增，23 項） | 區塊邊界切割、巨型單一區塊退路、部分頁標示與前綴一致性、worker 兩段輸出、pending find/scroll 還原、匯出延後 |
| `tests/test_render_service.py`（新增，20 項） | 真子程序渲染一致性（含語法壓力文件）、重用／回收、取消殺程序、逾時、崩潰換新、關閉清理、行內回退、env 停用、封裝 argv 入口 smoke |

## 3. 驗收逐條（同機、同工具、同 seed；after JSON：`docs/benchmarks/large-markdown-after-2026-09-08.json`）

| 驗收條件 | 結果 | before → after |
| --- | --- | --- |
| 5 MB 載入回饋 200 ms 內 | **pass** | 載入提示在任何解析前同步 setHtml；背景解析期間 GUI 心跳最大間隔 108.3 → **69.4 ms**（10 MB：152.9 → 78.5 ms），皆 < 200 ms |
| 5 MB 可閱讀首屏 p50 ≤ 2 s 或降 50% | **pass（靠快速文字模式）** | 2345.7 ms（冷開全文）→ **228.2 ms**（p95 245.4；覆蓋文件前 10.5%）＝ −90% |
| ↳ 同一份 5 MB 的完整預覽時間（分開列，不混報） | 部分退步 | 同程序重開 3337.6 → **3156.8 ms**（−5.4%）；新程序冷開 2345.7 → **2487.1 ms**（+6.0%，子程序啟動成本） |
| 5 MB 解析中切到 100 KB：1 秒內、不等舊任務 | **pass** | 100 KB 請求延遲 3215.2 → **36.3 ms**（10 MB：8783.4 → 36.1 ms），與單獨開啟 34.9 ms 幾乎相同 |
| ↳ 舊任務真的停止（不只丟棄 callback） | **pass** | 新增 `cancel_switch`：取消後大檔呼叫 **44.6 ms**（10 MB 45.9 ms）內返回，`render_worker_processes_killed = 1`；小檔同時 41.7 ms 完成 |
| GUI 無超過 200 ms 長阻塞 | **pass** | 見上（69.4／78.5 ms） |
| 100 KB p50 不退步超過 10% | **pass** | 同程序重開 40.1 → **40.4 ms**（+0.7%）；新程序冷開 32.4 → **30.1 ms** |
| 連續開關 20 次記憶體回收 | **pass** | 主程序 RSS 峰值 220.7 → **169.3 MB**，成長 4.3 → **2.1 MB**；另量子程序：20 次 5 MB 後主程序 41.2 MB／存活子程序 1 個／子程序峰值 RSS 68.9 MB／共 spawn 3 個（每 12 次回收）／`shutdown()` 後子程序 0 |
| 語法壓力輸出與既有完整渲染一致 | **pass** | `test_child_output_is_identical_for_the_syntax_stress_document`、`test_stress_file_rendered_through_the_child_matches_in_process`（front matter／重複標題／fence／mermaid／表格／清單／callout／footnote／reference link／deflist／$$ 數學／details）逐字元相同 |
| 取消、關閉、切頁、變更主題、磁碟內容改變無過期回寫 | **pass（自動測試層級）** | generation／path 檢查沿用並擴充到部分頁（`test_stale_partial_view_never_touches_the_page`）；`_body_signature` 前後比對未變；主題只影響 `_wrap`，body 快取與主題無關 |
| 保留 1.31.0 搜尋來源區塊定位與 pending recovery | **pass** | `tests/test_renderer_source_navigation.py` 全過（含 `RUN_WEBENGINE_TESTS=1`）；部分頁會把被吃掉的 pending find／scroll 還原給完整頁，`show_pending_recovery` 未改 |
| 全套 `pytest tests` exit 0 | **pass** | 1542 passed／75 skipped（含本批新增 43 項，以及自基線批次帶入的量測工具自我測試 6 項） |

## 4. 未做／未驗（不默默降標）

1. **封裝版未實跑**：本機未打包。已用 `test_frozen_style_argv_entry_point_serves_a_render`
   走 `main.py --render-worker <port> <token>`（＝封裝 bootloader 會執行的同一條 argv）證明入口可用，
   並在 `.spec` 補 hiddenimports。仍待：`py -3 -m PyInstaller markdown_viewer.spec` 後開 5 MB 文件，
   確認 `MarkdownViewer.exe --render-worker` 子程序有起來（工作管理員可見同名第二個程序）且無主控台視窗。
2. **實體視窗首屏未驗**：所有數字為 `QT_QPA_PLATFORM=offscreen`，且量的是 `convert()`／部分渲染的
   Python 端時間，不是 Chromium 真實首繪。需要實體 Windows 桌面開 5 MB 文件目視確認橫幅與接續。
3. **語法壓力案例的 after 效能未重跑**：只跑 `long_prose`（與基線的完整量測範圍一致）。
   `code_heavy` 等仍只有基線的少次數數據。
4. **編輯模式即時預覽仍在行內解析**：`convert_text()` 未走子程序，5 MB 未存檔緩衝區的即時預覽仍會佔用 parser。
5. **只有兩段（前綴 → 全文），沒有多段漸進渲染**；前綴之後的 reference link／footnote 定義會在
   部分頁以字面文字呈現（橫幅已說明範圍）。
6. **部分頁的搜尋／TOC 只涵蓋前綴**：完整頁載入後會自動重跑被延後的搜尋與捲動還原；
   但「使用者在部分頁手動搜尋且該詞只出現在後段」目前只會找不到，沒有主動提示「請等完整預覽」。
7. 快速模式沒有做成使用者可切換的開關（門檻自動判斷）；規格允許的「明確兩選項」目前是
   「自動顯示部分內容 + 明講範圍 + 自動接上完整版」。

## 5. 意外發現

1. **基線工具的 `small_blocked_until_big_finished_fraction` 這個旗標不可信**：
   `parser_lock_contention_once()` 在 `thread.join()` **之後**才讀 `big_done.is_set()`，
   所以 `big_still_running_when_small_finished` 永遠是 `False`，該比例永遠是 1.0——
   before／after 都一樣。真正的證據是 `small_request_latency_ms`（3215 ms → 36 ms）。
   依交接指示未修改既有量測邏輯，僅在此註記。
2. **1 MB 同程序重開反而變快**：399.8 → 289.7 ms（−27.5%）。1 MB 已超過 256 KB 門檻走子程序，
   子程序沒有 GIL 競爭也沒有主程序的量測 hook；意味著門檻可再往下探，但本批不調（100 KB 必須零退步）。
3. 子程序冷啟動只要 ~205 ms（子程序不 import Qt），比預期便宜很多；因此 `prewarm()` 只在
   開啟 ≥256 KB 文件時才觸發，一般使用者不會多背一個常駐程序。

## 6. 指令與證據

```powershell
py -3 -X utf8 -m pytest tests -q -p no:cacheprovider --basetemp tmp/next-render-tests
# 1542 passed, 75 skipped, exit 0
$env:RUN_WEBENGINE_TESTS = '1'
py -3 -X utf8 -m pytest tests/test_renderer_async.py tests/test_renderer_source_navigation.py `
    tests/test_fast_preview.py tests/test_render_service.py -q -p no:cacheprovider `
    --basetemp tmp/next-render-webengine   # 49 passed
Remove-Item Env:RUN_WEBENGINE_TESTS
py -3 -X utf8 tools/benchmark_markdown_first_load.py --categories long_prose `
    --sizes 100kb,1mb,5mb,10mb --warm-reps 10 --switch-reps 5 --contention-reps 3 `
    --gui-reps 3 --webengine-reps 3 --memory-iterations 20 --first-readable-reps 5 `
    --cancel-switch-reps 3 --cold-timeout-s 60 `
    --output docs/benchmarks/large-markdown-after-2026-09-08.json   # exit 0
```

環境：Windows 11、Python 3.14.4、PySide6/Qt 6.11.0、i9-13900H、`QT_QPA_PLATFORM=offscreen`、未封裝，
與基線同機同設定；WebEngine 區段的數字沿用基線註記的不可信結論，未拿來當驗收依據。
